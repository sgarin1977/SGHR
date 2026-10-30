"""Atomic pending operator-link issuance with secret-free optional keyed replay.

Same key/request returns the original issuance receipt, even after expiry or
consume; it never resends, refreshes TTL or reactivates the request. Configured
Platform encryption key must stay stable throughout the shared replay window.
Public routes, key requiredness and delivery transport are separate adapters.
"""
from datetime import datetime,timezone
import hashlib
import hmac
from uuid import UUID

from database.construction import set_construction_tenant_context
from database.repositories.construction.construction_client_link_requests import ConstructionClientLinkRequestState
from database.repositories.construction.construction_client_user_links import MAX_CLIENT_ROW_VERSION
from services.api_identity import ApiActorContext,ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyReservation,ApiIdempotencyKeyReusedError,build_api_idempotency_service,hash_request_payload
from services.construction.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction.construction_client_link_issuance_replay import ConstructionClientLinkTokenDeriver
from services.construction.construction_client_link_request_issuance import (
    CLIENT_LINK_CONFIRMATION_TTL,ConstructionClientLinkRequestIssuanceService,ConstructionClientLinkIssuanceResult,
    hash_client_link_confirmation_token,build_construction_client_link_request_issuance_service,
)
from services.construction.construction_clients import ConstructionClientNotFoundError,ConstructionClientValidationError,ConstructionClientOperationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext,ConstructionPermissionDeniedError,ConstructionScopeDeniedError,require_construction_access,
)

from database.repositories.construction.construction_client_link_email import (
    ConstructionClientLinkEmailRepository,
)
from database.repositories.construction.construction_client_user_links import (
    ConstructionClientUserLinkState,
)
from services.construction.construction_client_link_email import (
    ClientLinkPlatformUserNotFoundError, normalize_client_link_email,
)

ISSUE='construction.client_link.issue'


def _error():return ConstructionClientOperationError('Client link issuance workflow state is invalid.')


def _uuid(value):
    if not isinstance(value,str):raise _error()
    try:identifier=UUID(value)
    except (ValueError,TypeError,AttributeError):raise _error() from None
    if str(identifier)!=value:raise _error()
    return identifier


def _time(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise _error()
    return value.astimezone(timezone.utc)


class ConstructionClientLinkIssuanceWorkflow:
    def __init__(self, *, session, issuance_service, idempotency_service, token_deriver, now_provider=None, target_email_reader=None, email_delivery_queue=None):
        if (not isinstance(issuance_service,ConstructionClientLinkRequestIssuanceService) or issuance_service.session is not session
                or getattr(getattr(idempotency_service,'repository',None),'session',None) is not session
                or not callable(getattr(getattr(idempotency_service,'repository',None),'reserve_record',None))
                or not callable(getattr(getattr(idempotency_service,'response_codec',None),'decrypt',None))
                or not callable(getattr(issuance_service.requests,'get_request_for_update',None))
                or not all(callable(getattr(session,n,None)) for n in ('execute','in_transaction','commit','rollback'))
                or not isinstance(token_deriver,ConstructionClientLinkTokenDeriver)
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Issuance workflow requires shared writes, encrypted atomic idempotency and a purpose-specific token deriver.')
        self.session,self.service,self.idempotency,self.deriver=session,issuance_service,idempotency_service,token_deriver
        if target_email_reader is not None and (
                getattr(target_email_reader, "session", None)
                is not getattr(issuance_service.identity, "session", None)
                or getattr(target_email_reader, "session", None) is session
                or not callable(
                    getattr(target_email_reader, "find_verified_user_id", None)
                )):
            raise ValueError("Email selection requires the canonical Platform read session.")
        self.target_email_reader = target_email_reader
        if email_delivery_queue is not None and (
                target_email_reader is None
                or getattr(email_delivery_queue, "session", None) is not session
                or not callable(getattr(email_delivery_queue, "enqueue", None))
                or not callable(
                    getattr(email_delivery_queue, "validate_receipt", None)
                )):
            raise ValueError("Email delivery requires a shared-session queue.")
        self.email_delivery_queue = email_delivery_queue
        self.now_provider=now_provider or (lambda:datetime.now(timezone.utc))
        self.resolver=ConstructionAccessContextResolver(repository=issuance_service.access,now_provider=self._now)

    def _now(self):return _time(self.now_provider())

    @staticmethod
    def _scope(actor,context):
        if (not isinstance(actor,ApiActorContext) or actor.status!='active' or not isinstance(actor.user_id,UUID)
                or not isinstance(actor.tenant_id,UUID) or not isinstance(context,ConstructionAccessContext)):
            raise ConstructionPermissionDeniedError('Client edit authorization is not available.')
        require_construction_access(actor=actor,access_context=context,permission_code='construction.projects.edit')

    async def _authorize(self,actor):
        current=await self.service.identity.require_active_actor(actor=actor)
        if not isinstance(current,ApiActorContext) or current.user_id!=actor.user_id or current.tenant_id!=actor.tenant_id or current.status!='active':
            raise ApiIdentityAccessError('Authenticated Platform initiator is not available.')
        if await self.service.access.has_active_platform_membership(user_id=actor.user_id,tenant_id=actor.tenant_id) is not True:
            raise ConstructionPermissionDeniedError('Client edit membership is not available.')
        self._scope(actor,await self.resolver.resolve(actor=actor))

    def _record(self,row,binding):
        if (not isinstance(getattr(row,'id',None),UUID) or getattr(row,'tenant_id',None)!=_uuid(binding['tenant_id'])
                or getattr(row,'principal_id',None)!=_uuid(binding['principal_id'])
                or any(getattr(row,n,None)!=binding[n] for n in ('principal_type','operation','key_hash','request_hash'))
                or _time(getattr(row,'created_at',None)).isoformat()!=binding['created_at']
                or _time(row.created_at)>self._now()
                or _time(getattr(row,'expires_at',None))<=self._now()):raise _error()

    @staticmethod
    def _snapshot(material):
        state=material.request
        data={n:(v.isoformat() if isinstance(v,datetime) else str(v) if isinstance(v,UUID) else v)
            for n in state.__dataclass_fields__ if n!='token_hash' for v in (getattr(state,n),)}
        return dict(request=data,audit_id=str(material.audit_id))

    @staticmethod
    def _material(snapshot,token):
        if not isinstance(snapshot,dict) or set(snapshot)!={'request','audit_id'}:raise _error()
        data=snapshot['request']
        expected=set(ConstructionClientLinkRequestState.__dataclass_fields__)-{'token_hash'}
        if not isinstance(data,dict) or set(data) not in (expected,expected-{'invalidated_at'}):raise _error()
        # Older encrypted receipts omit the new nullable state; never rewrite them.
        fields=dict(data)
        fields.setdefault('invalidated_at',None)
        for n in ('request_id','tenant_id','client_id','initiated_by_user_id','target_user_id'):
            fields[n]=_uuid(data[n])
        fields['expected_linked_user_id']=_uuid(data['expected_linked_user_id']) if data['expected_linked_user_id'] is not None else None
        for n in ('issued_at','expires_at','updated_at'):
            try:
                if not isinstance(data[n],str):raise ValueError()
                fields[n]=_time(datetime.fromisoformat(data[n]))
            except (ValueError,TypeError):raise _error() from None
            if fields[n].isoformat()!=data[n]:raise _error()
        fields['token_hash']=hash_client_link_confirmation_token(token)
        try:state=ConstructionClientLinkRequestState(**fields)
        except (ValueError,TypeError):raise _error() from None
        if state.consumed_at is not None or state.invalidated_at is not None or state.row_version!=1 or state.updated_at!=state.issued_at:raise _error()
        return ConstructionClientLinkIssuanceResult(state,token,_uuid(snapshot['audit_id']))

    @staticmethod
    def _context(material,actor,payload):
        if not isinstance(material,ConstructionClientLinkIssuanceResult) or not isinstance(material.request,ConstructionClientLinkRequestState):raise _error()
        state=material.request
        if (state.tenant_id!=actor.tenant_id or state.initiated_by_user_id!=actor.user_id
                or state.client_id!=_uuid(payload['client_id']) or state.target_user_id!=_uuid(payload['target_user_id'])
                or state.expected_client_row_version!=payload['expected_row_version']
                or (payload['replacement'] and state.expected_linked_user_id!=_uuid(payload['previous_user_id']))
                or (not payload['replacement'] and state.expected_linked_user_id not in (None,state.target_user_id))
                or state.row_version!=1 or state.consumed_at is not None or state.invalidated_at is not None or state.updated_at!=state.issued_at
                or not isinstance(material.audit_id,UUID)
                or not hmac.compare_digest(state.token_hash,hash_client_link_confirmation_token(material.token))):raise _error()

    async def _stored(self,material):
        state=material.request
        await self.service.clients.get_state_for_update(tenant_id=state.tenant_id,client_id=state.client_id)
        actual=await self.service.requests.get_request_for_update(tenant_id=state.tenant_id,request_id=state.request_id,
            target_user_id=state.target_user_id,token_hash=state.token_hash)
        if not isinstance(actual,ConstructionClientLinkRequestState):raise _error()
        # Consume and invalidation are distinct terminal transitions.
        terminal_at=actual.consumed_at if actual.consumed_at is not None else actual.invalidated_at
        if (actual.row_version!=(2 if terminal_at is not None else 1)
                or actual.updated_at!=(terminal_at if terminal_at is not None else actual.issued_at)):
            raise _error()
        mutable=('consumed_at','invalidated_at','row_version','updated_at')
        immutable={n:getattr(actual,n) for n in actual.__dataclass_fields__ if n not in mutable}
        original={n:getattr(state,n) for n in state.__dataclass_fields__ if n not in mutable}
        if immutable!=original:raise _error()

    def _scoped_service(self,token):
        # Request-local instance: never replace the shared service's generator,
        # even while concurrent commands use this workflow object.
        source=self.service
        return ConstructionClientLinkRequestIssuanceService(session=self.session,request_repository=source.requests,
            client_repository=source.clients,access_repository=source.access,event_repository=source.audit.event_repository,
            identity_reader=source.identity,confirmation_lifetime=CLIENT_LINK_CONFIRMATION_TTL,
            now_provider=source.now_provider,token_factory=lambda:token)

    async def request_email_link(
        self, *, actor, access_context, client_id, target_email,
        expected_row_version, idempotency_key,
        previous_user_id=None, trace_id=None,
    ):
        return await self._issue(
            actor=actor, access_context=access_context, client_id=client_id,
            target_user_id=None, target_email=target_email,
            expected_row_version=expected_row_version,
            previous_user_id=previous_user_id,
            replacement=previous_user_id is not None,
            idempotency_key=idempotency_key, trace_id=trace_id,
        )

    async def _resolve_email_target(
        self, *, actor, client_id, expected_row_version,
        previous_user_id, replacement, email,
    ):
        reader = self.target_email_reader
        if reader is None:
            raise ConstructionClientOperationError(
                "Verified email selection is not configured."
            )

        # Check the authorized client and requested base before resolving email.
        # This operation must not become a general Platform user lookup.
        before = await self.service.clients.get_state_for_update(
            tenant_id=actor.tenant_id, client_id=client_id,
        )
        if before is None:
            raise ConstructionClientNotFoundError(
                "Construction client is not available."
            )
        if (
            not isinstance(before, ConstructionClientUserLinkState)
            or before.client_id != client_id
            or before.tenant_id != actor.tenant_id
            or type(before.row_version) is not int
        ):
            raise _error()
        if before.row_version != expected_row_version:
            raise ConstructionRowVersionConflictError()
        if replacement and before.linked_user_id != previous_user_id:
            raise ConstructionRowVersionConflictError()

        # A grant or membership may have changed while the client lock waited.
        await self._authorize(actor)
        identifier = await reader.find_verified_user_id(email=email)
        if identifier is None:
            raise ClientLinkPlatformUserNotFoundError()
        if not isinstance(identifier, UUID):
            raise _error()
        return identifier

    async def request_link(self, *, actor, access_context, client_id, target_user_id, expected_row_version,
            idempotency_key=None, trace_id=None):
        return await self._issue(actor=actor,access_context=access_context,client_id=client_id,target_user_id=target_user_id,
            expected_row_version=expected_row_version,previous_user_id=None,replacement=False,idempotency_key=idempotency_key,trace_id=trace_id)

    async def request_replacement(self, *, actor, access_context, client_id, target_user_id, previous_user_id,
            expected_row_version, idempotency_key=None, trace_id=None):
        return await self._issue(actor=actor,access_context=access_context,client_id=client_id,target_user_id=target_user_id,
            expected_row_version=expected_row_version,previous_user_id=previous_user_id,replacement=True,idempotency_key=idempotency_key,trace_id=trace_id)

    async def _issue(self, *, actor, access_context, client_id, target_user_id, expected_row_version,
            previous_user_id, replacement, idempotency_key, trace_id, target_email=None):
        try:
            self._scope(actor,access_context)
            if self.session.in_transaction() is not True:raise ConstructionClientOperationError('Issuance workflow requires a caller transaction.')
            if target_email is not None:
                target_email = normalize_client_link_email(target_email)
                if target_user_id is not None or idempotency_key is None:
                    raise ConstructionClientValidationError(
                        "Email issuance requires an idempotency key and no caller target UUID."
                    )
            if (not isinstance(client_id,UUID)
                    or (target_email is None and not isinstance(target_user_id,UUID))
                    or target_user_id==actor.user_id
                    or type(expected_row_version) is not int or not 1<=expected_row_version<=MAX_CLIENT_ROW_VERSION
                    or (replacement and (not isinstance(previous_user_id,UUID) or previous_user_id==target_user_id))
                    or (idempotency_key is not None and (not isinstance(idempotency_key,str) or not idempotency_key.strip() or len(idempotency_key)>255))
                    or (trace_id is not None and (not isinstance(trace_id,str) or not trace_id.strip() or len(trace_id)>128))):
                raise ConstructionClientValidationError('Client link issuance request is invalid.')
            await self._authorize(actor)
            payload=dict(
                client_id=str(client_id), expected_row_version=expected_row_version,
                replacement=replacement,
                previous_user_id=str(previous_user_id) if replacement else None,
            )
            if target_email is None:
                # Preserve the legacy UUID command's hash and replay contract.
                payload["target_user_id"] = str(target_user_id)
            else:
                # Bind the normalized selector, not whichever user a later
                # lookup might return for that email.
                payload["target_email"] = target_email
            with self.session.no_autoflush:
                await set_construction_tenant_context(self.session,tenant_id=actor.tenant_id)
                reservation=None
                service=self.service
                email_delivery_receipt = None
                if idempotency_key is not None:
                    reservation=await self.idempotency.reserve(tenant_id=actor.tenant_id,principal_type='user',principal_id=actor.user_id,
                        operation=ISSUE,idempotency_key=idempotency_key,payload=payload,now=self._now())
                    if not isinstance(reservation,ApiIdempotencyReservation) or type(reservation.is_replay) is not bool:raise _error()
                    row=reservation.record
                    binding=dict(tenant_id=str(actor.tenant_id),principal_type='user',principal_id=str(actor.user_id),operation=ISSUE,
                        key_hash=hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest(),request_hash=hash_request_payload(payload),
                        created_at=_time(getattr(row,'created_at',None)).isoformat())
                    self._record(row,binding)
                    token=self.deriver.derive(reservation_id=row.id,reserved_at=row.created_at,tenant_id=actor.tenant_id,
                        initiated_by_user_id=actor.user_id,payload=payload)
                    service=self._scoped_service(token)
                if reservation is not None and reservation.is_replay:
                    row=reservation.record
                    if row.status=='processing' and reservation.response_status is None:
                        raise ConstructionClientLinkIdempotencyInProgressError('Client link issuance is still processing.')
                    saved=reservation.response_payload
                    if (row.status!='completed' or type(reservation.response_status) is not int or reservation.response_status!=201
                            or not isinstance(saved,dict) or set(saved) not in ({'binding','material'}, {'binding','material','email_delivery'}) or saved['binding']!=binding):raise _error()
                    material=self._material(saved['material'],token)
                    email_delivery_receipt = saved.get("email_delivery")
                    if email_delivery_receipt is not None and target_email is None:
                        raise _error()
                    if target_email is not None:
                        # Replay keeps the original target UUID. It neither
                        # resolves a new target nor creates a new request.
                        target_user_id = material.request.target_user_id
                else:
                    if reservation is not None and reservation.record.status!='processing':raise _error()
                    if target_email is not None:
                        target_user_id = await self._resolve_email_target(
                            actor=actor, client_id=client_id,
                            expected_row_version=expected_row_version,
                            previous_user_id=previous_user_id,
                            replacement=replacement, email=target_email,
                        )
                    common=dict(actor=actor,access_context=access_context,client_id=client_id,target_user_id=target_user_id,
                        expected_row_version=expected_row_version,trace_id=trace_id)
                    material=await (service.request_replacement(**common,previous_user_id=previous_user_id) if replacement else service.request_link(**common))
                    self._context(material,actor,payload if target_email is None else dict(payload,target_user_id=str(target_user_id)))
                    if target_email is not None and self.email_delivery_queue is not None:
                        email_delivery_receipt = await self.email_delivery_queue.enqueue(
                            actor=actor, material=material,
                            reservation=reservation, payload=payload,
                        )
                    if reservation is not None:
                        snapshot=dict(binding=binding,material=self._snapshot(material))
                        if email_delivery_receipt is not None:
                            snapshot["email_delivery"] = email_delivery_receipt
                        reserved_id=reservation.record.id
                        completed=await self.idempotency.complete(reservation=reservation,response_status=201,response_payload=snapshot,now=self._now())
                        self._record(completed,binding)
                        if (completed.id!=reserved_id or completed.status!='completed' or type(completed.response_status) is not int
                                or completed.response_status!=201 or self.idempotency.response_codec.decrypt(completed.response_ciphertext)!=snapshot):raise _error()
                self._context(material,actor,payload if target_email is None else dict(payload,target_user_id=str(target_user_id)))
                if target_email is not None and self.email_delivery_queue is not None:
                    # A historical receipt without a delivery job must not
                    # acquire one during replay or be advertised as queued.
                    self.email_delivery_queue.validate_receipt(
                        actor=actor, material=material,
                        reservation=reservation, payload=payload,
                        receipt=email_delivery_receipt,
                    )
                await self._stored(material)
                await self._authorize(actor)
                if await self.service.identity.require_existing_user(user_id=target_user_id)!=target_user_id:raise _error()
                if (reservation is None or not reservation.is_replay) and not material.request.issued_at<=self._now()<material.request.expires_at:
                    raise ConstructionClientOperationError('Client link issuance expired before commit.')
            await self.session.commit()
            return material
        except (ConstructionClientValidationError,ConstructionClientOperationError,ConstructionClientNotFoundError,
                ApiIdentityAccessError,ConstructionPermissionDeniedError,ConstructionScopeDeniedError,
                ConstructionRowVersionConflictError,ApiIdempotencyKeyReusedError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionClientOperationError('Client link issuance workflow failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_client_link_issuance_workflow(*, session, platform_session, encryption_key,
        confirmation_lifetime=CLIENT_LINK_CONFIRMATION_TTL, now_provider=None):
    return ConstructionClientLinkIssuanceWorkflow(session=session,
        issuance_service=build_construction_client_link_request_issuance_service(session=session,platform_session=platform_session,
            confirmation_lifetime=confirmation_lifetime,now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session,encryption_key=encryption_key),
        token_deriver=ConstructionClientLinkTokenDeriver(encryption_key=encryption_key),
        target_email_reader=ConstructionClientLinkEmailRepository(platform_session),
        now_provider=now_provider)

# client_link_exact_verified_email_v1

# client_link_atomic_email_queue_v1
