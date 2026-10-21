"""Atomic client-link confirmation with shared optional keyed replay support.

Transport requiredness is not set here. A supplied key uses the existing
24-hour encrypted idempotency store. Without a key, the service still enforces
one-time consume and optimistic locking. No public endpoint is registered here.
"""
from datetime import datetime, timezone
import hashlib
import hmac
from types import SimpleNamespace
from uuid import UUID

from database.construction import set_construction_tenant_context
from database.repositories.construction_client_link_requests import ConstructionClientLinkRequestState
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.api_idempotency import (
    ApiIdempotencyReservation, ApiIdempotencyKeyReusedError, build_api_idempotency_service, hash_request_payload,
)
from services.construction_client_link_confirmation import (
    ConstructionClientLinkConfirmationResult, ConstructionClientLinkConfirmationDeniedError,
    build_construction_client_link_confirmation_service,
)
from services.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec
from services.construction_client_link_request_issuance import hash_client_link_confirmation_token
from services.construction_clients import ConstructionClientOperationError, ConstructionClientValidationError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_access,
)

CONFIRM = 'construction.client_link.confirm'


class ConstructionClientLinkIdempotencyInProgressError(ConstructionClientOperationError):
    status_code = 409
    code = 'idempotency_in_progress'


def _error():
    return ConstructionClientOperationError('Client confirmation workflow state is invalid.')


def _uuid(value):
    if not isinstance(value,str):raise _error()
    try:identifier=UUID(value)
    except (ValueError,TypeError,AttributeError):raise _error() from None
    if str(identifier)!=value:raise _error()
    return identifier


def _time(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise _error()
    return value.astimezone(timezone.utc)


class ConstructionClientLinkConfirmationWorkflow:
    def __init__(self, *, session, confirmation_service, idempotency_service, context_codec, now_provider=None):
        methods = ((confirmation_service,('confirm_request',)),
            (getattr(confirmation_service,'requests',None),('get_request_for_update',)),
            (getattr(confirmation_service,'clients',None),('get_state_for_update',)),
            (getattr(confirmation_service,'access',None),('has_active_platform_membership','list_active_grants')),
            (getattr(idempotency_service,'repository',None),('reserve_record',)))
        if (session is None or any(getattr(repo,'session',None) is not session
                or any(not callable(getattr(repo,name,None)) for name in names) for repo,names in methods)
                or not all(callable(getattr(session,name,None)) for name in ('execute','in_transaction','commit','rollback'))
                or not hasattr(session,'no_autoflush')
                or not isinstance(context_codec,ConstructionClientLinkRecipientContextCodec)
                or not callable(getattr(idempotency_service,'reserve',None))
                or not callable(getattr(idempotency_service,'complete',None))
                or not callable(getattr(getattr(idempotency_service,'response_codec',None),'decrypt',None))
                or getattr(getattr(confirmation_service,'identity',None),'session',None) in (None,session)
                or not callable(getattr(getattr(confirmation_service,'identity',None),'require_active_user',None))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Client confirmation workflow requires shared writes, separate Platform identity and atomic encrypted idempotency.')
        self.session,self.service,self.idempotency,self.context_codec = session,confirmation_service,idempotency_service,context_codec
        self.now_provider = now_provider or (lambda:datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=self.service.access,now_provider=self._now)

    def _now(self):return _time(self.now_provider())

    async def _active(self,user_id):
        if await self.service.identity.require_active_user(user_id=user_id)!=user_id:
            raise ApiIdentityAccessError('Authenticated Platform identity is not available.')

    async def _authorize(self,state,recipient_id):
        await self._active(recipient_id)
        await self._active(state.initiated_by_user_id)
        principal=SimpleNamespace(user_id=state.initiated_by_user_id,tenant_id=state.tenant_id)
        if await self.service.access.has_active_platform_membership(user_id=principal.user_id,tenant_id=principal.tenant_id) is not True:
            raise ConstructionPermissionDeniedError('Client edit membership is not available.')
        context=await self.resolver.resolve(actor=principal)
        require_construction_access(actor=principal,access_context=context,permission_code='construction.projects.edit')

    @staticmethod
    def _snapshot(result):
        if not isinstance(result,ConstructionClientLinkConfirmationResult):raise _error()
        return {name:(value.isoformat() if isinstance(value,datetime) else str(value) if isinstance(value,UUID) else value)
            for name in result.__dataclass_fields__ for value in (getattr(result,name),)}

    @staticmethod
    def _result(data,route):
        if not isinstance(data,dict) or set(data)!=set(ConstructionClientLinkConfirmationResult.__dataclass_fields__):raise _error()
        fields=dict(data)
        for name in ('request_id','tenant_id','client_id','linked_user_id','request_audit_id'):
            fields[name]=_uuid(data[name])
        fields['client_audit_id']=_uuid(data['client_audit_id']) if data['client_audit_id'] is not None else None
        try:
            if not isinstance(data['consumed_at'],str):raise ValueError()
            fields['consumed_at']=_time(datetime.fromisoformat(data['consumed_at']))
        except (ValueError,TypeError):raise _error() from None
        if (fields['consumed_at'].isoformat()!=data['consumed_at']
                or fields['request_id']!=route.request_id or fields['tenant_id']!=route.tenant_id
                or fields['client_id']!=route.client_id or fields['linked_user_id']!=route.target_user_id
                or type(data['client_row_version']) is not int or not 1<=data['client_row_version']<2**63
                or type(data['request_row_version']) is not int or data['request_row_version']!=route.expected_request_row_version+1
                or type(data['client_changed']) is not bool
                or (fields['client_audit_id'] is not None)!=data['client_changed']):raise _error()
        return ConstructionClientLinkConfirmationResult(**fields)

    async def _saved_request(self,route,digest):
        # Preserve client -> request lock order. Replay returns a historical
        # receipt even after a later unlink/archive; it never reapplies the link.
        await self.service.clients.get_state_for_update(tenant_id=route.tenant_id,client_id=route.client_id)
        state=await self.service.requests.get_request_for_update(tenant_id=route.tenant_id,request_id=route.request_id,
            target_user_id=route.target_user_id,token_hash=digest)
        if (not isinstance(state,ConstructionClientLinkRequestState) or state.tenant_id!=route.tenant_id
                or state.client_id!=route.client_id or state.request_id!=route.request_id
                or state.target_user_id!=route.target_user_id or not hmac.compare_digest(state.token_hash,digest)):
            raise ConstructionClientLinkConfirmationDeniedError()
        return state

    @staticmethod
    def _verify_receipt(state,result):
        if (state.invalidated_at is not None or state.consumed_at!=result.consumed_at or state.row_version!=result.request_row_version
                or result.client_changed!=(state.expected_linked_user_id!=state.target_user_id)
                or result.client_row_version!=state.expected_client_row_version+int(result.client_changed)
                or not state.issued_at<=result.consumed_at<state.expires_at):raise _error()

    def _record(self,record,binding):
        if (not isinstance(getattr(record,'id',None),UUID)
                or getattr(record,'tenant_id',None)!=_uuid(binding['tenant_id'])
                or getattr(record,'principal_id',None)!=_uuid(binding['principal_id'])
                or any(getattr(record,name,None)!=binding[name] for name in ('principal_type','operation','key_hash','request_hash'))
                or _time(getattr(record,'expires_at',None))<=self._now()):raise _error()

    async def confirm_request(self, *, actor, recipient_context, token, idempotency_key=None, trace_id=None):
        try:
            if (not isinstance(actor,ApiActorContext) or actor.status!='active' or not isinstance(actor.user_id,UUID)):
                raise ApiIdentityAccessError('Authenticated Platform recipient is not available.')
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Confirmation workflow requires an existing caller transaction.')
            if ((idempotency_key is not None and (not isinstance(idempotency_key,str) or not idempotency_key.strip() or len(idempotency_key)>255))
                    or (trace_id is not None and (not isinstance(trace_id,str) or not trace_id.strip() or len(trace_id)>128))):
                raise ConstructionClientValidationError('Client confirmation request metadata is invalid.')
            route=self.context_codec.resolve(recipient_context=recipient_context,token=token)
            if route.target_user_id!=actor.user_id:raise ConstructionClientLinkConfirmationDeniedError()
            digest=hash_client_link_confirmation_token(token)
            await self._active(actor.user_id)
            with self.session.no_autoflush:
                await set_construction_tenant_context(self.session,tenant_id=route.tenant_id)
                reservation=None
                if idempotency_key is not None:
                    payload=dict(request_id=str(route.request_id),client_id=str(route.client_id),
                        expected_request_row_version=route.expected_request_row_version,token_hash=digest)
                    binding=dict(tenant_id=str(route.tenant_id),principal_type='user',principal_id=str(actor.user_id),operation=CONFIRM,
                        key_hash=hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest(),request_hash=hash_request_payload(payload))
                    reservation=await self.idempotency.reserve(tenant_id=route.tenant_id,principal_type='user',principal_id=actor.user_id,
                        operation=CONFIRM,idempotency_key=idempotency_key,payload=payload,now=self._now())
                    if not isinstance(reservation,ApiIdempotencyReservation) or type(reservation.is_replay) is not bool:raise _error()
                    self._record(reservation.record,binding)
                if reservation is not None and reservation.is_replay:
                    if reservation.record.status=='processing' and reservation.response_status is None:
                        raise ConstructionClientLinkIdempotencyInProgressError('Client confirmation is still processing.')
                    snapshot=reservation.response_payload
                    if (reservation.record.status!='completed' or type(reservation.response_status) is not int or reservation.response_status!=200
                            or not isinstance(snapshot,dict) or set(snapshot)!={'binding','data'} or snapshot['binding']!=binding):raise _error()
                    result=self._result(snapshot['data'],route)
                    state=await self._saved_request(route,digest)
                    self._verify_receipt(state,result)
                else:
                    if reservation is not None and reservation.record.status!='processing':raise _error()
                    result=await self.service.confirm_request(actor=actor,tenant_id=route.tenant_id,client_id=route.client_id,
                        request_id=route.request_id,token=token,expected_request_row_version=route.expected_request_row_version,trace_id=trace_id)
                    data=self._snapshot(result)
                    result=self._result(data,route)
                    state=await self._saved_request(route,digest)
                    self._verify_receipt(state,result)
                    if reservation is not None:
                        reserved_id=reservation.record.id
                        snapshot=dict(binding=binding,data=data)
                        row=await self.idempotency.complete(reservation=reservation,response_status=200,response_payload=snapshot,now=self._now())
                        self._record(row,binding)
                        if (row.id!=reserved_id or row.status!='completed' or type(row.response_status) is not int
                                or row.response_status!=200 or self.idempotency.response_codec.decrypt(row.response_ciphertext)!=snapshot):raise _error()
                await self._authorize(state,actor.user_id)
            await self.session.commit()
            return result
        except (ConstructionClientValidationError,ConstructionClientOperationError,ApiIdentityAccessError,
                ConstructionPermissionDeniedError,ConstructionScopeDeniedError,ConstructionRowVersionConflictError,ApiIdempotencyKeyReusedError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionClientOperationError('Client confirmation workflow failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_client_link_confirmation_workflow(*, session, platform_session, encryption_key, now_provider=None):
    return ConstructionClientLinkConfirmationWorkflow(session=session,
        confirmation_service=build_construction_client_link_confirmation_service(session=session,platform_session=platform_session,now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session,encryption_key=encryption_key),
        context_codec=ConstructionClientLinkRecipientContextCodec(encryption_key=encryption_key),now_provider=now_provider)
