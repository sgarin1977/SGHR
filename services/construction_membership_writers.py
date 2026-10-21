"""Production factory adapters for the shared tenant/member write protocol.

Lock before workflow reservations and all domain row locks. The underlying
preparation services retain their business rules; their factories select these
adapters. No authorization is conferred by an advisory lock.
"""
from uuid import UUID

from database.repositories.platform_membership_serialization import (
    PlatformMembershipSerializationRepository, PlatformMembershipSerializationReceipt,
)
from services.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction_access_issuance import ConstructionAccessIssuanceService
from services.construction_access_issuance_workflow import ConstructionAccessIssuanceWorkflow
from services.construction_access_mutations import ConstructionAccessMutationService
from services.construction_access_workflows import ConstructionAccessMutationWorkflow
from services.construction_grants import (
    ConstructionAccessGrantService, ConstructionAccessContextResolver, ConstructionGrantOperationError,
)
from services.construction_invitation_acceptance import ConstructionInvitationAcceptanceService
from services.construction_invitation_acceptance_workflow import ConstructionInvitationAcceptanceWorkflow
from services.construction_invitations import (
    ConstructionInvitationOperationError, ConstructionInvitationRecipientDeniedError, ConstructionInvitationValidationError,
)
from services.construction_permissions import ConstructionPermissionDeniedError
from services.construction_team_block import ConstructionTeamBlockService
from services.construction_team_block_workflow import ConstructionTeamBlockWorkflow


def _actor(actor, *, tenant_id=None):
    tenant = getattr(actor, 'tenant_id', None) if tenant_id is None else tenant_id
    if (getattr(actor, 'status', None) != 'active' or not isinstance(getattr(actor, 'user_id', None), UUID)
            or not isinstance(tenant, UUID)):
        raise ConstructionPermissionDeniedError('Construction write actor is unavailable.')
    return tenant


class _MembershipWriter:
    def __init__(self, *, membership_serializer=None, **kwargs):
        super().__init__(**kwargs)
        if membership_serializer is not None and (
                getattr(membership_serializer, 'session', None) is not self.session
                or not callable(getattr(membership_serializer, 'lock_member', None))):
            raise ValueError('Membership writer requires shared-session serialization.')
        self._membership_serializer = membership_serializer

    async def _serialize_member(self, *, tenant_id, user_id):
        try:
            if not isinstance(tenant_id, UUID) or not isinstance(user_id, UUID):
                raise ConstructionPermissionDeniedError('Construction write target is unavailable.')
            # Lazy wiring lets read-only factory consumers keep using read APIs.
            serializer = self._membership_serializer
            if serializer is None:
                serializer = PlatformMembershipSerializationRepository(self.session)
            receipt = await serializer.lock_member(tenant_id=tenant_id, user_id=user_id)
            transaction = self.session.get_transaction()
            if (not isinstance(receipt, PlatformMembershipSerializationReceipt)
                    or receipt.tenant_id != tenant_id or receipt.user_id != user_id
                    or receipt.transaction is not transaction or transaction is None
                    or transaction.is_active is not True or self.session.in_transaction() is not True
                    or self.session.get_nested_transaction() is not None):
                raise ConstructionGrantOperationError('Construction write serialization receipt is invalid.')
            return receipt
        except ConstructionPermissionDeniedError:
            raise
        except Exception:
            raise ConstructionGrantOperationError('Construction write serialization is unavailable.') from None

    async def _serialize_actor_target(self, *, actor, user_id, tenant_id=None):
        tenant = _actor(actor, tenant_id=tenant_id)
        if not isinstance(user_id, UUID):
            raise ConstructionPermissionDeniedError('Construction write target is unavailable.')
        # Authority and target use one order, including self-directed operations.
        # Otherwise removal of an actor could invert with its locked grant/audit.
        for member in sorted({actor.user_id, user_id}, key=lambda value: value.int):
            await self._serialize_member(tenant_id=tenant, user_id=member)

    async def _serialize_grant(self, *, actor, grant_id):
        tenant = _actor(actor)
        if not isinstance(grant_id, UUID) or self.session.in_transaction() is not True:
            raise ConstructionPermissionDeniedError('Construction access grant is unavailable.')
        try:
            # Scalar, tenant-filtered locator only: no FOR UPDATE before barrier.
            member = await self.repository.get_grant_member_id(tenant_id=tenant, grant_id=grant_id)
            if not isinstance(member, UUID):
                raise ConstructionPermissionDeniedError('Construction access grant is unavailable.')
            await self._serialize_actor_target(actor=actor, user_id=member)
            current = await self.repository.get_grant_member_id(tenant_id=tenant, grant_id=grant_id)
            if current != member:
                raise ConstructionAccessGrantConflictError()
            return member
        except (ConstructionPermissionDeniedError, ConstructionAccessGrantConflictError, ConstructionGrantOperationError):
            raise
        except Exception:
            raise ConstructionGrantOperationError('Construction access locator is unavailable.') from None


class SerializedConstructionAccessIssuanceService(_MembershipWriter, ConstructionAccessIssuanceService):
    async def grant_access(self, **values):
        try:
            await self._serialize_actor_target(actor=values.get('actor'), user_id=values.get('target_user_id'))
            return await super().grant_access(**values)
        except BaseException:
            await self.session.rollback()
            raise


class SerializedConstructionAccessMutationService(_MembershipWriter, ConstructionAccessMutationService):
    async def _original(self, *, actor, access_context, grant_id):
        member = await self._serialize_grant(actor=actor, grant_id=grant_id)
        access_context = await ConstructionAccessContextResolver(repository=self.repository,
            now_provider=self.now_provider).resolve(actor=actor)
        grant = await super()._original(actor=actor, access_context=access_context, grant_id=grant_id)
        if grant.user_id != member:
            raise ConstructionAccessGrantConflictError()
        return grant

    async def replace_access(self, **values):
        try:
            return await super().replace_access(**values)
        except BaseException:
            await self.session.rollback()
            raise

    async def revoke_access(self, **values):
        try:
            return await super().revoke_access(**values)
        except BaseException:
            await self.session.rollback()
            raise


class SerializedConstructionAccessGrantService(_MembershipWriter, ConstructionAccessGrantService):
    async def grant_access(self, **values):
        try:
            actor = values.get('actor')
            await self._serialize_actor_target(actor=actor, user_id=values.get('target_user_id'))
            # The legacy caller context may have been read before the wait.
            values['access_context'] = await ConstructionAccessContextResolver(repository=self.repository,
                now_provider=self.now_provider).resolve(actor=actor)
            return await super().grant_access(**values)
        except BaseException:
            await self.session.rollback()
            raise

    async def revoke_access(self, **values):
        try:
            actor = values.get('actor')
            await self._serialize_grant(actor=actor, grant_id=values.get('grant_id'))
            values['access_context'] = await ConstructionAccessContextResolver(repository=self.repository,
                now_provider=self.now_provider).resolve(actor=actor)
            return await super().revoke_access(**values)
        except BaseException:
            await self.session.rollback()
            raise


class SerializedConstructionInvitationAcceptanceService(_MembershipWriter, ConstructionInvitationAcceptanceService):
    async def _serialize_recipient(self, *, actor, tenant_id):
        try:
            if not isinstance(tenant_id, UUID):
                raise ConstructionInvitationValidationError('Trusted invitation tenant is required.')
            await self._serialize_actor_target(actor=actor, user_id=getattr(actor, 'user_id', None), tenant_id=tenant_id)
        except ConstructionPermissionDeniedError:
            raise ConstructionInvitationRecipientDeniedError('Invitation recipient is unavailable.') from None
        except ConstructionGrantOperationError:
            raise ConstructionInvitationOperationError('Invitation write serialization is unavailable.') from None

    async def accept_invitation(self, **values):
        try:
            await self._serialize_recipient(actor=values.get('actor'), tenant_id=values.get('tenant_id'))
            return await super().accept_invitation(**values)
        except BaseException:
            await self.session.rollback()
            raise


class SerializedConstructionAccessIssuanceWorkflow(ConstructionAccessIssuanceWorkflow):
    async def grant_access(self, **values):
        try:
            await self.service._serialize_actor_target(actor=values.get('actor'), user_id=values.get('target_user_id'))
            return await super().grant_access(**values)
        except BaseException:
            await self.session.rollback()
            raise


class SerializedConstructionAccessMutationWorkflow(ConstructionAccessMutationWorkflow):
    async def _run(self, **values):
        try:
            await self.service._serialize_grant(actor=values.get('actor'), grant_id=values.get('grant_id'))
            return await super()._run(**values)
        except BaseException:
            await self.session.rollback()
            raise


class SerializedConstructionInvitationAcceptanceWorkflow(ConstructionInvitationAcceptanceWorkflow):
    async def accept_invitation(self, **values):
        try:
            await self.acceptance_service._serialize_recipient(actor=values.get('actor'), tenant_id=values.get('tenant_id'))
            return await super().accept_invitation(**values)
        except BaseException:
            await self.session.rollback()
            raise


class SerializedConstructionTeamBlockService(_MembershipWriter, ConstructionTeamBlockService):
    async def block_access(self, **values):
        try:
            await self._serialize_actor_target(actor=values.get('actor'), user_id=values.get('user_id'))
            return await super().block_access(**values)
        except BaseException:
            await self.session.rollback()
            raise


class SerializedConstructionTeamBlockWorkflow(ConstructionTeamBlockWorkflow):
    async def block_access(self, **values):
        try:
            await self.service._serialize_actor_target(actor=values.get('actor'), user_id=values.get('user_id'))
            return await super().block_access(**values)
        except BaseException:
            await self.session.rollback()
            raise
