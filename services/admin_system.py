from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import ModerationService
from services.user import UserService


class AdminSystemAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminSystemActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


class AdminSystemService:
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
    ) -> AdminSystemActor:
        user = await self.users.get_user_by_telegram_id(
            platform_user_id
        )

        if not user or user.tenant_id is None:
            raise AdminSystemAccessError(
                "Super Admin system access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if "super_admin" not in roles:
            raise AdminSystemAccessError(
                "Super Admin system access denied."
            )

        return AdminSystemActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def list_smoke_definitions(
        self,
        *,
        platform_user_id: int | str,
    ):
        await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return (
            self.moderation
            .list_super_admin_smoke_definitions()
        )

    async def run_smoke_tests(
        self,
        *,
        platform_user_id: int | str,
        selected_code: str | None = None,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .run_super_admin_smoke_tests(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                selected_code=selected_code,
            )
        )

    async def list_smoke_history(
        self,
        *,
        platform_user_id: int | str,
        limit: int = 5,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .list_super_admin_smoke_history(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                limit=limit,
            )
        )

    async def get_system_status(
        self,
        *,
        platform_user_id: int | str,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .open_super_admin_system_status(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
            )
        )
