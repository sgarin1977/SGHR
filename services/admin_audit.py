from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import (
    AdminAuditCard,
    AdminAuditPage,
    ModerationService,
    SuperAdminAuditEventDetailCard,
)
from services.user import UserService


class AdminAuditAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminAuditActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


class AdminAuditService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: (
            ModerationService | None
        ) = None,
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
    ) -> AdminAuditActor:
        user = (
            await self.users
            .get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminAuditAccessError(
                "Administrative audit "
                "access denied."
            )

        roles = (
            await self.moderation
            .get_admin_roles(
                user.id,
                tenant_id=user.tenant_id,
            )
        )

        if not roles:
            raise AdminAuditAccessError(
                "Administrative audit "
                "access denied."
            )

        return AdminAuditActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_regional_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminAuditActor:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        if not actor.roles.intersection(
            {"admin", "super_admin"}
        ):
            raise AdminAuditAccessError(
                "Regional audit access denied."
            )

        return actor

    async def require_super_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminAuditActor:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        if "super_admin" not in actor.roles:
            raise AdminAuditAccessError(
                "Global audit access denied."
            )

        return actor

    async def require_impersonated_admin(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
    ) -> AdminAuditActor:
        actor = (
            await self
            .require_super_admin_actor(
                platform_user_id=(
                    platform_user_id
                )
            )
        )

        effective_roles = (
            await self.moderation
            .get_admin_roles(
                effective_admin_user_id,
                tenant_id=actor.tenant_id,
            )
        )

        if "admin" not in effective_roles:
            raise AdminAuditAccessError(
                "Impersonated Admin audit "
                "access denied."
            )

        return actor

    async def open_impersonated_admin_audit(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        target_type: str,
        page: int,
        page_size: int = 5,
    ) -> AdminAuditPage:
        actor = (
            await self
            .require_impersonated_admin(
                platform_user_id=(
                    platform_user_id
                ),
                effective_admin_user_id=(
                    effective_admin_user_id
                ),
            )
        )

        return await (
            self.moderation.open_admin_audit(
                admin_user_id=(
                    effective_admin_user_id
                ),
                tenant_id=actor.tenant_id,
                target_type=target_type,
                page=page,
                page_size=page_size,
            )
        )

    async def get_impersonated_admin_audit_card(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        action_id: UUID,
    ) -> AdminAuditCard:
        actor = (
            await self
            .require_impersonated_admin(
                platform_user_id=(
                    platform_user_id
                ),
                effective_admin_user_id=(
                    effective_admin_user_id
                ),
            )
        )

        return await (
            self.moderation.get_admin_audit_card(
                admin_user_id=(
                    effective_admin_user_id
                ),
                tenant_id=actor.tenant_id,
                action_id=action_id,
            )
        )

    async def open_regional_audit(
        self,
        *,
        platform_user_id: int | str,
        target_type: str,
        page: int,
        page_size: int = 5,
    ) -> AdminAuditPage:
        actor = (
            await self.require_regional_actor(
                platform_user_id=(
                    platform_user_id
                )
            )
        )

        return await (
            self.moderation.open_admin_audit(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_type=target_type,
                page=page,
                page_size=page_size,
            )
        )

    async def get_regional_audit_card(
        self,
        *,
        platform_user_id: int | str,
        action_id: UUID,
    ) -> AdminAuditCard:
        actor = (
            await self.require_regional_actor(
                platform_user_id=(
                    platform_user_id
                )
            )
        )

        return await (
            self.moderation.get_admin_audit_card(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                action_id=action_id,
            )
        )

    async def open_super_admin_audit(
        self,
        *,
        platform_user_id: int | str,
        target_type: str,
        page: int,
        page_size: int = 5,
    ) -> AdminAuditPage:
        actor = (
            await self
            .require_super_admin_actor(
                platform_user_id=(
                    platform_user_id
                )
            )
        )

        return await (
            self.moderation
            .open_super_admin_audit(
                admin_user_id=actor.user_id,
                target_type=target_type,
                page=page,
                page_size=page_size,
            )
        )

    async def get_super_admin_audit_detail(
        self,
        *,
        platform_user_id: int | str,
        action_id: UUID,
    ) -> SuperAdminAuditEventDetailCard:
        actor = (
            await self
            .require_super_admin_actor(
                platform_user_id=(
                    platform_user_id
                )
            )
        )

        return await (
            self.moderation
            .get_super_admin_audit_event_detail(
                admin_user_id=actor.user_id,
                action_id=action_id,
            )
        )
