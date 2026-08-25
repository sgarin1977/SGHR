from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AbuseEvent,
    Complaint,
    ContactRequest,
    EventLog,
    Message,
    RateLimitRule,
)
from database.session import async_session


class RateLimitRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        audit_session_factory=None,
    ):
        self.session = session
        self.audit_session_factory = (
            audit_session_factory
            or async_session
        )

    async def acquire_action_lock(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        action: str,
    ) -> None:
        lock_key = (
            f"rate-limit:"
            f"{tenant_id}:"
            f"{user_id}:"
            f"{action}"
        )

        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        lock_key,
                        0,
                    )
                )
            )
        )

    async def get_active_rule(
        self,
        *,
        scope: str,
        action: str,
    ) -> RateLimitRule | None:
        result = await self.session.execute(
            select(RateLimitRule)
            .where(
                RateLimitRule.scope == scope,
                RateLimitRule.action == action,
                RateLimitRule.is_active.is_(True),
            )
            .order_by(RateLimitRule.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_start_events_in_window(
        self,
        *,
        user_id: UUID,
        window_seconds: int,
    ) -> int:
        return await self.count_event_logs_in_window(
            user_id=user_id,
            event_type="user_started",
            window_seconds=window_seconds,
        )

    async def count_contact_requests_in_window(
        self,
        *,
        user_id: UUID,
        window_seconds: int,
    ) -> int:
        since = datetime.utcnow() - timedelta(seconds=window_seconds)

        result = await self.session.execute(
            select(func.count(ContactRequest.id)).where(
                ContactRequest.from_user_id == user_id,
                ContactRequest.created_at >= since,
            )
        )
        return int(result.scalar_one() or 0)

    async def count_messages_in_window(
        self,
        *,
        user_id: UUID,
        window_seconds: int,
    ) -> int:
        since = datetime.utcnow() - timedelta(seconds=window_seconds)

        result = await self.session.execute(
            select(func.count(Message.id)).where(
                Message.sender_user_id == user_id,
                Message.created_at >= since,
            )
        )
        return int(result.scalar_one() or 0)

    async def count_complaints_in_window(
        self,
        *,
        user_id: UUID,
        window_seconds: int,
    ) -> int:
        since = datetime.utcnow() - timedelta(seconds=window_seconds)

        result = await self.session.execute(
            select(func.count(Complaint.id)).where(
                Complaint.reporter_user_id == user_id,
                Complaint.created_at >= since,
            )
        )
        return int(result.scalar_one() or 0)

    async def count_event_logs_in_window(
        self,
        *,
        user_id: UUID,
        event_type: str,
        window_seconds: int,
    ) -> int:
        since = datetime.utcnow() - timedelta(seconds=window_seconds)

        result = await self.session.execute(
            select(func.count(EventLog.id)).where(
                EventLog.user_id == user_id,
                EventLog.event_type == event_type,
                EventLog.created_at >= since,
            )
        )
        return int(result.scalar_one() or 0)

    async def log_rate_limit_exceeded(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        action: str,
        limit_count: int,
        window_seconds: int,
        current_count: int,
        penalty_action: str,
    ) -> AbuseEvent:
        async with (
            self.audit_session_factory()
        ) as audit_session:
            event = AbuseEvent(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        event_type="rate_limit_exceeded",
                        score=1,
                        action_taken=penalty_action,
                        details={
                            "action": action,
                            "limit_count": limit_count,
                            "window_seconds": window_seconds,
                            "current_count": current_count,
                        },
                    )

            audit_session.add(event)

            try:
                await audit_session.commit()
            except Exception:
                await audit_session.rollback()
                raise

        return event
