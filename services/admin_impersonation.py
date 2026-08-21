from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from database.repositories.specialist import (
    SpecialistRepository,
)
from services.admin_specialists import (
    AdminSpecialistsService,
)
from services.moderation import ModerationService
from services.specialist import SpecialistService
from services.user import UserService


class AdminImpersonationAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class AdminImpersonationActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminImpersonationAction:
    actor: AdminImpersonationActor
    result: object


class AdminImpersonationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: ModerationService | None = None,
        profiles: SpecialistService | None = None,
        admin_specialists: AdminSpecialistsService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )
        self.profiles = (
            profiles
            or SpecialistService(
                SpecialistRepository(session)
            )
        )
        self.admin_specialists = (
            admin_specialists
            or AdminSpecialistsService(session)
        )

    async def require_super_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminImpersonationActor:
        user = await self.users.get_user_by_telegram_id(
            platform_user_id
        )

        if not user or user.tenant_id is None:
            raise AdminImpersonationAccessError(
                "Super Admin impersonation access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if "super_admin" not in roles:
            raise AdminImpersonationAccessError(
                "Super Admin impersonation access denied."
            )

        return AdminImpersonationActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_target_user(
        self,
        *,
        actor: AdminImpersonationActor,
        target_user_id: UUID,
    ):
        target = await self.users.get_user_by_id(
            target_user_id
        )

        if (
            not target
            or target.tenant_id is None
            or target.tenant_id != actor.tenant_id
        ):
            raise AdminImpersonationAccessError(
                "Impersonation target access denied."
            )

        return target

    async def start_view(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        target_role: str,
        reason: str,
    ) -> AdminImpersonationAction:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=target_user_id,
        )

        result = await (
            self.moderation
            .start_super_admin_impersonation_view(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
                target_role=target_role,
                reason=reason,
            )
        )

        return AdminImpersonationAction(
            actor=actor,
            result=result,
        )

    async def stop_view(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        reason: str,
    ) -> AdminImpersonationAction:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=target_user_id,
        )

        result = await (
            self.moderation
            .stop_super_admin_impersonation_view(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
                reason=reason,
            )
        )

        return AdminImpersonationAction(
            actor=actor,
            result=result,
        )

    async def get_client_cabinet(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        language: str,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=target_user_id,
        )

        return await (
            self.moderation.get_client_read_only_cabinet(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
                language=language,
            )
        )

    async def list_specialist_cabinets(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        language: str,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=target_user_id,
        )

        return await (
            self.moderation
            .list_specialist_read_only_cabinet_options(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
                language=language,
            )
        )

    async def get_specialist_cabinet(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        language: str,
        professional_cabinet_id: UUID | None,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=target_user_id,
        )

        return await (
            self.moderation
            .get_specialist_read_only_cabinet(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=target_user_id,
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

    async def get_specialist_profile(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        specialist_id: UUID,
        language: str,
        professional_cabinet_id: UUID | None,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=target_user_id,
        )

        return await self.profiles.get_active_cabinet_profile(
            tenant_id=actor.tenant_id,
            user_id=target_user_id,
            specialist_id=specialist_id,
            language=language,
            professional_cabinet_id=(
                professional_cabinet_id
            ),
        )

    async def open_admin_cabinet(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=target_user_id,
        )

        return await self.moderation.open_admin_menu(
            admin_user_id=target_user_id,
            tenant_id=actor.tenant_id,
        )

    async def require_moderator_preview(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        target_role: str,
    ) -> AdminImpersonationActor:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=target_user_id,
        )

        normalized_role = (
            target_role or ""
        ).strip().lower()

        if normalized_role not in {
            "moderator",
            "admin",
        }:
            raise AdminImpersonationAccessError(
                "Moderation preview access denied."
            )

        await (
            self.admin_specialists
            .require_impersonated_actor(
                platform_user_id=platform_user_id,
                effective_user_id=target_user_id,
                required_effective_roles={
                    normalized_role
                },
            )
        )

        return actor

    async def open_admin_specialists(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        status: str,
        page: int,
        page_size: int,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=effective_admin_user_id,
        )

        return await (
            self.admin_specialists
            .open_impersonated_admin_specialists(
                platform_user_id=platform_user_id,
                effective_admin_user_id=(
                    effective_admin_user_id
                ),
                status=status,
                page=page,
                page_size=page_size,
            )
        )

    async def open_moderator_queue(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        page: int,
        page_size: int,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=(
                effective_moderator_user_id
            ),
        )

        return await (
            self.admin_specialists
            .open_impersonated_moderator_queue(
                platform_user_id=platform_user_id,
                effective_moderator_user_id=(
                    effective_moderator_user_id
                ),
                page=page,
                page_size=page_size,
            )
        )

    async def get_admin_specialist(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        professional_cabinet_id: UUID,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=effective_admin_user_id,
        )

        return await (
            self.admin_specialists
            .get_impersonated_admin_specialist_card(
                platform_user_id=platform_user_id,
                effective_admin_user_id=(
                    effective_admin_user_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

    async def get_moderator_specialist(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        professional_cabinet_id: UUID,
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_target_user(
            actor=actor,
            target_user_id=(
                effective_moderator_user_id
            ),
        )

        return await (
            self.admin_specialists
            .get_impersonated_moderator_specialist_card(
                platform_user_id=platform_user_id,
                effective_moderator_user_id=(
                    effective_moderator_user_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )
