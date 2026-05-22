from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AbuseEvent, ContactRequest, RateLimitRule


class RateLimitRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

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
        self.session.add(event)
        await self.session.flush()
        return event