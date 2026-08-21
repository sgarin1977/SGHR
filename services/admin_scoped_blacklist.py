from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import ModerationService
from services.user import UserService


class AdminScopedBlacklistAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class AdminScopedBlacklistActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminScopedBlacklistAction:
    actor: AdminScopedBlacklistActor
    result: object


class AdminScopedBlacklistService:
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
        required_roles: set[str],
    ) -> AdminScopedBlacklistActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminScopedBlacklistAccessError(
                "Scoped blacklist access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(required_roles):
            raise AdminScopedBlacklistAccessError(
                "Scoped blacklist access denied."
            )

        return AdminScopedBlacklistActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_moderator_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminScopedBlacklistActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={
                "admin",
                "moderator",
                "super_admin",
            },
        )

    async def require_super_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminScopedBlacklistActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"super_admin"},
        )

    async def require_impersonated_actor(
        self,
        *,
        platform_user_id: int | str,
        effective_user_id: UUID,
    ) -> AdminScopedBlacklistActor:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        effective_roles = (
            await self.moderation.get_admin_roles(
                effective_user_id,
                tenant_id=actor.tenant_id,
            )
        )

        if not effective_roles.intersection(
            {"admin", "moderator"}
        ):
            raise AdminScopedBlacklistAccessError(
                "Impersonated scoped blacklist "
                "access denied."
            )

        return actor

    async def open_queue(
        self,
        *,
        platform_user_id: int | str,
        view: str,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .open_scoped_blacklist_queue(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                view=view,
                page=page,
                page_size=page_size,
            )
        )

    async def add_by_telegram_id(
        self,
        *,
        platform_user_id: int | str,
        telegram_id: str,
        reason: str,
    ) -> AdminScopedBlacklistAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await (
            self.moderation
            .add_scoped_blacklist_by_telegram_id(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                telegram_id=telegram_id,
                reason=reason,
            )
        )

        return AdminScopedBlacklistAction(
            actor=actor,
            result=result,
        )

    async def add_specialist_owner(
        self,
        *,
        platform_user_id: int | str,
        specialist_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> AdminScopedBlacklistAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await (
            self.moderation
            .add_specialist_owner_scoped_blacklist(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                specialist_id=specialist_id,
                reason=reason,
                comment=comment,
            )
        )

        return AdminScopedBlacklistAction(
            actor=actor,
            result=result,
        )

    async def add_complaint_target(
        self,
        *,
        platform_user_id: int | str,
        complaint_id: UUID,
        reason: str,
    ) -> AdminScopedBlacklistAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await (
            self.moderation
            .add_complaint_target_scoped_blacklist(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                complaint_id=complaint_id,
                reason=reason,
            )
        )

        return AdminScopedBlacklistAction(
            actor=actor,
            result=result,
        )

    async def revoke(
        self,
        *,
        platform_user_id: int | str,
        blacklist_id: UUID,
        reason: str,
    ) -> AdminScopedBlacklistAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await (
            self.moderation.revoke_scoped_blacklist(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                blacklist_id=blacklist_id,
                reason=reason,
            )
        )

        return AdminScopedBlacklistAction(
            actor=actor,
            result=result,
        )

    async def open_impersonated_queue(
        self,
        *,
        platform_user_id: int | str,
        effective_user_id: UUID,
        view: str,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=effective_user_id,
        )

        return await (
            self.moderation
            .open_scoped_blacklist_queue(
                moderator_user_id=effective_user_id,
                tenant_id=actor.tenant_id,
                view=view,
                page=page,
                page_size=page_size,
            )
        )
