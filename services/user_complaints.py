from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.repositories.moderation import (
    ModerationRepository,
)
from services.moderation import (
    ModerationService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


class UserComplaintsAccessError(
    PermissionError
):
    pass


class UserComplaintsSelectionError(
    ValueError
):
    pass


@dataclass(frozen=True)
class UserComplaintsActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class UserComplaintTarget:
    actor: UserComplaintsActor
    target_type: str
    target_id: UUID
    conversation_thread_id: UUID


@dataclass(frozen=True)
class UserComplaintAction:
    actor: UserComplaintsActor
    complaint: Any


class UserComplaintsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: (
            UserSettingsService | None
        ) = None,
        moderation: (
            ModerationService | None
        ) = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )

    @staticmethod
    def parse_id(
        value: UUID | str,
        *,
        field: str,
    ) -> UUID:
        try:
            return (
                value
                if isinstance(value, UUID)
                else UUID(str(value))
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise (
                UserComplaintsSelectionError(
                    f"Invalid {field}."
                )
            ) from exc

    @classmethod
    def parse_optional_id(
        cls,
        value: UUID | str | None,
        *,
        field: str,
    ) -> UUID | None:
        if value is None:
            return None

        return cls.parse_id(
            value,
            field=field,
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserComplaintsActor:
        try:
            context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError as exc:
            raise (
                UserComplaintsAccessError(
                    "Complaint reporter not found."
                )
            ) from exc

        return UserComplaintsActor(
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            language=(
                context.interface_language
            ),
        )

    async def resolve_thread_target(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID | str,
    ) -> UserComplaintTarget:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_thread_id = self.parse_id(
            thread_id,
            field="complaint thread",
        )

        (
            target_type,
            target_id,
            conversation_thread_id,
        ) = await (
            self.moderation
            .resolve_thread_complaint_target(
                tenant_id=actor.tenant_id,
                reporter_user_id=(
                    actor.user_id
                ),
                thread_id=parsed_thread_id,
            )
        )

        return UserComplaintTarget(
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            conversation_thread_id=(
                conversation_thread_id
            ),
        )

    async def create_complaint(
        self,
        *,
        platform_user_id: int | str,
        target_type: str,
        target_id: UUID | str,
        reason: str,
        comment: str | None = None,
        conversation_thread_id: (
            UUID | str | None
        ) = None,
    ) -> UserComplaintAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_target_id = self.parse_id(
            target_id,
            field="complaint target",
        )
        parsed_thread_id = (
            self.parse_optional_id(
                conversation_thread_id,
                field="conversation thread",
            )
        )

        complaint = await (
            self.moderation.create_complaint(
                tenant_id=actor.tenant_id,
                reporter_user_id=(
                    actor.user_id
                ),
                target_type=target_type,
                target_id=parsed_target_id,
                reason=reason,
                comment=comment,
                conversation_thread_id=(
                    parsed_thread_id
                ),
            )
        )

        await self.moderation.confirm_complaint(
            reporter_user_id=actor.user_id,
            complaint_id=complaint.id,
        )

        return UserComplaintAction(
            actor=actor,
            complaint=complaint,
        )
