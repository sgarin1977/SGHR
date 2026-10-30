"""Local binding limiter SQL/transaction contracts; no native acceptance."""
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.platform_telegram_binding_rate_limit import (
    BINDING_ADMISSION_EVENT, PlatformTelegramBindingPostgresRateLimiter,
    binding_bucket, get_binding_rate_limit_service,
)
from test_construction_bot_postgres_rate_limit import rate_case, compiled


@pytest.mark.asyncio
async def test_admission_locks_exact_bucket_before_count_and_commits(rate_case):
    c = rate_case
    limiter = PlatformTelegramBindingPostgresRateLimiter(session_factory=c.factory)
    assert await limiter.ensure_telegram_binding_allowed(
        bot_id=100, telegram_user_id=200,
    ) == 1
    sql = [str(compiled(query)) for query in c.queries]
    assert sql[0] == "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
    assert "pg_advisory_xact_lock" in sql[1]
    assert "count" in sql[2] and "event_logs" in sql[2]
    bucket = binding_bucket(bot_id=100, telegram_user_id=200)
    assert (
        "rate-limit:None:None:" + BINDING_ADMISSION_EVENT + ":" + str(bucket)
    ) in compiled(c.queries[1]).params.values()
    values = compiled(c.committed[0]).params
    assert values["user_id"] is None and values["tenant_id"] is None
    assert values["entity_id"] == bucket
    assert values["entity_type"] == "telegram_binding_rate_bucket"
    assert values["event_type"] == BINDING_ADMISSION_EVENT
    assert values["payload"] == {} and values["platform"] == "telegram"


@pytest.mark.asyncio
async def test_existing_60_request_limit_has_no_rejected_event_insert(rate_case):
    c = rate_case
    c.count = 59
    limiter = PlatformTelegramBindingPostgresRateLimiter(session_factory=c.factory)
    assert await limiter.ensure_telegram_binding_allowed(
        bot_id=100, telegram_user_id=200,
    ) == 60
    with pytest.raises(ApiRequestRateLimitExceededError):
        await limiter.ensure_telegram_binding_allowed(bot_id=100, telegram_user_id=200)
    assert c.count == 60 and len(c.committed) == 1
    assert c.rolled_back == 1


@pytest.mark.asyncio
async def test_admission_survives_service_recreation(rate_case):
    c = rate_case
    for expected in (1, 2):
        limiter = PlatformTelegramBindingPostgresRateLimiter(session_factory=c.factory)
        assert await limiter.ensure_telegram_binding_allowed(
            bot_id=100, telegram_user_id=200,
        ) == expected
    assert len(c.committed) == 2


@pytest.mark.asyncio
async def test_count_is_scoped_to_transport_event_bucket_and_window(rate_case):
    c = rate_case
    limiter = PlatformTelegramBindingPostgresRateLimiter(session_factory=c.factory)
    await limiter.ensure_telegram_binding_allowed(bot_id=100, telegram_user_id=200)
    query = compiled(c.queries[2])
    sql, values = str(query), query.params
    assert "event_logs.entity_id =" in sql
    assert "event_logs.event_type =" in sql
    assert "event_logs.entity_type =" in sql
    assert "event_logs.platform =" in sql
    assert "event_logs.user_id IS NULL" in sql
    assert "event_logs.tenant_id IS NULL" in sql
    assert "event_logs.created_at >" in sql and "clock_timestamp" in sql
    assert "LIMIT" in sql and 60 in values.values()
    assert binding_bucket(bot_id=100, telegram_user_id=200) in values.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_id,user_id", [
    (None, 2), (True, 2), (0, 2), (-1, 2), ("1", 2),
    (1, None), (1, True), (1, 0), (1, -2), (1, "2"),
])
async def test_invalid_identifiers_are_rejected_before_database(rate_case, bot_id, user_id):
    limiter = PlatformTelegramBindingPostgresRateLimiter(session_factory=rate_case.factory)
    with pytest.raises(ValueError):
        await limiter.ensure_telegram_binding_allowed(
            bot_id=bot_id, telegram_user_id=user_id,
        )
    assert rate_case.queries == []


@pytest.mark.asyncio
async def test_storage_failure_rolls_back_without_unlimited_fallback(rate_case):
    c = rate_case
    c.insert_failure = True
    limiter = PlatformTelegramBindingPostgresRateLimiter(session_factory=c.factory)
    with pytest.raises(RuntimeError, match="simulated write failure"):
        await limiter.ensure_telegram_binding_allowed(bot_id=100, telegram_user_id=200)
    assert c.committed == [] and c.count == 0 and c.rolled_back == 1


@pytest.mark.asyncio
async def test_non_postgresql_backend_is_rejected():
    begin = AsyncMock()
    @asynccontextmanager
    async def factory():
        yield NS(get_bind=lambda: NS(dialect=NS(name="sqlite")), begin=begin)
    limiter = PlatformTelegramBindingPostgresRateLimiter(session_factory=factory)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        await limiter.ensure_telegram_binding_allowed(bot_id=100, telegram_user_id=200)
    begin.assert_not_called()


def test_bots_and_telegram_identities_have_separate_buckets():
    assert binding_bucket(bot_id=1, telegram_user_id=2) == binding_bucket(
        bot_id=1, telegram_user_id=2,
    )
    assert len({
        binding_bucket(bot_id=1, telegram_user_id=2),
        binding_bucket(bot_id=1, telegram_user_id=3),
        binding_bucket(bot_id=2, telegram_user_id=2),
    }) == 3


def test_factory_uses_existing_platform_database_session_without_redis(monkeypatch):
    import api.dependencies as dependencies
    from database.session import get_session

    def forbidden():
        raise AssertionError("Legacy API rate-limit settings must not be read.")

    monkeypatch.setattr(dependencies, "get_api_request_rate_limit_service", forbidden)
    limiter = get_binding_rate_limit_service()
    assert isinstance(limiter, PlatformTelegramBindingPostgresRateLimiter)
    assert limiter.session_factory is get_session
