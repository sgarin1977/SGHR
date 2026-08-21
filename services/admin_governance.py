from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import ModerationService
from services.user import UserService


class AdminGovernanceAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminGovernanceActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


class AdminGovernanceService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: ModerationService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )

    async def require_super_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminGovernanceActor:
        user = await self.users.get_user_by_telegram_id(
            platform_user_id
        )

        if not user or user.tenant_id is None:
            raise AdminGovernanceAccessError(
                "Super Admin governance access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if "super_admin" not in roles:
            raise AdminGovernanceAccessError(
                "Super Admin governance access denied."
            )

        return AdminGovernanceActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def list_role_scopes(
        self,
        *,
        platform_user_id: int | str,
        user_id: UUID | None,
        view: str,
        page: int,
        page_size: int = 5,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await self.moderation.open_super_admin_role_scopes(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            user_id=user_id,
            view=view,
            page=page,
            page_size=page_size,
        )

    async def list_permission_matrix(
        self,
        *,
        platform_user_id: int | str,
        query: str = "",
        limit: int = 10,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await self.moderation.list_super_admin_permission_matrix(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            query=query,
            limit=limit,
        )

    async def grant_role_permission(
        self,
        *,
        platform_user_id: int | str,
        role: str,
        permission_code: str,
        reason: str,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await self.moderation.grant_super_admin_permission(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            role=role,
            permission_code=permission_code,
            reason=reason,
        )

    async def revoke_role_permission(
        self,
        *,
        platform_user_id: int | str,
        role: str,
        permission_code: str,
        reason: str,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await self.moderation.revoke_super_admin_permission(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            role=role,
            permission_code=permission_code,
            reason=reason,
        )
