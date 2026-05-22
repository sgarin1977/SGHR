from dataclasses import dataclass
from uuid import UUID

from database.repositories.rate_limit import RateLimitRepository


class RateLimitError(Exception):
    pass


@dataclass
class RateLimitResult:
    allowed: bool
    action: str
    current_count: int
    limit_count: int | None
    window_seconds: int | None


class RateLimitService:
    def __init__(self, repository: RateLimitRepository):
        self.repository = repository

    async def ensure_contact_request_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> RateLimitResult:
        action = "contact_request"
        rule = await self.repository.get_active_rule(
            scope="user",
            action=action,
        )

        if not rule:
            return RateLimitResult(
                allowed=True,
                action=action,
                current_count=0,
                limit_count=None,
                window_seconds=None,
            )

        current_count = await self.repository.count_contact_requests_in_window(
            user_id=user_id,
            window_seconds=rule.window_seconds,
        )

        if current_count >= rule.limit_count:
            await self.repository.log_rate_limit_exceeded(
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                limit_count=rule.limit_count,
                window_seconds=rule.window_seconds,
                current_count=current_count,
                penalty_action=rule.penalty_action,
            )
            raise RateLimitError("Contact request rate limit exceeded.")

        return RateLimitResult(
            allowed=True,
            action=action,
            current_count=current_count,
            limit_count=rule.limit_count,
            window_seconds=rule.window_seconds,
        )