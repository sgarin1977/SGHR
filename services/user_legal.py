from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.repositories.event import (
    EventRepository,
)
from database.repositories.legal import (
    LegalRepository,
)
from services.legal import LegalService
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


class UserLegalAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class UserLegalActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class UserLegalResult:
    actor: UserLegalActor
    documents: tuple[Any, ...]


class UserLegalService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: (
            UserSettingsService | None
        ) = None,
        legal: LegalService | None = None,
        events: EventRepository | None = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.legal = (
            legal
            or LegalService(
                LegalRepository(session)
            )
        )
        self.events = (
            events
            or EventRepository(session)
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserLegalActor:
        try:
            context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError as exc:
            raise UserLegalAccessError(
                "Legal user not found."
            ) from exc

        return UserLegalActor(
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            language=(
                context.interface_language
            ),
        )

    async def get_start_context(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserLegalActor:
        return await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

    async def start_specialist_gate(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserLegalResult:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        try:
            await self.events.create_event(
                event_type=(
                    "registration_started"
                ),
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type=(
                    "specialist_registration"
                ),
                entity_id=None,
                payload={
                    "source": (
                        "specialist_start"
                    ),
                },
                platform="telegram",
            )
            await self.session.commit()

        except Exception:
            await self.session.rollback()
            raise

        documents = await (
            self.legal
            .get_missing_specialist_consents(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                language=actor.language,
            )
        )

        return UserLegalResult(
            actor=actor,
            documents=tuple(documents),
        )

    async def list_specialist_documents(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserLegalResult:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        documents = await (
            self.legal
            .get_missing_specialist_consents(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                language=actor.language,
            )
        )

        return UserLegalResult(
            actor=actor,
            documents=tuple(documents),
        )

    async def accept_specialist_gate(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserLegalActor:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        await (
            self.legal
            .accept_required_specialist_consents(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                language=actor.language,
                platform="telegram",
            )
        )

        return actor
