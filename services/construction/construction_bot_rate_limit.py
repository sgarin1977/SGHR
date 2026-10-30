"""PostgreSQL admission accounting for authenticated Construction bot reads.

Uses the bot's existing advisory-lock and event-log infrastructure. No Redis,
environment-mode selection, new tables or process-local counters are involved.
The independent transaction keeps admission accounting after a failed read.
"""
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, text

from database.models import EventLog
from database.repositories.rate_limit import RateLimitRepository
from services.api_rate_limits import (
    API_RATE_LIMIT_WINDOW_SECONDS, AUTHENTICATED_USER_RATE_LIMIT,
    ApiRequestRateLimitExceededError,
)


ADMISSION_EVENT = 'construction_bot_request_admitted'


class ConstructionBotPostgresRateLimiter:
    def __init__(self, *, session_factory):
        self.session_factory = session_factory

    async def ensure_authenticated_user_allowed(
        self, *, tenant_id: UUID, user_id: UUID
    ) -> int:
        if not isinstance(tenant_id, UUID):
            raise ValueError('Canonical Platform tenant UUID is required.')
        # The API guard supplies verified actor UUIDs. Organization switching
        # must not reset the shared canonical user's admission bucket.
        return await self.ensure_platform_user_allowed(user_id=user_id)

    async def ensure_platform_user_allowed(self, *, user_id: UUID) -> int:
        if not isinstance(user_id, UUID):
            raise ValueError('Canonical Platform user UUID is required.')
        async with self.session_factory() as session:
            if session.get_bind().dialect.name != 'postgresql':
                raise RuntimeError('Bot admission accounting requires PostgreSQL.')
            async with session.begin():
                # The post-lock count must see admissions committed while waiting.
                await session.execute(text('SET TRANSACTION ISOLATION LEVEL READ COMMITTED'))
                await RateLimitRepository(session).acquire_action_lock(
                    tenant_id=None, user_id=user_id, action=ADMISSION_EVENT,
                )
                # NULL tenant deliberately means a Platform-user-wide bucket:
                # switching organization/account cannot reset the same user's limit.
                recent = select(EventLog.id).where(
                    EventLog.user_id == user_id,
                    EventLog.event_type == ADMISSION_EVENT,
                    EventLog.platform == 'telegram',
                    EventLog.created_at > func.clock_timestamp() - timedelta(seconds=API_RATE_LIMIT_WINDOW_SECONDS),
                ).limit(AUTHENTICATED_USER_RATE_LIMIT).subquery()
                result = await session.execute(select(func.count()).select_from(recent))
                count = result.scalar_one()
                if type(count) is not int or count < 0:
                    raise RuntimeError('Bot admission count is invalid.')
                if count >= AUTHENTICATED_USER_RATE_LIMIT:
                    # Do not grow the event log with repeated rejected requests.
                    raise ApiRequestRateLimitExceededError()
                await session.execute(insert(EventLog).values(
                    id=uuid4(), tenant_id=None, user_id=user_id,
                    event_type=ADMISSION_EVENT, entity_type='user', entity_id=user_id,
                    payload={}, platform='telegram', trace_id='evt_' + uuid4().hex,
                    created_at=func.clock_timestamp(),
                ))
            return count + 1
