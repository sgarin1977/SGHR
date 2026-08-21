from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import ModerationService
from services.user import UserService


class AdminComplaintsAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminComplaintsActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminComplaintsAction:
    actor: AdminComplaintsActor
    result: object


class AdminComplaintsService:
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
    ) -> AdminComplaintsActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminComplaintsAccessError(
                "Complaint moderation access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(required_roles):
            raise AdminComplaintsAccessError(
                "Complaint moderation access denied."
            )

        return AdminComplaintsActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_moderator_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminComplaintsActor:
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
    ) -> AdminComplaintsActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"super_admin"},
        )

    async def require_impersonated_moderator(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
    ) -> AdminComplaintsActor:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        effective_roles = (
            await self.moderation.get_admin_roles(
                effective_moderator_user_id,
                tenant_id=actor.tenant_id,
            )
        )

        if not effective_roles.intersection(
            {"admin", "moderator"}
        ):
            raise AdminComplaintsAccessError(
                "Impersonated complaint "
                "moderation access denied."
            )

        return actor

    async def open_complaints_queue(
        self,
        *,
        platform_user_id: int | str,
        statuses: set[str],
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await self.moderation.open_complaints_queue(
            moderator_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            statuses=statuses,
            page=page,
            page_size=page_size,
        )

    async def get_complaint_card(
        self,
        *,
        platform_user_id: int | str,
        complaint_id: UUID,
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.moderation
            .get_moderator_complaint_card(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                complaint_id=complaint_id,
            )
        )

    async def take_complaint(
        self,
        *,
        platform_user_id: int | str,
        complaint_id: UUID,
    ) -> AdminComplaintsAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await self.moderation.take_complaint(
            moderator_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            complaint_id=complaint_id,
        )

        return AdminComplaintsAction(
            actor=actor,
            result=result,
        )

    async def escalate_complaint(
        self,
        *,
        platform_user_id: int | str,
        complaint_id: UUID,
        reason: str,
    ) -> AdminComplaintsAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await (
            self.moderation
            .escalate_complaint_to_admin(
                moderator_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                complaint_id=complaint_id,
                reason=reason,
            )
        )

        return AdminComplaintsAction(
            actor=actor,
            result=result,
        )

    async def resolve_complaint(
        self,
        *,
        platform_user_id: int | str,
        complaint_id: UUID,
        status: str,
        reason: str,
    ) -> AdminComplaintsAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await self.moderation.resolve_complaint(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            complaint_id=complaint_id,
            status=status,
            reason=reason,
        )

        return AdminComplaintsAction(
            actor=actor,
            result=result,
        )

    async def open_impersonated_complaints_queue(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        statuses: set[str],
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await (
            self.require_impersonated_moderator(
                platform_user_id=platform_user_id,
                effective_moderator_user_id=(
                    effective_moderator_user_id
                ),
            )
        )

        return await self.moderation.open_complaints_queue(
            moderator_user_id=(
                effective_moderator_user_id
            ),
            tenant_id=actor.tenant_id,
            statuses=statuses,
            page=page,
            page_size=page_size,
        )

    async def get_impersonated_complaint_card(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        complaint_id: UUID,
    ):
        actor = await (
            self.require_impersonated_moderator(
                platform_user_id=platform_user_id,
                effective_moderator_user_id=(
                    effective_moderator_user_id
                ),
            )
        )

        return await (
            self.moderation
            .get_moderator_complaint_card(
                moderator_user_id=(
                    effective_moderator_user_id
                ),
                tenant_id=actor.tenant_id,
                complaint_id=complaint_id,
            )
        )
