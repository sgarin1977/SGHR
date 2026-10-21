"""Platform command: membership, grants, audit and optional replay share one commit."""
from datetime import datetime, timezone
from uuid import UUID

from services.api_idempotency import ApiIdempotencyKeyReusedError, build_api_idempotency_service
from services.construction_organization_removal_workflow import ConstructionOrganizationRemovalWorkflow
from services.platform_membership import PlatformMembershipRemovalAuthority, PlatformMembershipRemovalDeniedError, PlatformMembershipRemovalError
from services.platform_membership_authorization import build_authorized_organization_removal_workflow

OPERATION='platform.organization.membership.remove'


class PlatformRemovalInProgressError(PlatformMembershipRemovalError):
    pass


class PlatformOrganizationRemovalCommand:
    def __init__(self, *, session, removal_workflow, idempotency_service=None, now_provider=None):
        if (not isinstance(removal_workflow,ConstructionOrganizationRemovalWorkflow)
                or removal_workflow.session is not session
                or (idempotency_service is not None and
                    getattr(getattr(idempotency_service,'repository',None),'session',None) is not session)):
            raise ValueError('Organization removal requires one shared command transaction.')
        self.session,self.workflow,self.idempotency=session,removal_workflow,idempotency_service
        self.now_provider=now_provider or (lambda:datetime.now(timezone.utc))

    def _now(self):
        value=self.now_provider()
        if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PlatformMembershipRemovalError('Removal clock is invalid.')
        return value.astimezone(timezone.utc)

    def _authority(self,value,actor,user_id):
        if (not isinstance(value,PlatformMembershipRemovalAuthority)
                or (value.actor_user_id,value.tenant_id,value.target_user_id)!=(actor.user_id,actor.tenant_id,user_id)
                or (value.valid_until is not None and (not isinstance(value.valid_until,datetime)
                    or value.valid_until.tzinfo is None or value.valid_until.utcoffset() is None
                    or value.valid_until<=self._now()))):
            raise PlatformMembershipRemovalDeniedError('Removal authority is unavailable.')

    @staticmethod
    def _public(data,user_id):
        if (not isinstance(data,dict) or set(data)!={'user_id','changed'}
                or data['user_id']!=str(user_id) or type(data['changed']) is not bool):
            raise PlatformMembershipRemovalError('Removal response is invalid.')
        return dict(data)

    async def remove_from_organization(self, *, actor,user_id,idempotency_key=None,trace_id=None):
        try:
            if idempotency_key is not None and (type(idempotency_key) is not str
                    or not idempotency_key.strip() or len(idempotency_key)>255):
                raise ValueError('Idempotency key is invalid.')
            prepared=await self.workflow._prepare_authority(actor=actor,user_id=user_id,trace_id=trace_id)
            authorizer=self.workflow.service.authorization
            authority=await authorizer.authorize_removal(actor=actor,target_user_id=user_id)
            self._authority(authority,actor,user_id)
            reservation=None
            if idempotency_key is not None:
                if self.idempotency is None:
                    raise PlatformMembershipRemovalError('Removal replay is not configured.')
                reservation=await self.idempotency.reserve(tenant_id=actor.tenant_id,principal_type='user',
                    principal_id=actor.user_id,operation=OPERATION,idempotency_key=idempotency_key,
                    payload={'target_user_id':str(user_id)},now=self._now())
                # The catalogue, membership and grant rows remain locked. Only
                # time can invalidate authority during a receipt-reservation wait.
                authority=await authorizer.authorize_removal(actor=actor,target_user_id=user_id)
                self._authority(authority,actor,user_id)
                if reservation.is_replay:
                    if reservation.response_status is None and reservation.response_payload is None:
                        raise PlatformRemovalInProgressError('Removal is processing.')
                    if reservation.response_status!=200:
                        raise PlatformMembershipRemovalError('Removal replay status is invalid.')
                    data=self._public(reservation.response_payload,user_id)
                    for uid,receipt in prepared[1]:
                        self.workflow._lock_receipt(receipt,actor,uid,prepared[0])
                    self._authority(authority,actor,user_id)
                    await self.session.commit()
                    return data
            result=await self.workflow._mutate_prepared(actor=actor,user_id=user_id,trace_id=trace_id,prepared=prepared)
            data=self._public({'user_id':str(user_id),'changed':result.changed},user_id)
            if reservation is not None:
                await self.idempotency.complete(reservation=reservation,response_status=200,response_payload=data,now=self._now())
            for uid,receipt in prepared[1]:
                self.workflow._lock_receipt(receipt,actor,uid,prepared[0])
            self._authority(authority,actor,user_id)
            await self.session.commit()
            return data
        except (PlatformMembershipRemovalError,ApiIdempotencyKeyReusedError,ValueError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise PlatformMembershipRemovalError('Platform organization removal failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_organization_removal_command(*,session,idempotency_encryption_key=None,now_provider=None):
    workflow=build_authorized_organization_removal_workflow(session=session,now_provider=now_provider)
    idempotency=(build_api_idempotency_service(session,encryption_key=idempotency_encryption_key)
        if idempotency_encryption_key is not None else None)
    return PlatformOrganizationRemovalCommand(session=session,removal_workflow=workflow,
        idempotency_service=idempotency,now_provider=now_provider)
