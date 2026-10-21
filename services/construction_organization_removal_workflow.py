"""Single-commit removal with reusable locked preparation for replay composition.

The configured Platform authorizer prepares evidence after ownership/member
barriers and before Core user locks. Public command workflows may compose the
private preparation methods with the existing idempotency ledger in one commit.
"""
from uuid import UUID

from database.repositories.platform_membership_serialization import PlatformMembershipSerializationReceipt
from services.api_identity import ApiActorContext
from services.construction_organization_removal import (
    ConstructionOrganizationRemovalResult, ConstructionOrganizationRemovalService,
    build_construction_organization_removal_service,
)
from services.platform_membership import PlatformMembershipRemovalDeniedError, PlatformMembershipRemovalError


class ConstructionOrganizationRemovalWorkflow:
    def __init__(self, *, session, removal_service, serialization_repository):
        if (session is None or any(not callable(getattr(session, name, None)) for name in (
                'commit', 'rollback', 'in_transaction', 'get_transaction', 'get_nested_transaction'))
                or any(getattr(item, 'session', None) is not session for item in (removal_service, serialization_repository))
                or not callable(getattr(removal_service, 'remove_from_organization', None))
                or not callable(getattr(serialization_repository, 'lock_member', None))):
            raise ValueError('Organization removal workflow requires shared-session serialization and removal.')
        self.session, self.service, self.serialization = session, removal_service, serialization_repository

    def _transaction(self):
        transaction = self.session.get_transaction()
        if (self.session.in_transaction() is not True or transaction is None or transaction.is_active is not True
                or self.session.get_nested_transaction() is not None):
            raise PlatformMembershipRemovalError('Organization removal requires an active outer transaction.')
        return transaction

    def _lock_receipt(self, receipt, actor, user_id, transaction):
        if (not isinstance(receipt, PlatformMembershipSerializationReceipt)
                or receipt.tenant_id != actor.tenant_id or receipt.user_id != user_id
                or receipt.transaction is not transaction or self._transaction() is not transaction):
            raise PlatformMembershipRemovalError('Organization removal serialization receipt is invalid.')

    @staticmethod
    def _result(result, actor, user_id):
        if not isinstance(result, ConstructionOrganizationRemovalResult):
            raise PlatformMembershipRemovalError('Organization removal result is invalid.')
        ConstructionOrganizationRemovalService._membership_result(result.membership, actor, user_id)
        for values in (result.revoked_grant_ids, result.grant_audit_ids):
            if (not isinstance(values, tuple) or any(not isinstance(value, UUID) for value in values)
                    or len(set(values)) != len(values)):
                raise PlatformMembershipRemovalError('Organization removal result is invalid.')
        if (result.revoked_grant_ids != tuple(sorted(result.revoked_grant_ids, key=lambda value: value.int))
                or len(result.revoked_grant_ids) != len(result.grant_audit_ids)
                or result.membership.audit_id in result.grant_audit_ids):
            raise PlatformMembershipRemovalError('Organization removal audit result is invalid.')
        return result

    async def _prepare_authority(self, *, actor, user_id, trace_id=None):
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
            raise PlatformMembershipRemovalDeniedError('Organization removal actor is unavailable.')
        if (not isinstance(user_id, UUID) or (trace_id is not None and (
                not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128))):
            raise ValueError('Organization removal request is invalid.')
        transaction = self._transaction()
        # Same actor/target order as every participating grant writer.
        receipts = []
        for member in sorted({actor.user_id, user_id}, key=lambda value: value.int):
            receipt = await self.serialization.lock_member(tenant_id=actor.tenant_id, user_id=member)
            self._lock_receipt(receipt, actor, member, transaction)
            receipts.append((member, receipt))
        # Concrete Platform authorizers prepare locked evidence BEFORE Core
        # takes its actor/target user locks. Legacy injected test authorizers
        # remain supported; the concrete one denies calls without preparation.
        prepare = getattr(self.service.authorization, 'prepare_removal', None)
        if callable(prepare):
            await prepare(actor=actor, target_user_id=user_id)
            for member, receipt in receipts:
                self._lock_receipt(receipt, actor, member, transaction)
        return transaction, receipts

    async def _mutate_prepared(self, *, actor, user_id, trace_id, prepared):
        transaction, receipts = prepared
        if self._transaction() is not transaction:
            raise PlatformMembershipRemovalError('Removal preparation transaction changed.')
        result = self._result(await self.service.remove_from_organization(
            actor=actor, user_id=user_id, trace_id=trace_id), actor, user_id)
        for member, receipt in receipts:
            self._lock_receipt(receipt, actor, member, transaction)
        return result

    async def remove_from_organization(self, *, actor, user_id, trace_id=None):
        try:
            prepared = await self._prepare_authority(actor=actor,user_id=user_id,trace_id=trace_id)
            result = await self._mutate_prepared(actor=actor,user_id=user_id,trace_id=trace_id,prepared=prepared)
            await self.session.commit()
            return result
        except (PlatformMembershipRemovalError, ValueError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise PlatformMembershipRemovalError('Organization removal transaction failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_organization_removal_workflow(*, session, authorization_service, now_provider=None):
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    return ConstructionOrganizationRemovalWorkflow(session=session,
        removal_service=build_construction_organization_removal_service(session=session,
            authorization_service=authorization_service, now_provider=now_provider),
        serialization_repository=PlatformMembershipSerializationRepository(session))
