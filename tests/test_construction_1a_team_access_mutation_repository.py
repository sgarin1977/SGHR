from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from database.repositories.construction_team_access_mutations import ConstructionTeamAccessMutationRepository


@pytest.fixture
def team_case():
    c = SimpleNamespace(
        tenant_id=uuid4(), user_id=uuid4(), actor_id=uuid4(),
        now=datetime(2026, 10, 7, 12, tzinfo=timezone.utc), active=True,
        rows=[object(), object()], statements=[],
    )
    async def execute(statement):
        c.statements.append(statement)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: c.rows))
    c.session = SimpleNamespace(in_transaction=lambda: c.active, execute=AsyncMock(side_effect=execute))
    for name in ("commit", "rollback", "flush", "add", "delete"):
        setattr(c.session, name, AsyncMock())
    c.repository = ConstructionTeamAccessMutationRepository(c.session)
    return c


async def revoke(c, **changes):
    values = dict(tenant_id=c.tenant_id, user_id=c.user_id, revoked_by_user_id=c.actor_id, as_of=c.now)
    values.update(changes)
    return await c.repository.revoke_effective_user_grants(**values)


def caller_owns_transaction(c):
    for name in ("commit", "rollback", "flush", "add", "delete"):
        getattr(c.session, name).assert_not_called()


@pytest.mark.asyncio
async def test_team_revoke_targets_one_user_in_one_tenant_and_all_scopes(team_case):
    c = team_case
    result = await revoke(c)
    assert result == c.rows and result is not c.rows
    c.session.execute.assert_awaited_once()
    statement = c.statements[0]
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert sql.startswith("UPDATE construction_access_grants SET ")
    where = sql.split(" WHERE ", 1)[1].split(" RETURNING ", 1)[0]
    assert "construction_access_grants.tenant_id = '" + str(c.tenant_id) + "'" in where
    assert "construction_access_grants.user_id = '" + str(c.user_id) + "'" in where
    assert "construction_access_grants.status = 'active'" in where
    assert "construction_access_grants.revoked_at IS NULL" in where
    assert "construction_access_grants.revoked_by_user_id IS NULL" in where
    assert "expires_at IS NULL OR construction_access_grants.expires_at >" in where
    assert "scope_type" not in where and "scope_id" not in where
    assert "RETURNING construction_access_grants.id" in sql
    options = statement.get_execution_options()
    assert options["populate_existing"] is True
    assert options["synchronize_session"] is False and options["autoflush"] is False
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_team_revoke_preserves_grant_history_and_platform_membership(team_case):
    c = team_case
    await revoke(c)
    sql = str(c.statements[0].compile(dialect=postgresql.dialect()))
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    names = {item.strip().split("=", 1)[0] for item in assignments.split(",")}
    assert names == {"status", "revoked_at", "revoked_by_user_id"}
    assert "row_version" not in sql and "user_roles" not in sql
    assert "UPDATE users" not in sql
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_team_revoke_returns_empty_when_no_effective_grants_match(team_case):
    c = team_case
    c.rows = []
    assert await revoke(c) == []
    c.session.execute.assert_awaited_once()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_team_revoke_normalizes_the_same_server_instant_to_utc(team_case):
    c = team_case
    await revoke(c, as_of=c.now.astimezone(timezone(timedelta(hours=3))))
    instants = [value for value in c.statements[0].compile().params.values() if isinstance(value, datetime)]
    assert instants and all(value == c.now and value.tzinfo is timezone.utc for value in instants)
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("tenant_id", None), ("tenant_id", "invalid"), ("user_id", None), ("user_id", True),
    ("revoked_by_user_id", None), ("revoked_by_user_id", "invalid"),
    ("as_of", None), ("as_of", "2026-10-07T12:00:00Z"), ("as_of", 123),
    ("as_of", datetime(2026, 10, 7, 12)),
])
async def test_team_revoke_rejects_invalid_context_before_sql(team_case, field, value):
    c = team_case
    with pytest.raises(ValueError):
        await revoke(c, **{field: value})
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_team_revoke_requires_an_existing_transaction(team_case):
    c = team_case
    c.active = False
    with pytest.raises(ValueError):
        await revoke(c)
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_team_revoke_leaves_database_failure_to_the_transaction_owner(team_case):
    c = team_case
    error = RuntimeError("database operation failed")
    c.session.execute.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await revoke(c)
    assert caught.value is error
    c.session.execute.assert_awaited_once()
    caller_owns_transaction(c)
