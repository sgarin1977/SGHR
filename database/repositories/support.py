from datetime import datetime
from uuid import UUID

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    EventLog,
    SupportMessage,
    SupportTicket,
    UserAccount,
    UserRoleMapping,
)


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
    "request",
    "dialog",
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

    async def user_has_admin_support_access(
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
                UserRoleMapping.role.in_(
                    {"admin", "super_admin"}
                ),
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

    async def create_system_ticket(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        subject: str,
        priority: str,
        category: str,
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
            sender_role="system",
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
        statuses: set[str] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SupportTicket]:
        stmt = (
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
            .offset(max(0, int(offset)))
        )

        if statuses is not None:
            stmt = stmt.where(SupportTicket.status.in_(statuses))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_admin_escalated_tickets(
        self,
        *,
        tenant_id: UUID,
        limit: int = 5,
        offset: int = 0,
    ) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .where(
                SupportTicket.tenant_id == tenant_id,
                SupportTicket.priority == "P1",
                SupportTicket.status.in_(
                    {"open", "in_progress"}
                ),
            )
            .order_by(
                SupportTicket.updated_at.desc(),
                SupportTicket.created_at.desc(),
                SupportTicket.id.asc(),
            )
            .offset(max(0, int(offset)))
            .limit(max(1, min(int(limit), 20)))
        )
        return list(result.scalars().all())

    async def list_staff_tickets(
        self,
        *,
        tenant_id: UUID,
        statuses: set[str] | None = None,
        limit: int = 20,
        offset: int = 0,
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
            .offset(max(0, int(offset)))
        )
        return list(result.scalars().all())

    async def search_staff_tickets(
        self,
        *,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
        offset: int = 0,
    ) -> list[SupportTicket]:
        search = f"%{query}%"
        id_prefix = f"{query}%"

        result = await self.session.execute(
            select(SupportTicket)
            .outerjoin(UserAccount, UserAccount.user_id == SupportTicket.user_id)
            .where(
                SupportTicket.tenant_id == tenant_id,
                or_(
                    cast(SupportTicket.id, String).ilike(id_prefix),
                    UserAccount.username.ilike(search),
                    UserAccount.platform_user_id.ilike(search),
                    SupportTicket.category.ilike(search),
                    SupportTicket.status.ilike(search),
                ),
            )
            .order_by(
                SupportTicket.updated_at.desc(),
                SupportTicket.created_at.desc(),
            )
            .distinct()
            .limit(max(1, min(int(limit), 50)))
            .offset(max(0, int(offset)))
        )
        return list(result.scalars().all())

    async def get_staff_ticket_counts(
        self,
        *,
        tenant_id: UUID,
        statuses: set[str] | None = None,
    ) -> dict[str, int]:
        allowed_statuses = statuses or {"open", "in_progress", "resolved"}

        result = await self.session.execute(
            select(SupportTicket.status, func.count(SupportTicket.id))
            .where(
                SupportTicket.tenant_id == tenant_id,
                SupportTicket.status.in_(allowed_statuses),
            )
            .group_by(SupportTicket.status)
        )

        return {status: count for status, count in result.all()}

    async def get_staff_ticket_stats(
        self,
        *,
        tenant_id: UUID,
    ) -> dict:
        counts = await self.get_staff_ticket_counts(
            tenant_id=tenant_id,
            statuses={"open", "in_progress", "resolved", "closed", "rejected"},
        )

        first_response_result = await self.session.execute(
            select(
                SupportTicket.created_at,
                func.min(SupportMessage.created_at),
            )
            .join(SupportMessage, SupportMessage.ticket_id == SupportTicket.id)
            .where(
                SupportTicket.tenant_id == tenant_id,
                SupportMessage.sender_role.in_(("support", "admin")),
                SupportMessage.is_internal.is_(False),
            )
            .group_by(SupportTicket.id, SupportTicket.created_at)
        )

        response_minutes = []
        for created_at, first_response_at in first_response_result.all():
            if created_at and first_response_at and first_response_at >= created_at:
                response_minutes.append(
                    (first_response_at - created_at).total_seconds() / 60
                )

        avg_response_minutes = None
        if response_minutes:
            avg_response_minutes = round(
                sum(response_minutes) / len(response_minutes)
            )

        return {
            "counts": counts,
            "total": sum(counts.values()),
            "avg_response_minutes": avg_response_minutes,
        }

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

    async def log_admin_ticket_event(
        self,
        *,
        tenant_id: UUID,
        admin_user_id: UUID,
        ticket_id: UUID | None,
        action: str,
        payload: dict | None = None,
    ) -> EventLog:
        event = EventLog(
            tenant_id=tenant_id,
            user_id=admin_user_id,
            event_type="admin_ticket",
            entity_type="support_ticket",
            entity_id=ticket_id,
            payload={
                "action": action,
                **(payload or {}),
            },
            platform="telegram",
        )
        self.session.add(event)
        await self.session.flush()
        return event

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
    async def update_ticket_priority(
        self,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
        priority: str,
    ) -> SupportTicket | None:
        ticket = await self.get_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
        )
        if not ticket:
            return None

        now = datetime.utcnow()
        ticket.priority = priority
        ticket.updated_at = now

        await self.session.flush()
        return ticket