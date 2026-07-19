from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from database.repositories.event import EventRepository
from database.repositories.user import UserRepository
from database.models import SupportMessage, SupportTicket
from database.repositories.contact import ContactChatRepository
from database.repositories.support import (
    SUPPORT_MESSAGE_SENDER_ROLES,
    SUPPORT_STAFF_ROLES,
    SUPPORT_TICKET_CATEGORIES,
    SUPPORT_TICKET_STATUSES,
    SupportRepository,
)


SUPPORT_PRIORITY_ORDER = ("P1", "P2", "P3", "P4")
SUPPORT_TICKET_PRIORITIES = set(SUPPORT_PRIORITY_ORDER)
SUPPORT_TICKET_NON_REPLYABLE_STATUSES = frozenset(
    {
        "resolved",
        "closed",
        "rejected",
    }
)

class SupportServiceError(Exception):
    pass


@dataclass
class SupportTicketView:
    ticket: SupportTicket
    messages: list[SupportMessage]

    @property
    def can_reply(self) -> bool:
        return (
            self.ticket.status
            not in SUPPORT_TICKET_NON_REPLYABLE_STATUSES
        )

@dataclass(frozen=True)
class StaffMessageResult:
    message: SupportMessage
    recipient_chat_id: int | None

@dataclass(frozen=True)
class AdminEscalatedTicketPage:
    tickets: tuple[SupportTicket, ...]
    page: int
    has_next: bool

class SupportService:
    def __init__(self, repository: SupportRepository):
        self.repository = repository
        self.users = UserRepository(
            repository.session
        )
        self.events = EventRepository(
            repository.session
        )

    async def create_ticket(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        subject: str | None,
        priority: str,
        category: str | None,
        message_text: str,
    ) -> SupportTicket:
        priority = self._validate_priority(priority)
        category = self._validate_category(category)
        subject = self._normalize_optional_text(subject, max_length=200)
        message_text = self._validate_message_text(message_text)

        ticket = await self.repository.create_ticket(
            tenant_id=tenant_id,
            user_id=user_id,
            subject=subject,
            priority=priority,
            category=category,
            message_text=message_text,
        )
        await self.repository.session.commit()
        return ticket

    async def list_user_tickets(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        statuses: set[str] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SupportTicket]:
        normalized_statuses = None
        if statuses is not None:
            normalized_statuses = {
                self._validate_status(status)
                for status in statuses
            }

        return await self.repository.list_user_tickets(
            tenant_id=tenant_id,
            user_id=user_id,
            statuses=normalized_statuses,
            limit=limit,
            offset=offset,
        )

    async def list_admin_escalated_tickets(
        self,
        *,
        tenant_id: UUID,
        admin_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminEscalatedTicketPage:
        has_access = (
            await self.repository
            .user_has_admin_support_access(
                user_id=admin_user_id,
                tenant_id=tenant_id,
            )
        )

        if not has_access:
            raise SupportServiceError(
                "Admin access denied."
            )

        normalized_page = max(int(page), 0)
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        tickets = (
            await self.repository
            .list_admin_escalated_tickets(
                tenant_id=tenant_id,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )

        visible_tickets = tickets[:normalized_page_size]
        has_next = len(tickets) > normalized_page_size

        await self.repository.log_admin_ticket_event(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
            ticket_id=None,
            action="escalated_list_opened",
            payload={
                "page": normalized_page,
                "visible_count": len(visible_tickets),
                "has_next": has_next,
            },
        )

        await self.repository.session.commit()

        return AdminEscalatedTicketPage(
            tickets=tuple(visible_tickets),
            page=normalized_page,
            has_next=has_next,
        )

    async def list_staff_tickets(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        statuses: set[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SupportTicket]:
        await self._ensure_staff_access(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
        )

        normalized_statuses = None
        if statuses is not None:
            normalized_statuses = {
                self._validate_status(status)
                for status in statuses
            }

        return await self.repository.list_staff_tickets(
            tenant_id=tenant_id,
            statuses=normalized_statuses,
            limit=limit,
            offset=offset,
        )

    async def search_staff_tickets(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        query: str,
        limit: int = 5,
        offset: int = 0,
    ) -> list[SupportTicket]:
        await self._ensure_staff_access(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
        )

        search_query = (query or "").strip().lstrip("#")
        if len(search_query) < 2:
            raise SupportServiceError("Search query must be at least 2 characters.")
        if len(search_query) > 100:
            raise SupportServiceError("Search query is too long.")

        return await self.repository.search_staff_tickets(
            tenant_id=tenant_id,
            query=search_query,
            limit=limit,
            offset=offset,
        )

    async def get_staff_ticket_counts(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        statuses: set[str] | None = None,
    ) -> dict[str, int]:
        await self._ensure_staff_access(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
        )

        normalized_statuses = None
        if statuses is not None:
            normalized_statuses = {
                self._validate_status(status)
                for status in statuses
            }

        return await self.repository.get_staff_ticket_counts(
            tenant_id=tenant_id,
            statuses=normalized_statuses,
        )

    async def get_staff_ticket_stats(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
    ) -> dict:
        await self._ensure_staff_access(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
        )

        return await self.repository.get_staff_ticket_stats(
            tenant_id=tenant_id,
        )

    async def get_user_ticket_view(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        ticket_id: UUID,
    ) -> SupportTicketView:
        ticket = await self.repository.get_user_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            user_id=user_id,
        )
        if not ticket:
            raise SupportServiceError("Support ticket not found.")

        messages = await self.repository.list_ticket_messages(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            include_internal=False,
        )
        return SupportTicketView(ticket=ticket, messages=messages)

    async def get_staff_ticket_view(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        ticket_id: UUID,
    ) -> SupportTicketView:
        await self._ensure_staff_access(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
        )

        ticket = await self.repository.get_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
        )
        if not ticket or ticket.tenant_id != tenant_id:
            raise SupportServiceError("Support ticket not found.")

        messages = await self.repository.list_ticket_messages(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            include_internal=True,
        )
        return SupportTicketView(ticket=ticket, messages=messages)

    async def close_user_ticket(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        ticket_id: UUID,
    ) -> SupportTicket:
        ticket = await self.repository.get_user_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            user_id=user_id,
        )
        if not ticket:
            raise SupportServiceError("Support ticket not found.")

        if ticket.status in {"resolved", "closed", "rejected"}:
            raise SupportServiceError("Support ticket is already closed.")

        ticket = await self.repository.update_ticket_status(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            status="closed",
            assigned_user_id=None,
        )
        if not ticket:
            raise SupportServiceError("Support ticket not found.")

        await self.repository.session.commit()
        return ticket

    async def add_user_message(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        ticket_id: UUID,
        message_text: str,
    ) -> SupportMessage:
        ticket = await self.repository.get_user_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            user_id=user_id,
        )
        if not ticket:
            raise SupportServiceError("Support ticket not found.")

        if ticket.status in {"resolved", "closed", "rejected"}:
            raise SupportServiceError("Support ticket is already closed.")

        message = await self.repository.add_message(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            sender_user_id=user_id,
            sender_role="user",
            message_text=self._validate_message_text(message_text),
            is_internal=False,
        )
        await self.repository.session.commit()
        return message

    async def add_staff_message(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        ticket_id: UUID,
        message_text: str,
        is_internal: bool = False,
    ) -> StaffMessageResult:
        await self._ensure_staff_access(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
        )

        ticket = await self.repository.get_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
        )
        if not ticket or ticket.tenant_id != tenant_id:
            raise SupportServiceError(
                "Support ticket not found."
            )

        if ticket.status in {
            "resolved",
            "closed",
            "rejected",
        }:
            raise SupportServiceError(
                "Support ticket is already closed."
            )

        validated_message = self._validate_message_text(
            message_text
        )

        message = await self.repository.add_message(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            sender_user_id=staff_user_id,
            sender_role="support",
            message_text=validated_message,
            is_internal=is_internal,
        )

        account = (
            await self.users.get_telegram_account_by_user_id(
                ticket.user_id
            )
        )

        await self.events.create_event(
            event_type="reply",
            tenant_id=tenant_id,
            user_id=staff_user_id,
            entity_type="support_ticket",
            entity_id=ticket_id,
            payload={
                "source": "support_staff",
                "message_length": len(validated_message),
            },
            platform="telegram",
        )

        await self.repository.session.commit()

        return StaffMessageResult(
            message=message,
            recipient_chat_id=self._parse_telegram_chat_id(
                (
                    account.platform_user_id
                    if account
                    else None
                )
            ),
        )
    
    async def update_ticket_status(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        ticket_id: UUID,
        status: str,
    ) -> SupportTicket:
        await self._ensure_staff_access(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
        )

        status = self._validate_status(status)
        ticket = await self.repository.update_ticket_status(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            status=status,
            assigned_user_id=staff_user_id,
        )
        if not ticket:
            raise SupportServiceError("Support ticket not found.")

        await self.repository.session.commit()
        return ticket

    async def get_admin_escalated_ticket_view(
        self,
        *,
        tenant_id: UUID,
        admin_user_id: UUID,
        ticket_id: UUID,
    ) -> SupportTicketView:
        await self._ensure_admin_support_access(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
        )

        ticket = await self.repository.get_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
        )

        if (
            not ticket
            or ticket.tenant_id != tenant_id
            or ticket.priority != "P1"
        ):
            raise SupportServiceError(
                "Escalated support ticket not found."
            )

        messages = await self.repository.list_ticket_messages(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            include_internal=True,
        )

        await self.repository.log_admin_ticket_event(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
            ticket_id=ticket.id,
            action="opened",
            payload={
                "status": ticket.status,
                "priority": ticket.priority,
            },
        )

        await self.repository.session.commit()

        return SupportTicketView(
            ticket=ticket,
            messages=messages,
        )

    async def assign_admin_escalated_ticket(
        self,
        *,
        tenant_id: UUID,
        admin_user_id: UUID,
        ticket_id: UUID,
        reason: str,
    ) -> SupportTicket:
        await self._ensure_admin_support_access(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
        )

        normalized_reason = self._validate_admin_reason(
            reason
        )

        ticket = await self.repository.get_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
        )

        if (
            not ticket
            or ticket.tenant_id != tenant_id
            or ticket.priority != "P1"
        ):
            raise SupportServiceError(
                "Escalated support ticket not found."
            )

        if ticket.status in {
            "resolved",
            "closed",
            "rejected",
        }:
            raise SupportServiceError(
                "Support ticket is already closed."
            )

        before_status = ticket.status

        ticket = await self.repository.update_ticket_status(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            status="in_progress",
            assigned_user_id=admin_user_id,
        )

        if not ticket:
            raise SupportServiceError(
                "Support ticket not found."
            )

        await self.repository.add_message(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            sender_user_id=admin_user_id,
            sender_role="admin",
            message_text=(
                f"Assigned by Admin: {normalized_reason}"
            ),
            is_internal=True,
        )

        await self.repository.log_admin_ticket_event(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
            ticket_id=ticket.id,
            action="assigned",
            payload={
                "before_status": before_status,
                "after_status": ticket.status,
                "reason": normalized_reason,
            },
        )

        await self.repository.session.commit()
        return ticket

    async def resolve_admin_escalated_ticket(
        self,
        *,
        tenant_id: UUID,
        admin_user_id: UUID,
        ticket_id: UUID,
        reason: str,
    ) -> SupportTicket:
        await self._ensure_admin_support_access(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
        )

        normalized_reason = self._validate_admin_reason(
            reason
        )

        ticket = await self.repository.get_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
        )

        if (
            not ticket
            or ticket.tenant_id != tenant_id
            or ticket.priority != "P1"
        ):
            raise SupportServiceError(
                "Escalated support ticket not found."
            )

        if ticket.status in {
            "resolved",
            "closed",
            "rejected",
        }:
            raise SupportServiceError(
                "Support ticket is already closed."
            )

        before_status = ticket.status
        contact_repository = ContactChatRepository(
            self.repository.session
        )

        try:
            contact_request = (
                await contact_repository
                .get_contact_request_by_escalated_ticket_id(
                    tenant_id=tenant_id,
                    support_ticket_id=ticket_id,
                )
            )

            if contact_request is not None:
                if contact_request.status != "accepted":
                    raise SupportServiceError(
                        "Escalated contact request "
                        "is no longer accepted."
                    )

                thread = (
                    await contact_repository
                    .get_thread_by_contact_request_id(
                        contact_request.id
                    )
                )
                if not thread:
                    raise SupportServiceError(
                        "Conversation thread not found."
                    )

                await contact_repository.complete_contact_request_by_admin(
                    contact_request=contact_request,
                    thread=thread,
                    admin_user_id=admin_user_id,
                    reason=normalized_reason,
                    completed_at=datetime.utcnow(),
                )

            await self.repository.add_message(
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                sender_user_id=admin_user_id,
                sender_role="admin",
                message_text=(
                    f"Resolved by Admin: "
                    f"{normalized_reason}"
                ),
                is_internal=True,
            )

            ticket = (
                await self.repository.update_ticket_status(
                    tenant_id=tenant_id,
                    ticket_id=ticket_id,
                    status="resolved",
                    assigned_user_id=admin_user_id,
                )
            )

            if not ticket:
                raise SupportServiceError(
                    "Support ticket not found."
                )

            await self.repository.log_admin_ticket_event(
                tenant_id=tenant_id,
                admin_user_id=admin_user_id,
                ticket_id=ticket.id,
                action="resolved",
                payload={
                    "before_status": before_status,
                    "after_status": ticket.status,
                    "reason": normalized_reason,
                    "contact_request_id": (
                        str(contact_request.id)
                        if contact_request is not None
                        else None
                    ),
                },
            )

            await self.repository.session.commit()
            return ticket

        except SupportServiceError:
            await self.repository.session.rollback()
            raise
        except Exception as exc:
            await self.repository.session.rollback()
            raise SupportServiceError(
                "Failed to resolve escalated ticket."
            ) from exc
        
    async def assign_ticket(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        ticket_id: UUID,
    ) -> SupportTicket:
        return await self.update_ticket_status(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
            ticket_id=ticket_id,
            status="in_progress",
        )

    async def escalate_ticket_to_admin(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        ticket_id: UUID,
        reason: str,
    ) -> SupportTicket:
        await self._ensure_staff_access(
            tenant_id=tenant_id,
            staff_user_id=staff_user_id,
        )

        reason_text = self._validate_message_text(reason)

        ticket = await self.repository.get_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
        )
        if not ticket:
            raise SupportServiceError("Support ticket not found.")

        if ticket.status in {"resolved", "closed", "rejected"}:
            raise SupportServiceError("Support ticket is already closed.")

        ticket = await self.repository.update_ticket_priority(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            priority="P1",
        )
        if not ticket:
            raise SupportServiceError("Support ticket not found.")

        await self.repository.add_message(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            sender_user_id=staff_user_id,
            sender_role="support",
            message_text=f"Escalated to admin: {reason_text}",
            is_internal=True,
        )

        await self.repository.session.commit()
        return ticket

    async def _ensure_staff_access(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
    ) -> None:
        has_access = await self.repository.user_has_support_access(
            tenant_id=tenant_id,
            user_id=staff_user_id,
        )
        if not has_access:
            raise SupportServiceError("Support access denied.")

    async def _ensure_admin_support_access(
        self,
        *,
        tenant_id: UUID,
        admin_user_id: UUID,
    ) -> None:
        has_access = (
            await self.repository
            .user_has_admin_support_access(
                user_id=admin_user_id,
                tenant_id=tenant_id,
            )
        )

        if not has_access:
            raise SupportServiceError(
                "Admin access denied."
            )

    @staticmethod
    def _validate_admin_reason(
        reason: str | None,
    ) -> str:
        normalized_reason = (reason or "").strip()

        if len(normalized_reason) < 3:
            raise SupportServiceError(
                "Reason must contain at least 3 characters."
            )

        if len(normalized_reason) > 500:
            raise SupportServiceError(
                "Reason is too long."
            )

        return normalized_reason

    @staticmethod
    def _validate_priority(priority: str | None) -> str:
        value = (priority or "P3").strip().upper()
        if value not in SUPPORT_TICKET_PRIORITIES:
            raise SupportServiceError("Unsupported support priority.")
        return value

    @staticmethod
    def _validate_status(status: str | None) -> str:
        value = (status or "").strip()
        if value not in SUPPORT_TICKET_STATUSES:
            raise SupportServiceError("Unsupported support ticket status.")
        return value

    @staticmethod
    def _validate_category(category: str | None) -> str | None:
        value = (category or "").strip()
        if not value:
            return None
        if value not in SUPPORT_TICKET_CATEGORIES:
            raise SupportServiceError("Unsupported support category.")
        return value

    @staticmethod
    def _validate_sender_role(sender_role: str | None) -> str:
        value = (sender_role or "").strip()
        if value not in SUPPORT_MESSAGE_SENDER_ROLES:
            raise SupportServiceError("Unsupported support sender role.")
        return value

    @staticmethod
    def _normalize_optional_text(
        value: str | None,
        *,
        max_length: int,
    ) -> str | None:
        text = (value or "").strip()
        if not text:
            return None
        if len(text) > max_length:
            raise SupportServiceError("Text is too long.")
        return text

    @staticmethod
    def _parse_telegram_chat_id(
        platform_user_id: str | None,
    ) -> int | None:
        if not platform_user_id:
            return None

        try:
            return int(platform_user_id)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _validate_message_text(message_text: str | None) -> str:
        text = (message_text or "").strip()
        if not text:
            raise SupportServiceError("Message text is required.")
        if len(text) > 4000:
            raise SupportServiceError("Message text is too long.")
        return text
