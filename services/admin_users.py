from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import ModerationService
from services.user import UserService


class AdminUsersAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminUsersActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


class AdminUsersService:
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

    @staticmethod
    def normalize_search_query(
        query: str,
    ) -> str:
        normalized_query = str(
            query or ""
        ).strip()

        if normalized_query.startswith("@"):
            normalized_query = (
                normalized_query[1:].strip()
            )

        return normalized_query

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
        required_roles: set[str],
    ) -> AdminUsersActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminUsersAccessError(
                "Administrative user access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(required_roles):
            raise AdminUsersAccessError(
                "Administrative user access denied."
            )

        return AdminUsersActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_regional_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminUsersActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"admin", "super_admin"},
        )

    async def require_super_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminUsersActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"super_admin"},
        )

    async def require_impersonated_admin(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
    ) -> AdminUsersActor:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        effective_roles = (
            await self.moderation.get_admin_roles(
                effective_admin_user_id,
                tenant_id=actor.tenant_id,
            )
        )

        if "admin" not in effective_roles:
            raise AdminUsersAccessError(
                "Impersonated Admin user access denied."
            )

        return actor

    async def search_regional_users(
        self,
        *,
        platform_user_id: int | str,
        query: str,
    ):
        actor = await self.require_regional_actor(
            platform_user_id=platform_user_id
        )
        normalized_query = (
            self.normalize_search_query(
                query
            )
        )
        if len(normalized_query) < 2:
            return []


        return await self.moderation.search_admin_users(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            query=normalized_query,
        )

    async def get_regional_user_details(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
    ):
        actor = await self.require_regional_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation.get_admin_user_details(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
            )
        )

    async def list_regional_user_history(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        limit: int = 10,
    ):
        actor = await self.require_regional_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation.list_admin_user_history(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
                limit=limit,
            )
        )

    async def search_super_admin_users(
        self,
        *,
        platform_user_id: int | str,
        query: str,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        normalized_query = (
            self.normalize_search_query(
                query
            )
        )
        if len(normalized_query) < 2:
            return []


        return await (
            self.moderation.search_super_admin_users(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                query=normalized_query,
            )
        )

    async def get_super_admin_user_details(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .get_super_admin_user_details(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
            )
        )

    async def list_super_admin_user_roles(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .list_super_admin_user_roles(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
            )
        )

    async def search_impersonated_admin_users(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        query: str,
    ):
        actor = await self.require_impersonated_admin(
            platform_user_id=platform_user_id,
            effective_admin_user_id=(
                effective_admin_user_id
            ),
        )
        normalized_query = (
            self.normalize_search_query(
                query
            )
        )
        if len(normalized_query) < 2:
            return []


        return await self.moderation.search_admin_users(
            admin_user_id=effective_admin_user_id,
            tenant_id=actor.tenant_id,
            query=normalized_query,
        )

    async def get_impersonated_admin_user_details(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        target_user_id: UUID,
    ):
        actor = await self.require_impersonated_admin(
            platform_user_id=platform_user_id,
            effective_admin_user_id=(
                effective_admin_user_id
            ),
        )

        return await (
            self.moderation.get_admin_user_details(
                admin_user_id=effective_admin_user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
            )
        )

    async def list_impersonated_admin_user_history(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        target_user_id: UUID,
        limit: int = 10,
    ):
        actor = await self.require_impersonated_admin(
            platform_user_id=platform_user_id,
            effective_admin_user_id=(
                effective_admin_user_id
            ),
        )

        return await (
            self.moderation.list_admin_user_history(
                admin_user_id=effective_admin_user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
                limit=limit,
            )
        )
