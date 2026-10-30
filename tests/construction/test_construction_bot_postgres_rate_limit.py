from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction.construction_bot_rate_limit import ADMISSION_EVENT, ConstructionBotPostgresRateLimiter


def compiled(query):
    return query.compile(dialect=postgresql.dialect())


@pytest.fixture
def rate_case():
    c = NS(count=0, committed=[], rolled_back=0, queries=[], insert_failure=False)
    @asynccontextmanager
    async def factory():
        pending = []
        async def execute(query):
            c.queries.append(query)
            if getattr(query, 'is_insert', False):
                if c.insert_failure:
                    raise RuntimeError('simulated write failure')
                pending.append(query)
            return NS(scalar_one=lambda: c.count)
        @asynccontextmanager
        async def begin():
            try:
                yield
            except BaseException:
                c.rolled_back += 1
                raise
            else:
                c.committed.extend(pending)
                c.count += len(pending)
        yield NS(get_bind=lambda: NS(dialect=NS(name='postgresql')), begin=begin, execute=execute)
    c.factory = factory
    c.limiter = ConstructionBotPostgresRateLimiter(session_factory=factory)
    return c


@pytest.mark.asyncio
async def test_committed_admission_uses_existing_event_log_and_serializes_before_count(rate_case):
    c = rate_case
    user = uuid4()
    assert await c.limiter.ensure_platform_user_allowed(user_id=user) == 1
    sql = [str(compiled(q)) for q in c.queries]
    assert sql[0] == 'SET TRANSACTION ISOLATION LEVEL READ COMMITTED'
    assert 'pg_advisory_xact_lock' in sql[1]
    assert 'count' in sql[2] and 'event_logs' in sql[2] and 'clock_timestamp' in sql[2]
    assert 'INSERT INTO event_logs' in sql[3] and 'clock_timestamp()' in sql[3]
    lock_values = compiled(c.queries[1]).params.values()
    assert f'rate-limit:None:{user}:{ADMISSION_EVENT}' in lock_values
    values = compiled(c.committed[0]).params
    assert values['tenant_id'] is None and values['user_id'] == user
    assert values['payload'] == {} and values['event_type'] == ADMISSION_EVENT
    assert values['platform'] == 'telegram'


@pytest.mark.asyncio
async def test_120th_admitted_and_121st_denied_without_another_event(rate_case):
    c = rate_case
    c.count = 119
    user = uuid4()
    assert await c.limiter.ensure_platform_user_allowed(user_id=user) == 120
    with pytest.raises(ApiRequestRateLimitExceededError):
        await c.limiter.ensure_platform_user_allowed(user_id=user)
    assert len(c.committed) == 1 and c.count == 120
    assert c.rolled_back == 1


@pytest.mark.asyncio
async def test_count_survives_limiter_recreation_and_no_tenant_or_telegram_id_is_accepted(rate_case):
    c = rate_case
    user = uuid4()
    assert await c.limiter.ensure_platform_user_allowed(user_id=user) == 1
    replacement = ConstructionBotPostgresRateLimiter(session_factory=c.factory)
    assert await replacement.ensure_platform_user_allowed(user_id=user) == 2
    with pytest.raises(TypeError):
        await replacement.ensure_platform_user_allowed(user_id=user, tenant_id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize('user', [None, 123, 'telegram-user', True])
async def test_noncanonical_identity_is_rejected_before_database(rate_case, user):
    with pytest.raises(ValueError):
        await rate_case.limiter.ensure_platform_user_allowed(user_id=user)
    assert rate_case.queries == []


@pytest.mark.asyncio
async def test_database_failure_is_not_a_silent_unlimited_fallback(rate_case):
    c = rate_case
    c.insert_failure = True
    with pytest.raises(RuntimeError, match='simulated write failure'):
        await c.limiter.ensure_platform_user_allowed(user_id=uuid4())
    assert c.committed == [] and c.count == 0 and c.rolled_back == 1


@pytest.mark.asyncio
async def test_count_query_scopes_user_event_transport_and_bounds_scan(rate_case):
    c = rate_case
    user = uuid4()
    await c.limiter.ensure_platform_user_allowed(user_id=user)
    query = compiled(c.queries[2])
    sql, values = str(query), query.params
    assert 'event_logs.user_id =' in sql and 'event_logs.event_type =' in sql
    assert 'event_logs.platform =' in sql and 'event_logs.created_at >' in sql
    assert 'LIMIT' in sql and 120 in values.values()
    assert user in values.values() and ADMISSION_EVENT in values.values()
