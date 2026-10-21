"""Platform membership storage primitives for an authorized removal operation.

This is not a public removal entry point. The future Platform service and
Construction orchestration own authorization, mandatory audit, grant revocation,
postconditions and the single commit. No user, role or scope is deleted here.
Existing Platform tables remain the only membership model.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import exists, select, update

from database.models import RoleScope, User, UserRoleMapping


class PlatformMembershipRepository:
    def __init__(self, session):
        if (session is None or not callable(getattr(session, 'execute', None))
                or not callable(getattr(session, 'in_transaction', None))):
            raise ValueError('Platform membership requires a transaction-capable session.')
        self.session = session

    def _context(self, *identifiers):
        if any(not isinstance(value, UUID) for value in identifiers):
            raise ValueError('Platform membership requires UUID references.')
        if not self.session.in_transaction():
            raise ValueError('Platform membership requires an existing caller transaction.')

    async def _one(self, statement):
        with self.session.no_autoflush:
            return (await self.session.execute(statement)).scalar_one_or_none()

    async def _many(self, statement):
        with self.session.no_autoflush:
            rows = (await self.session.execute(statement)).scalars().all()
        return tuple(sorted(rows, key=lambda row: row.id.int))

    async def get_user_for_update(self, *, user_id):
        """Refresh and lock the canonical user, including inactive users."""
        self._context(user_id)
        return await self._one(select(User).where(User.id == user_id)
            .execution_options(populate_existing=True, autoflush=False).with_for_update())

    async def list_tenant_roles_for_update(self, *, user_id, tenant_id):
        self._context(user_id, tenant_id)
        if await self.get_user_for_update(user_id=user_id) is None:
            return ()
        return await self._many(select(UserRoleMapping).where(
            UserRoleMapping.user_id == user_id, UserRoleMapping.tenant_id == tenant_id,
        ).order_by(UserRoleMapping.id).execution_options(
            populate_existing=True, autoflush=False).with_for_update())

    async def revoke_active_tenant_roles(self, *, user_id, tenant_id):
        """Revoke all active roles in this tenant; preserve global/other roles."""
        self._context(user_id, tenant_id)
        if await self.get_user_for_update(user_id=user_id) is None:
            return ()
        return await self._many(update(UserRoleMapping).where(
            UserRoleMapping.user_id == user_id, UserRoleMapping.tenant_id == tenant_id,
            UserRoleMapping.status == 'active',
        ).values(status='revoked').returning(UserRoleMapping).execution_options(
            synchronize_session=False, populate_existing=True, autoflush=False))

    async def revoke_active_scopes_of_revoked_tenant_roles(
        self, *, user_id, tenant_id, revoked_by_user_id, as_of,
    ):
        """After role revocation, end only its matching tenant-local scopes.

        This also permits repairing an active scope of an already revoked parent
        without touching scopes of suspended, global or other-tenant roles.
        revoked_at uses naive UTC because the canonical RoleScope column does.
        """
        self._context(user_id, tenant_id, revoked_by_user_id)
        if (not isinstance(as_of, datetime) or as_of.tzinfo is None
                or as_of.utcoffset() is None):
            raise ValueError('Scope revocation requires an aware server timestamp.')
        instant = as_of.astimezone(timezone.utc).replace(tzinfo=None)
        if await self.get_user_for_update(user_id=user_id) is None:
            return ()
        parent = exists(select(UserRoleMapping.id).where(
            UserRoleMapping.id == RoleScope.user_role_id,
            UserRoleMapping.user_id == user_id, UserRoleMapping.tenant_id == tenant_id,
            UserRoleMapping.role == RoleScope.role, UserRoleMapping.status == 'revoked',
        ).correlate(RoleScope))
        return await self._many(update(RoleScope).where(
            RoleScope.user_id == user_id, RoleScope.tenant_id == tenant_id,
            RoleScope.status == 'active', parent,
        ).values(status='revoked', revoked_by=revoked_by_user_id, revoked_at=instant)
            .returning(RoleScope).execution_options(
                synchronize_session=False, populate_existing=True, autoflush=False))

    async def clear_primary_tenant_if_matches(self, *, user_id, tenant_id):
        self._context(user_id, tenant_id)
        if await self.get_user_for_update(user_id=user_id) is None:
            return None
        return await self._one(update(User).where(
            User.id == user_id, User.tenant_id == tenant_id,
        ).values(tenant_id=None).returning(User).execution_options(
            synchronize_session=False, populate_existing=True, autoflush=False))

    async def clear_invalid_active_role(self, *, user_id):
        """Retain the selection if any active assignment still supports it.

        A matching role in another tenant or a global assignment is sufficient;
        no alternative active role or primary organization is chosen implicitly.
        """
        self._context(user_id)
        if await self.get_user_for_update(user_id=user_id) is None:
            return None
        supported = exists(select(UserRoleMapping.id).where(
            UserRoleMapping.user_id == User.id,
            UserRoleMapping.role == User.active_role,
            UserRoleMapping.status == 'active',
        ).correlate(User))
        return await self._one(update(User).where(
            User.id == user_id, User.active_role.is_not(None), ~supported,
        ).values(active_role=None).returning(User).execution_options(
            synchronize_session=False, populate_existing=True, autoflush=False))
