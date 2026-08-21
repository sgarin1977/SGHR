from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import ModerationService
from services.user import UserService


class AdminSpecialistsAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminSpecialistsActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminSpecialistsAction:
    actor: AdminSpecialistsActor
    result: object


class AdminSpecialistsService:
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
    ) -> AdminSpecialistsActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminSpecialistsAccessError(
                "Specialist moderation access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(required_roles):
            raise AdminSpecialistsAccessError(
                "Specialist moderation access denied."
            )

        return AdminSpecialistsActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminSpecialistsActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"admin", "super_admin"},
        )

    async def require_moderator_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminSpecialistsActor:
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
    ) -> AdminSpecialistsActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"super_admin"},
        )

    async def require_impersonated_actor(
        self,
        *,
        platform_user_id: int | str,
        effective_user_id: UUID,
        required_effective_roles: set[str],
    ) -> AdminSpecialistsActor:
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
            required_effective_roles
        ):
            raise AdminSpecialistsAccessError(
                "Impersonated specialist "
                "moderation access denied."
            )

        return actor

    async def open_admin_specialists(
        self,
        *,
        platform_user_id: int | str,
        status: str = "approved",
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_admin_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation.open_admin_specialists(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                status=status,
                page=page,
                page_size=page_size,
            )
        )

    async def open_pending_specialists(
        self,
        *,
        platform_user_id: int | str,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .open_pending_specialists_queue(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                page=page,
                page_size=page_size,
            )
        )

    async def get_specialist_card(
        self,
        *,
        platform_user_id: int | str,
        specialist_id: UUID | None = None,
        professional_cabinet_id: UUID | None = None,
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .get_moderator_specialist_card(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                specialist_id=specialist_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

    async def open_impersonated_admin_specialists(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        status: str = "approved",
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=effective_admin_user_id,
            required_effective_roles={"admin"},
        )

        return await (
            self.moderation.open_admin_specialists(
                admin_user_id=effective_admin_user_id,
                tenant_id=actor.tenant_id,
                status=status,
                page=page,
                page_size=page_size,
            )
        )

    async def open_impersonated_moderator_queue(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=(
                effective_moderator_user_id
            ),
            required_effective_roles={"moderator"},
        )

        return await (
            self.moderation
            .open_pending_specialists_queue(
                moderator_user_id=(
                    effective_moderator_user_id
                ),
                tenant_id=actor.tenant_id,
                page=page,
                page_size=page_size,
            )
        )

    async def get_impersonated_admin_specialist_card(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        professional_cabinet_id: UUID,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=effective_admin_user_id,
            required_effective_roles={"admin"},
        )

        return await (
            self.moderation
            .get_moderator_specialist_card(
                moderator_user_id=(
                    effective_admin_user_id
                ),
                tenant_id=actor.tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

    async def get_impersonated_moderator_specialist_card(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        professional_cabinet_id: UUID,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=(
                effective_moderator_user_id
            ),
            required_effective_roles={"moderator"},
        )

        return await (
            self.moderation
            .get_moderator_specialist_card(
                moderator_user_id=(
                    effective_moderator_user_id
                ),
                tenant_id=actor.tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )


    async def approve_specialist(
        self,
        *,
        platform_user_id: int | str,
        reason: str,
        specialist_id: UUID | None = None,
        professional_cabinet_id: UUID | None = None,
    ) -> AdminSpecialistsAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await self.moderation.approve_specialist(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            reason=reason,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                professional_cabinet_id
            ),
        )
        return AdminSpecialistsAction(
            actor=actor,
            result=result,
        )


    async def reject_specialist(
        self,
        *,
        platform_user_id: int | str,
        reason: str,
        specialist_id: UUID | None = None,
        professional_cabinet_id: UUID | None = None,
    ) -> AdminSpecialistsAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await self.moderation.reject_specialist(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            reason=reason,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                professional_cabinet_id
            ),
        )
        return AdminSpecialistsAction(
            actor=actor,
            result=result,
        )


    async def request_specialist_changes(
        self,
        *,
        platform_user_id: int | str,
        reason: str,
        specialist_id: UUID | None = None,
        professional_cabinet_id: UUID | None = None,
    ) -> AdminSpecialistsAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await (
            self.moderation
            .request_specialist_changes(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                reason=reason,
                specialist_id=specialist_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )
        return AdminSpecialistsAction(
            actor=actor,
            result=result,
        )


    async def hide_professional_cabinet(
        self,
        *,
        platform_user_id: int | str,
        professional_cabinet_id: UUID,
        reason: str,
    ) -> AdminSpecialistsAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await (
            self.moderation
            .hide_professional_cabinet(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                reason=reason,
            )
        )
        return AdminSpecialistsAction(
            actor=actor,
            result=result,
        )


    async def restore_professional_cabinet(
        self,
        *,
        platform_user_id: int | str,
        professional_cabinet_id: UUID,
        reason: str,
    ) -> AdminSpecialistsAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await (
            self.moderation
            .restore_professional_cabinet(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                reason=reason,
            )
        )
        return AdminSpecialistsAction(
            actor=actor,
            result=result,
        )
