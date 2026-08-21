from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.event import (
    EventRepository,
)
from database.repositories.moderation import (
    ModerationRepository,
)
from database.repositories.support import (
    SupportRepository,
)
from services.moderation import ModerationService
from services.support import (
    SupportService,
    SupportServiceError,
)
from services.user import UserService


class AdminSupportAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminSupportActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminSupportMenuResult:
    actor: AdminSupportActor
    counts: dict[str, int]
    show_role_switch: bool


@dataclass(frozen=True)
class AdminSupportSearchResult:
    actor: AdminSupportActor
    tickets: tuple[object, ...]
    query: str


@dataclass(frozen=True)
class AdminSupportPageResult:
    actor: AdminSupportActor
    tickets: tuple[object, ...]
    page: int
    has_next: bool


@dataclass(frozen=True)
class AdminSupportAction:
    actor: AdminSupportActor
    result: object


class AdminSupportService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: ModerationService | None = None,
        support: SupportService | None = None,
        events: EventRepository | None = None,
        audit: ModerationRepository | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )
        self.support = (
            support
            or SupportService(
                SupportRepository(session)
            )
        )
        self.events = events or EventRepository(
            session
        )
        self.audit = audit or ModerationRepository(
            session
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
        required_roles: set[str],
    ) -> AdminSupportActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminSupportAccessError(
                "Support access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(required_roles):
            raise AdminSupportAccessError(
                "Support access denied."
            )

        return AdminSupportActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_staff_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminSupportActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"support"},
        )

    async def require_stats_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminSupportActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={
                "support",
                "admin",
                "super_admin",
            },
        )

    async def require_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminSupportActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={
                "admin",
                "super_admin",
            },
        )

    async def list_admin_escalated_tickets(
        self,
        *,
        platform_user_id: int | str,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_admin_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.support
            .list_admin_escalated_tickets(
                tenant_id=actor.tenant_id,
                admin_user_id=actor.user_id,
                page=page,
                page_size=page_size,
            )
        )

    async def get_admin_escalated_ticket(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID,
    ):
        actor = await self.require_admin_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.support
            .get_admin_escalated_ticket_view(
                tenant_id=actor.tenant_id,
                admin_user_id=actor.user_id,
                ticket_id=ticket_id,
            )
        )

    async def execute_admin_action(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID,
        action: str,
        reason: str,
    ) -> AdminSupportAction:
        actor = await self.require_admin_actor(
            platform_user_id=platform_user_id
        )
        normalized_action = (
            action or ""
        ).strip().lower()

        if normalized_action == "assign":
            result = await (
                self.support
                .assign_admin_escalated_ticket(
                    tenant_id=actor.tenant_id,
                    admin_user_id=actor.user_id,
                    ticket_id=ticket_id,
                    reason=reason,
                )
            )
        elif normalized_action == "resolve":
            result = await (
                self.support
                .resolve_admin_escalated_ticket(
                    tenant_id=actor.tenant_id,
                    admin_user_id=actor.user_id,
                    ticket_id=ticket_id,
                    reason=reason,
                )
            )
        else:
            raise SupportServiceError(
                "Unsupported admin support action."
            )

        return AdminSupportAction(
            actor=actor,
            result=result,
        )

    async def require_super_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminSupportActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"super_admin"},
        )

    async def require_impersonated_actor(
        self,
        *,
        platform_user_id: int | str,
        effective_user_id: UUID,
        effective_role: str,
    ) -> AdminSupportActor:
        normalized_role = (
            effective_role or ""
        ).strip().lower()

        if normalized_role not in {
            "admin",
            "support",
        }:
            raise AdminSupportAccessError(
                "Impersonated Support access denied."
            )

        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        effective_roles = await (
            self.moderation.get_admin_roles(
                effective_user_id,
                tenant_id=actor.tenant_id,
            )
        )

        if normalized_role not in effective_roles:
            raise AdminSupportAccessError(
                "Impersonated Support access denied."
            )

        return actor

    async def open_impersonated_support_cabinet(
        self,
        *,
        platform_user_id: int | str,
        effective_staff_user_id: UUID,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=effective_staff_user_id,
            effective_role="support",
        )

        return await (
            self.moderation
            .get_support_read_only_cabinet(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                target_user_id=(
                    effective_staff_user_id
                ),
            )
        )

    async def list_impersonated_admin_tickets(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=effective_admin_user_id,
            effective_role="admin",
        )

        return await (
            self.support.list_admin_escalated_tickets(
                tenant_id=actor.tenant_id,
                admin_user_id=effective_admin_user_id,
                page=page,
                page_size=page_size,
            )
        )

    async def get_impersonated_admin_ticket(
        self,
        *,
        platform_user_id: int | str,
        effective_admin_user_id: UUID,
        ticket_id: UUID,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=effective_admin_user_id,
            effective_role="admin",
        )

        return await (
            self.support.get_admin_escalated_ticket_view(
                tenant_id=actor.tenant_id,
                admin_user_id=effective_admin_user_id,
                ticket_id=ticket_id,
            )
        )

    async def list_impersonated_staff_tickets(
        self,
        *,
        platform_user_id: int | str,
        effective_staff_user_id: UUID,
        statuses: set[str],
        view: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminSupportPageResult:
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=effective_staff_user_id,
            effective_role="support",
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 20),
        )

        tickets = await self.support.list_staff_tickets(
            tenant_id=actor.tenant_id,
            staff_user_id=effective_staff_user_id,
            statuses=statuses,
            limit=normalized_page_size + 1,
            offset=(
                normalized_page
                * normalized_page_size
            ),
        )
        visible_tickets = tuple(
            tickets[:normalized_page_size]
        )

        return AdminSupportPageResult(
            actor=actor,
            tickets=visible_tickets,
            page=normalized_page,
            has_next=(
                len(tickets) > normalized_page_size
            ),
        )

    async def get_impersonated_staff_ticket(
        self,
        *,
        platform_user_id: int | str,
        effective_staff_user_id: UUID,
        ticket_id: UUID,
    ):
        actor = await self.require_impersonated_actor(
            platform_user_id=platform_user_id,
            effective_user_id=effective_staff_user_id,
            effective_role="support",
        )

        return await self.support.get_staff_ticket_view(
            tenant_id=actor.tenant_id,
            staff_user_id=effective_staff_user_id,
            ticket_id=ticket_id,
        )

    async def open_staff_menu(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminSupportMenuResult:
        actor = await self.require_staff_actor(
            platform_user_id=platform_user_id
        )
        counts = await (
            self.support.get_staff_ticket_counts(
                tenant_id=actor.tenant_id,
                staff_user_id=actor.user_id,
                statuses={
                    "open",
                    "in_progress",
                    "resolved",
                },
            )
        )
        role_context = await (
            self.users.get_role_switch_context(
                platform_user_id
            )
        )
        show_role_switch = bool(
            role_context
            and len(role_context.available_roles) > 1
        )

        await self.events.create_event(
            event_type="support_menu",
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            entity_type="support_ticket",
            entity_id=None,
            payload={
                "source": "support_staff_menu",
                "counts": counts,
            },
            platform="telegram",
        )
        await self.session.commit()

        return AdminSupportMenuResult(
            actor=actor,
            counts=counts,
            show_role_switch=show_role_switch,
        )

    async def search_staff_tickets(
        self,
        *,
        platform_user_id: int | str,
        query: str,
        limit: int = 5,
    ) -> AdminSupportSearchResult:
        actor = await self.require_staff_actor(
            platform_user_id=platform_user_id
        )
        normalized_query = (query or "").strip()

        tickets = await (
            self.support.search_staff_tickets(
                tenant_id=actor.tenant_id,
                staff_user_id=actor.user_id,
                query=normalized_query,
                limit=limit,
                offset=0,
            )
        )

        await self.events.create_event(
            event_type="ticket_search",
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            entity_type="support_ticket",
            entity_id=None,
            payload={
                "query": normalized_query,
                "count": len(tickets),
            },
            platform="telegram",
        )
        await self.session.commit()

        return AdminSupportSearchResult(
            actor=actor,
            tickets=tuple(tickets),
            query=normalized_query,
        )

    async def list_staff_tickets(
        self,
        *,
        platform_user_id: int | str,
        statuses: set[str],
        view: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminSupportPageResult:
        actor = await self.require_staff_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 20),
        )

        tickets = await (
            self.support.list_staff_tickets(
                tenant_id=actor.tenant_id,
                staff_user_id=actor.user_id,
                statuses=statuses,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )
        visible_tickets = tuple(
            tickets[:normalized_page_size]
        )
        has_next = (
            len(tickets) > normalized_page_size
        )

        await self.events.create_event(
            event_type="ticket_list",
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            entity_type="support_ticket",
            entity_id=None,
            payload={
                "source": "support_staff",
                "view": view,
                "page": normalized_page,
                "count": len(visible_tickets),
                "has_next": has_next,
            },
            platform="telegram",
        )
        await self.session.commit()

        return AdminSupportPageResult(
            actor=actor,
            tickets=visible_tickets,
            page=normalized_page,
            has_next=has_next,
        )

    async def get_staff_ticket(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID,
    ):
        actor = await self.require_staff_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.support.get_staff_ticket_view(
                tenant_id=actor.tenant_id,
                staff_user_id=actor.user_id,
                ticket_id=ticket_id,
            )
        )

    async def assign_staff_ticket(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID,
    ) -> AdminSupportAction:
        actor = await self.require_staff_actor(
            platform_user_id=platform_user_id
        )
        ticket = await self.support.assign_ticket(
            tenant_id=actor.tenant_id,
            staff_user_id=actor.user_id,
            ticket_id=ticket_id,
        )

        await self.audit.log_event(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            event_type="ticket_assigned",
            entity_type="support_ticket",
            entity_id=ticket.id,
            payload={
                "status": ticket.status,
            },
        )
        await self.session.commit()

        return AdminSupportAction(
            actor=actor,
            result=ticket,
        )

    async def add_staff_reply(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID,
        message_text: str,
    ) -> AdminSupportAction:
        actor = await self.require_staff_actor(
            platform_user_id=platform_user_id
        )
        result = await self.support.add_staff_message(
            tenant_id=actor.tenant_id,
            staff_user_id=actor.user_id,
            ticket_id=ticket_id,
            message_text=message_text,
        )

        return AdminSupportAction(
            actor=actor,
            result=result,
        )

    async def update_staff_ticket_status(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID,
        status: str,
    ) -> AdminSupportAction:
        actor = await self.require_staff_actor(
            platform_user_id=platform_user_id
        )
        ticket = await (
            self.support.update_ticket_status(
                tenant_id=actor.tenant_id,
                staff_user_id=actor.user_id,
                ticket_id=ticket_id,
                status=status,
            )
        )

        await self.audit.log_admin_action(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            action_type=(
                f"support_ticket_{status}"
            ),
            target_type="support_ticket",
            target_id=ticket_id,
            before_state={},
            after_state={
                "status": ticket.status,
            },
            reason=(
                "support ticket status changed "
                "from Telegram admin panel"
            ),
        )
        await self.audit.log_event(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            event_type=(
                f"support_ticket_{status}"
            ),
            entity_type="support_ticket",
            entity_id=ticket_id,
            payload={
                "status": status,
            },
        )

        if status == "resolved":
            await self.audit.log_event(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                event_type="resolved",
                entity_type="support_ticket",
                entity_id=ticket_id,
                payload={
                    "source": "support_staff",
                    "status": status,
                },
            )

        await self.session.commit()

        return AdminSupportAction(
            actor=actor,
            result=ticket,
        )

    async def escalate_staff_ticket(
        self,
        *,
        platform_user_id: int | str,
        ticket_id: UUID,
        reason: str,
    ) -> AdminSupportAction:
        actor = await self.require_staff_actor(
            platform_user_id=platform_user_id
        )
        ticket = await (
            self.support.escalate_ticket_to_admin(
                tenant_id=actor.tenant_id,
                staff_user_id=actor.user_id,
                ticket_id=ticket_id,
                reason=reason,
            )
        )

        await self.audit.log_event(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            event_type="ticket_escalated",
            entity_type="support_ticket",
            entity_id=ticket.id,
            payload={
                "priority": ticket.priority,
            },
        )
        await self.session.commit()

        return AdminSupportAction(
            actor=actor,
            result=ticket,
        )

    async def get_staff_stats(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminSupportAction:
        actor = await self.require_stats_actor(
            platform_user_id=platform_user_id
        )
        stats = await (
            self.support.get_staff_ticket_stats(
                tenant_id=actor.tenant_id,
                staff_user_id=actor.user_id,
            )
        )

        await self.events.create_event(
            event_type="stats_viewed",
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            entity_type="support_ticket",
            entity_id=None,
            payload={
                "source": "support_staff_stats",
            },
            platform="telegram",
        )
        await self.session.commit()

        return AdminSupportAction(
            actor=actor,
            result=stats,
        )
