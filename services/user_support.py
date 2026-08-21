from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.repositories.event import (
    EventRepository,
)
from database.repositories.support import (
    SUPPORT_TICKET_CATEGORIES,
    SupportRepository,
)
from services.support import (
    SupportService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


class UserSupportAccessError(
    PermissionError
):
    pass


class UserSupportSelectionError(
    ValueError
):
    pass


@dataclass(frozen=True)
class UserSupportActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class UserSupportAction:
    actor: UserSupportActor
    result: Any


@dataclass(frozen=True)
class UserSupportPage:
    actor: UserSupportActor
    items: tuple[Any, ...]
    view: str
    page: int
    has_next: bool


class UserSupportService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: (
            UserSettingsService | None
        ) = None,
        events: EventRepository | None = None,
        support: SupportService | None = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.events = (
            events
            or EventRepository(session)
        )
        self.support = (
            support
            or SupportService(
                SupportRepository(session)
            )
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserSupportActor:
        try:
            context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError as exc:
            raise UserSupportAccessError(
                "Support user not found."
            ) from exc

        return UserSupportActor(
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            language=(
                context.interface_language
            ),
        )

    async def record_event(
        self,
        *,
        actor: UserSupportActor,
        event_type: str,
        entity_type: str,
        entity_id: UUID | None = None,
        payload: dict | None = None,
    ) -> None:
        try:
            await self.events.create_event(
                event_type=event_type,
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload or {},
                platform="telegram",
            )
            await self.session.commit()

        except Exception:
            await self.session.rollback()
            raise

    async def open_menu(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserSupportActor:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        await self.record_event(
            actor=actor,
            event_type="support_opened",
            entity_type="support",
            payload={
                "source": "support_menu",
            },
        )

        return actor

    async def select_category(
        self,
        *,
        platform_user_id: int | str,
        category: str,
    ) -> UserSupportActor:
        normalized_category = (
            category or ""
        ).strip()

        if (
            normalized_category
            not in SUPPORT_TICKET_CATEGORIES
        ):
            raise UserSupportSelectionError(
                "Invalid support category."
            )

        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        await self.record_event(
            actor=actor,
            event_type="ticket_category",
            entity_type="support_ticket",
            payload={
                "category": (
                    normalized_category
                ),
            },
        )

        return actor

    async def create_ticket(
        self,
        *,
        platform_user_id: int | str,
        category: str | None,
        priority: str,
        message_text: str,
    ) -> UserSupportAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        ticket = await self.support.create_ticket(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            subject=None,
            priority=priority,
            category=category,
            message_text=message_text,
        )

        await self.record_event(
            actor=actor,
            event_type="ticket_created",
            entity_type="support_ticket",
            entity_id=ticket.id,
            payload={
                "category": category,
                "priority": priority,
            },
        )

        return UserSupportAction(
            actor=actor,
            result=ticket,
        )

    async def list_tickets(
        self,
        *,
        platform_user_id: int | str,
        view: str = "active",
        page: int | str = 0,
        page_size: int | str = 5,
    ) -> UserSupportPage:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        normalized_view = (
            "resolved"
            if view == "resolved"
            else "active"
        )

        try:
            normalized_page = max(
                0,
                int(page),
            )
            normalized_size = max(
                1,
                min(
                    int(page_size),
                    10,
                ),
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise UserSupportSelectionError(
                "Invalid support ticket page."
            ) from exc

        statuses = (
            {
                "resolved",
                "closed",
                "rejected",
            }
            if normalized_view == "resolved"
            else {
                "open",
                "in_progress",
            }
        )

        tickets = await (
            self.support.list_user_tickets(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                statuses=statuses,
                limit=normalized_size + 1,
                offset=(
                    normalized_page
                    * normalized_size
                ),
            )
        )

        visible_items = tuple(
            tickets[:normalized_size]
        )
        has_next = (
            len(tickets) > normalized_size
        )

        await self.record_event(
            actor=actor,
            event_type="ticket_list",
            entity_type="support_ticket",
            payload={
                "view": normalized_view,
                "page": normalized_page,
                "count": len(
                    visible_items
                ),
                "has_next": has_next,
            },
        )

        return UserSupportPage(
            actor=actor,
            items=visible_items,
            view=normalized_view,
            page=normalized_page,
            has_next=has_next,
        )

    @staticmethod
    def parse_ticket_id(
        ticket_id: UUID | str,
    ) -> UUID:
        try:
            return (
                ticket_id
                if isinstance(ticket_id, UUID)
                else UUID(str(ticket_id))
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise UserSupportSelectionError(
                "Invalid support ticket."
            ) from exc

    async def get_ticket(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID | str,
    ) -> UserSupportAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        view = await (
            self.support.get_user_ticket_view(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                ticket_id=(
                    self.parse_ticket_id(
                        ticket_id
                    )
                ),
            )
        )

        return UserSupportAction(
            actor=actor,
            result=view,
        )

    async def close_ticket(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID | str,
    ) -> UserSupportAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_ticket_id = (
            self.parse_ticket_id(
                ticket_id
            )
        )

        ticket = await (
            self.support.close_user_ticket(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                ticket_id=parsed_ticket_id,
            )
        )

        await self.record_event(
            actor=actor,
            event_type="closed",
            entity_type="support_ticket",
            entity_id=ticket.id,
            payload={
                "source": (
                    "user_support_ticket"
                ),
                "status": "closed",
            },
        )

        return UserSupportAction(
            actor=actor,
            result=ticket,
        )

    async def reply_to_ticket(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID | str,
        message_text: str,
    ) -> UserSupportAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_ticket_id = (
            self.parse_ticket_id(
                ticket_id
            )
        )

        support_message = await (
            self.support.add_user_message(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                ticket_id=parsed_ticket_id,
                message_text=message_text,
            )
        )

        await self.record_event(
            actor=actor,
            event_type="ticket_message",
            entity_type="support_ticket",
            entity_id=parsed_ticket_id,
            payload={
                "sender_role": "user",
            },
        )

        return UserSupportAction(
            actor=actor,
            result=support_message,
        )
