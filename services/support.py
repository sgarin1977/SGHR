from dataclasses import dataclass
from uuid import UUID

from database.models import SupportMessage, SupportTicket
from database.repositories.support import (
    SUPPORT_MESSAGE_SENDER_ROLES,
    SUPPORT_STAFF_ROLES,
    SUPPORT_TICKET_CATEGORIES,
    SUPPORT_TICKET_STATUSES,
    SupportRepository,
)


SUPPORT_PRIORITY_ORDER = ("P1", "P2", "P3", "P4")
SUPPORT_TICKET_PRIORITIES = set(SUPPORT_PRIORITY_ORDER)


class SupportServiceError(Exception):
    pass


@dataclass
class SupportTicketView:
    ticket: SupportTicket
    messages: list[SupportMessage]


class SupportService:
    def __init__(self, repository: SupportRepository):
        self.repository = repository

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
    async def list_staff_tickets(
        self,
        *,
        tenant_id: UUID,
        staff_user_id: UUID,
        statuses: set[str] | None = None,
        limit: int = 20,
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
    ) -> SupportMessage:
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

        if ticket.status in {"resolved", "closed", "rejected"}:
            raise SupportServiceError("Support ticket is already closed.")

        role = "support"
        message = await self.repository.add_message(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            sender_user_id=staff_user_id,
            sender_role=role,
            message_text=self._validate_message_text(message_text),
            is_internal=is_internal,
        )
        await self.repository.session.commit()
        return message

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
    def _validate_message_text(message_text: str | None) -> str:
        text = (message_text or "").strip()
        if not text:
            raise SupportServiceError("Message text is required.")
        if len(text) > 4000:
            raise SupportServiceError("Message text is too long.")
        return text
