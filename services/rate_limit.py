from collections.abc import Awaitable, Callable
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

    async def ensure_action_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        action: str,
        counter: Callable[[int], Awaitable[int]],
    ) -> RateLimitResult:
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

        current_count = await counter(rule.window_seconds)

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
            raise RateLimitError(f"{action} rate limit exceeded.")

        return RateLimitResult(
            allowed=True,
            action=action,
            current_count=current_count,
            limit_count=rule.limit_count,
            window_seconds=rule.window_seconds,
        )

    async def ensure_start_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> RateLimitResult:
        return await self.ensure_action_allowed(
            tenant_id=tenant_id,
            user_id=user_id,
            action="start",
            counter=lambda window_seconds: self.repository.count_start_events_in_window(
                user_id=user_id,
                window_seconds=window_seconds,
            ),
        )

    async def ensure_contact_request_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> RateLimitResult:
        return await self.ensure_action_allowed(
            tenant_id=tenant_id,
            user_id=user_id,
            action="contact_request",
            counter=lambda window_seconds: self.repository.count_contact_requests_in_window(
                user_id=user_id,
                window_seconds=window_seconds,
            ),
        )

    async def ensure_chat_message_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> RateLimitResult:
        return await self.ensure_action_allowed(
            tenant_id=tenant_id,
            user_id=user_id,
            action="chat_message",
            counter=lambda window_seconds: self.repository.count_messages_in_window(
                user_id=user_id,
                window_seconds=window_seconds,
            ),
        )

    async def ensure_complaint_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> RateLimitResult:
        return await self.ensure_action_allowed(
            tenant_id=tenant_id,
            user_id=user_id,
            action="complaint",
            counter=lambda window_seconds: self.repository.count_complaints_in_window(
                user_id=user_id,
                window_seconds=window_seconds,
            ),
        )

    async def ensure_geo_change_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> RateLimitResult:
        return await self.ensure_action_allowed(
            tenant_id=tenant_id,
            user_id=user_id,
            action="geo_change",
            counter=lambda window_seconds: self.repository.count_event_logs_in_window(
                user_id=user_id,
                event_type="geo_change",
                window_seconds=window_seconds,
            ),
        )

    async def ensure_profile_edit_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> RateLimitResult:
        return await self.ensure_action_allowed(
            tenant_id=tenant_id,
            user_id=user_id,
            action="profile_edit",
            counter=lambda window_seconds: self.repository.count_event_logs_in_window(
                user_id=user_id,
                event_type="profile_edit",
                window_seconds=window_seconds,
            ),
        )