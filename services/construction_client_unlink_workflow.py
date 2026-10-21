"""Atomic client unlink and optional keyed receipt replay.

Caller supplies authenticated Platform identity and a trusted server tenant
for self unlink. A self replay is authorized only as an immutable receipt of
that user's original unlink; it never grants permission to edit the client.
Editor replay requires fresh CRM edit authority. No HTTP policy is defined.
"""
from datetime import datetime,timezone
import hashlib
from uuid import UUID

from database.construction import set_construction_tenant_context
from services.api_identity import ApiActorContext,ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyReservation,ApiIdempotencyKeyReusedError,build_api_idempotency_service,hash_request_payload
from services.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction_client_unlink import ConstructionClientUnlinkService,ConstructionClientUnlinkResult,build_construction_client_unlink_service
from services.construction_clients import ConstructionClientValidationError,ConstructionClientNotFoundError,ConstructionClientOperationError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import ConstructionAccessContext,ConstructionPermissionDeniedError,ConstructionScopeDeniedError,require_construction_access

UNLINK='construction.client_link.unlink'


def _error():return ConstructionClientOperationError('Client unlink workflow state is invalid.')


def _uuid(value):
    if not isinstance(value,str):raise _error()
    try:identifier=UUID(value)
    except (ValueError,TypeError,AttributeError):raise _error() from None
    if str(identifier)!=value:raise _error()
    return identifier


def _time(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise _error()
    return value.astimezone(timezone.utc)


class ConstructionClientUnlinkWorkflow:
    def __init__(self, *, session, unlink_service, idempotency_service, now_provider=None):
        if (not isinstance(unlink_service,ConstructionClientUnlinkService) or unlink_service.session is not session
                or (idempotency_service is not None and (
                    getattr(getattr(idempotency_service,'repository',None),'session',None) is not session
                    or not callable(getattr(getattr(idempotency_service,'repository',None),'reserve_record',None))
                    or not callable(getattr(getattr(idempotency_service,'response_codec',None),'decrypt',None))))
                or not all(callable(getattr(session,n,None)) for n in ('execute','in_transaction','commit','rollback'))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Client unlink workflow requires shared writes and atomic encrypted idempotency.')
        self.session,self.service,self.idempotency=session,unlink_service,idempotency_service
        self.now_provider=now_provider or (lambda:datetime.now(timezone.utc))
        self.resolver=ConstructionAccessContextResolver(repository=unlink_service.access,now_provider=self._now)

    def _now(self):return _time(self.now_provider())

    @staticmethod
    def _scope(actor,context):
        if not isinstance(actor.tenant_id,UUID) or not isinstance(context,ConstructionAccessContext):
            raise ConstructionPermissionDeniedError('Client edit authorization is not available.')
        require_construction_access(actor=actor,access_context=context,permission_code='construction.projects.edit')

    async def _active(self,actor):
        if await self.service.identity.require_active_user(user_id=actor.user_id)!=actor.user_id:
            raise ApiIdentityAccessError('Authenticated Platform actor is not available.')

    async def _authorize(self,actor,as_editor):
        await self._active(actor)
        if as_editor:
            if await self.service.access.has_active_platform_membership(user_id=actor.user_id,tenant_id=actor.tenant_id) is not True:
                raise ConstructionPermissionDeniedError('Client edit membership is not available.')
            self._scope(actor,await self.resolver.resolve(actor=actor))

    @staticmethod
    def _snapshot(result):
        if not isinstance(result,ConstructionClientUnlinkResult):raise _error()
        return {n:str(v) if isinstance(v,UUID) else v for n in result.__dataclass_fields__ for v in (getattr(result,n),)}

    @staticmethod
    def _result(data, *, actor, tenant_id, client_id, expected_row_version, as_editor):
        if not isinstance(data,dict) or set(data)!=set(ConstructionClientUnlinkResult.__dataclass_fields__):raise _error()
        fields=dict(data)
        fields['client_id'],fields['tenant_id']=_uuid(data['client_id']),_uuid(data['tenant_id'])
        for name in ('previous_linked_user_id','audit_id'):
            fields[name]=_uuid(data[name]) if data[name] is not None else None
        if (fields['client_id']!=client_id or fields['tenant_id']!=tenant_id or data['linked_user_id'] is not None
                or type(data['changed']) is not bool or type(data['row_version']) is not int
                or not 1<=data['row_version']<2**63 or data['row_version']!=expected_row_version+int(data['changed'])
                or (fields['previous_linked_user_id'] is not None)!=data['changed']
                or (fields['audit_id'] is not None)!=data['changed']
                or (not as_editor and (data['changed'] is not True or fields['previous_linked_user_id']!=actor.user_id))):raise _error()
        return ConstructionClientUnlinkResult(**fields)

    def _record(self,row,binding):
        if (not isinstance(getattr(row,'id',None),UUID) or getattr(row,'tenant_id',None)!=_uuid(binding['tenant_id'])
                or getattr(row,'principal_id',None)!=_uuid(binding['principal_id'])
                or any(getattr(row,n,None)!=binding[n] for n in ('principal_type','operation','key_hash','request_hash'))
                or _time(getattr(row,'created_at',None))>self._now()
                or _time(getattr(row,'expires_at',None))<=self._now()):raise _error()

    async def unlink_self(self, *, actor, tenant_id, client_id, expected_row_version, idempotency_key=None, trace_id=None):
        return await self._unlink(actor=actor,tenant_id=tenant_id,access_context=None,client_id=client_id,
            expected_row_version=expected_row_version,idempotency_key=idempotency_key,trace_id=trace_id,as_editor=False)

    async def unlink_as_editor(self, *, actor, access_context, client_id, expected_row_version, idempotency_key=None, trace_id=None):
        return await self._unlink(actor=actor,tenant_id=getattr(actor,'tenant_id',None),access_context=access_context,
            client_id=client_id,expected_row_version=expected_row_version,idempotency_key=idempotency_key,trace_id=trace_id,as_editor=True)

    async def _unlink(self, *, actor, tenant_id, access_context, client_id, expected_row_version, idempotency_key, trace_id, as_editor):
        try:
            if not isinstance(actor,ApiActorContext) or actor.status!='active' or not isinstance(actor.user_id,UUID):
                raise ApiIdentityAccessError('Authenticated Platform actor is not available.')
            if as_editor:self._scope(actor,access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Client unlink workflow requires a caller transaction.')
            if (not isinstance(tenant_id,UUID) or not isinstance(client_id,UUID)
                    or type(expected_row_version) is not int or not 1<=expected_row_version<2**63
                    or (idempotency_key is not None and (not isinstance(idempotency_key,str) or not idempotency_key.strip() or len(idempotency_key)>255))
                    or (trace_id is not None and (not isinstance(trace_id,str) or not trace_id.strip() or len(trace_id)>128))):
                raise ConstructionClientValidationError('Client unlink request is invalid.')
            if idempotency_key is not None and self.idempotency is None:
                raise ConstructionClientOperationError('Client unlink replay is not configured.')
            await self._active(actor)
            with self.session.no_autoflush:
                await set_construction_tenant_context(self.session,tenant_id=tenant_id)
                await self._authorize(actor,as_editor)
                reservation=None
                validation=dict(actor=actor,tenant_id=tenant_id,client_id=client_id,expected_row_version=expected_row_version,as_editor=as_editor)
                if idempotency_key is not None:
                    payload=dict(client_id=str(client_id),expected_row_version=expected_row_version,mode='editor' if as_editor else 'self')
                    binding=dict(tenant_id=str(tenant_id),principal_type='user',principal_id=str(actor.user_id),operation=UNLINK,
                        key_hash=hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest(),request_hash=hash_request_payload(payload))
                    reservation=await self.idempotency.reserve(tenant_id=tenant_id,principal_type='user',principal_id=actor.user_id,
                        operation=UNLINK,idempotency_key=idempotency_key,payload=payload,now=self._now())
                    if not isinstance(reservation,ApiIdempotencyReservation) or type(reservation.is_replay) is not bool:raise _error()
                    self._record(reservation.record,binding)
                if reservation is not None and reservation.is_replay:
                    if reservation.record.status=='processing' and reservation.response_status is None:
                        raise ConstructionClientLinkIdempotencyInProgressError('Client unlink is still processing.')
                    snapshot=reservation.response_payload
                    if (reservation.record.status!='completed' or type(reservation.response_status) is not int or reservation.response_status!=200
                            or not isinstance(snapshot,dict) or set(snapshot)!={'binding','data'} or snapshot['binding']!=binding):raise _error()
                    # Canonical identity + original recipient-owned receipt are
                    # sufficient for this historical self result. Never invoke
                    # the live unlink again or touch a newer client link.
                    result=self._result(snapshot['data'],**validation)
                else:
                    if reservation is not None and reservation.record.status!='processing':raise _error()
                    common=dict(actor=actor,client_id=client_id,expected_row_version=expected_row_version,trace_id=trace_id)
                    result=await (self.service.unlink_as_editor(**common,access_context=access_context) if as_editor
                        else self.service.unlink_self(**common,tenant_id=tenant_id))
                    data=self._snapshot(result)
                    result=self._result(data,**validation)
                    if reservation is not None:
                        snapshot=dict(binding=binding,data=data)
                        reserved_id=reservation.record.id
                        row=await self.idempotency.complete(reservation=reservation,response_status=200,response_payload=snapshot,now=self._now())
                        self._record(row,binding)
                        if (row.id!=reserved_id or row.status!='completed' or type(row.response_status) is not int
                                or row.response_status!=200 or self.idempotency.response_codec.decrypt(row.response_ciphertext)!=snapshot):raise _error()
                await self._authorize(actor,as_editor)
            await self.session.commit()
            return result
        except (ConstructionClientValidationError,ConstructionClientOperationError,ConstructionClientNotFoundError,ApiIdentityAccessError,
                ConstructionPermissionDeniedError,ConstructionScopeDeniedError,ConstructionRowVersionConflictError,ApiIdempotencyKeyReusedError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionClientOperationError('Client unlink workflow failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_client_unlink_workflow(*, session, platform_session, encryption_key=None, now_provider=None):
    return ConstructionClientUnlinkWorkflow(session=session,
        unlink_service=build_construction_client_unlink_service(session=session,platform_session=platform_session,now_provider=now_provider),
        idempotency_service=(build_api_idempotency_service(session,encryption_key=encryption_key)
            if encryption_key is not None else None),now_provider=now_provider)
