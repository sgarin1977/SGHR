from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import ModerationService
from services.user import UserService


class SuperAdminGlobalBlacklistAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class SuperAdminGlobalBlacklistActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class SuperAdminGlobalBlacklistAction:
    actor: SuperAdminGlobalBlacklistActor
    result: object


class SuperAdminGlobalBlacklistService:
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

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> SuperAdminGlobalBlacklistActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise (
                SuperAdminGlobalBlacklistAccessError(
                    "Global blacklist access denied."
                )
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if "super_admin" not in roles:
            raise (
                SuperAdminGlobalBlacklistAccessError(
                    "Global blacklist access denied."
                )
            )

        return SuperAdminGlobalBlacklistActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def open_queue(
        self,
        *,
        platform_user_id: int | str,
        view: str,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .open_super_admin_global_blacklist_queue(
                admin_user_id=actor.user_id,
                view=view,
                page=page,
                page_size=page_size,
            )
        )

    async def block_user(
        self,
        *,
        platform_user_id: int | str,
        user_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> SuperAdminGlobalBlacklistAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        result = await self.moderation.block_user(
            admin_user_id=actor.user_id,
            user_id=user_id,
            reason=reason,
            comment=comment,
        )

        return SuperAdminGlobalBlacklistAction(
            actor=actor,
            result=result,
        )

    async def unblock_user(
        self,
        *,
        platform_user_id: int | str,
        user_id: UUID,
        reason: str,
    ) -> SuperAdminGlobalBlacklistAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        result = await self.moderation.unblock_user(
            admin_user_id=actor.user_id,
            user_id=user_id,
            reason=reason,
        )

        return SuperAdminGlobalBlacklistAction(
            actor=actor,
            result=result,
        )
