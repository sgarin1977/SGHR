from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SupportMessage, SupportTicket, UserRoleMapping


SUPPORT_TICKET_STATUSES = {
    "open",
    "in_progress",
    "resolved",
    "closed",
    "rejected",
}

SUPPORT_TICKET_PRIORITIES = {
    "P1",
    "P2",
    "P3",
    "P4",
}

SUPPORT_TICKET_CATEGORIES = {
    "account",
    "specialist_profile",
    "payment",
    "translation",
    "complaint",
    "technical",
    "other",
}

SUPPORT_MESSAGE_SENDER_ROLES = {
    "user",
    "support",
    "admin",
    "system",
}

SUPPORT_STAFF_ROLES = {
    "support",
    "admin",
    "super_admin",
}


class SupportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def user_has_support_access(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        result = await self.session.execute(
            select(UserRoleMapping.id)
            .where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.status == "active",
                UserRoleMapping.role.in_(SUPPORT_STAFF_ROLES),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

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
        now = datetime.utcnow()

        ticket = SupportTicket(
            tenant_id=tenant_id,
            user_id=user_id,
            subject=subject,
            priority=priority,
            category=category,
            status="open",
            last_message_at=now,
            updated_at=now,
        )
        self.session.add(ticket)
        await self.session.flush()

        message = SupportMessage(
            tenant_id=tenant_id,
            ticket_id=ticket.id,
            sender_user_id=user_id,
            sender_role="user",
            message_text=message_text,
            is_internal=False,
            created_at=now,
        )
        self.session.add(message)
        await self.session.flush()

        return ticket

    async def get_ticket(
        self,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
    ) -> SupportTicket | None:
        return await self.session.get(SupportTicket, ticket_id)

    async def get_user_ticket(
        self,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
        user_id: UUID,
    ) -> SupportTicket | None:
        result = await self.session.execute(
            select(SupportTicket).where(
                SupportTicket.tenant_id == tenant_id,
                SupportTicket.id == ticket_id,
                SupportTicket.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_user_tickets(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        limit: int = 10,
    ) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .where(
                SupportTicket.tenant_id == tenant_id,
                SupportTicket.user_id == user_id,
            )
            .order_by(
                SupportTicket.last_message_at.desc().nullslast(),
                SupportTicket.created_at.desc(),
            )
            .limit(max(1, min(int(limit), 50)))
        )
        return list(result.scalars().all())

    async def list_staff_tickets(
        self,
        *,
        tenant_id: UUID,
        statuses: set[str] | None = None,
        limit: int = 20,
    ) -> list[SupportTicket]:
        allowed_statuses = statuses or {"open", "in_progress"}

        result = await self.session.execute(
            select(SupportTicket)
            .where(
                SupportTicket.tenant_id == tenant_id,
                SupportTicket.status.in_(allowed_statuses),
            )
            .order_by(
                SupportTicket.priority.asc(),
                SupportTicket.last_message_at.desc().nullslast(),
                SupportTicket.created_at.desc(),
            )
            .limit(max(1, min(int(limit), 100)))
        )
        return list(result.scalars().all())

    async def list_ticket_messages(
        self,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
        include_internal: bool = False,
        limit: int = 20,
    ) -> list[SupportMessage]:
        stmt = (
            select(SupportMessage)
            .where(
                SupportMessage.tenant_id == tenant_id,
                SupportMessage.ticket_id == ticket_id,
            )
            .order_by(SupportMessage.created_at.asc())
            .limit(max(1, min(int(limit), 100)))
        )

        if not include_internal:
            stmt = stmt.where(SupportMessage.is_internal.is_(False))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add_message(
        self,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
        sender_user_id: UUID,
        sender_role: str,
        message_text: str,
        is_internal: bool = False,
    ) -> SupportMessage:
        now = datetime.utcnow()

        message = SupportMessage(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            sender_user_id=sender_user_id,
            sender_role=sender_role,
            message_text=message_text,
            is_internal=is_internal,
            created_at=now,
        )
        self.session.add(message)

        ticket = await self.session.get(SupportTicket, ticket_id)
        if ticket:
            ticket.last_message_at = now
            ticket.updated_at = now
            if ticket.status == "open" and sender_role in {"support", "admin"}:
                ticket.status = "in_progress"

        await self.session.flush()
        return message

    async def update_ticket_status(
        self,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
        status: str,
        assigned_user_id: UUID | None = None,
    ) -> SupportTicket | None:
        ticket = await self.get_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
        )
        if not ticket or ticket.tenant_id != tenant_id:
            return None

        now = datetime.utcnow()
        ticket.status = status
        ticket.updated_at = now
        if assigned_user_id is not None:
            ticket.assigned_user_id = assigned_user_id
        if status in {"resolved", "closed", "rejected"}:
            ticket.resolved_at = now

        await self.session.flush()
        return ticket
