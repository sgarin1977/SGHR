"""PostgreSQL admission for trusted Telegram binding updates."""
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, insert, select, text

from database.models import EventLog
from database.repositories.rate_limit import RateLimitRepository
from services.api_rate_limits import (
    API_RATE_LIMIT_WINDOW_SECONDS, PUBLIC_API_RATE_LIMIT,
    ApiRequestRateLimitExceededError,
)
from services.construction.construction_bot_rate_limit import ConstructionBotPostgresRateLimiter

BINDING_ADMISSION_EVENT = "platform_telegram_binding_request_admitted"


def binding_bucket(*, bot_id, telegram_user_id):
    if (
        type(bot_id) is not int or bot_id <= 0
        or type(telegram_user_id) is not int or telegram_user_id <= 0
    ):
        raise ValueError("Telegram binding rate-limit identity is invalid.")
    # Technical bucket only. It is never used as a canonical users.id.
    return uuid5(
        NAMESPACE_URL,
        f"platform:telegram-binding:bot:{bot_id}:user:{telegram_user_id}",
    )


class PlatformTelegramBindingPostgresRateLimiter(ConstructionBotPostgresRateLimiter):
    async def ensure_telegram_binding_allowed(self, *, bot_id, telegram_user_id):
        # The handler supplies these identifiers only after trusted Update proof.
        bucket = binding_bucket(bot_id=bot_id, telegram_user_id=telegram_user_id)
        async with self.session_factory() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise RuntimeError("Binding admission accounting requires PostgreSQL.")
            async with session.begin():
                await session.execute(text(
                    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
                ))
                await RateLimitRepository(session).acquire_action_lock(
                    tenant_id=None, user_id=None,
                    action=BINDING_ADMISSION_EVENT + ":" + str(bucket),
                )
                recent = select(EventLog.id).where(
                    EventLog.tenant_id.is_(None),
                    EventLog.user_id.is_(None),
                    EventLog.event_type == BINDING_ADMISSION_EVENT,
                    EventLog.entity_type == "telegram_binding_rate_bucket",
                    EventLog.entity_id == bucket,
                    EventLog.platform == "telegram",
                    EventLog.created_at > (
                        func.clock_timestamp()
                        - timedelta(seconds=API_RATE_LIMIT_WINDOW_SECONDS)
                    ),
                ).limit(PUBLIC_API_RATE_LIMIT).subquery()
                result = await session.execute(
                    select(func.count()).select_from(recent)
                )
                count = result.scalar_one()
                if type(count) is not int or count < 0:
                    raise RuntimeError("Binding admission count is invalid.")
                if count >= PUBLIC_API_RATE_LIMIT:
                    raise ApiRequestRateLimitExceededError()
                await session.execute(insert(EventLog).values(
                    id=uuid4(), tenant_id=None, user_id=None,
                    event_type=BINDING_ADMISSION_EVENT,
                    entity_type="telegram_binding_rate_bucket",
                    entity_id=bucket, payload={}, platform="telegram",
                    trace_id="evt_" + uuid4().hex,
                    created_at=func.clock_timestamp(),
                ))
            return count + 1


def get_binding_rate_limit_service():
    from database.session import get_session
    return PlatformTelegramBindingPostgresRateLimiter(session_factory=get_session)
