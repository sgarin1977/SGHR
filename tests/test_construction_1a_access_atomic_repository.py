from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from database.models import ConstructionAccessGrant
from database.repositories.construction_access_mutations import ConstructionAccessMutationRepository
from services.construction_access_errors import ConstructionAccessGrantConflictError

@pytest.fixture
def mutation_case():
    state = SimpleNamespace(
        tenant_id=uuid4(), grant_id=uuid4(), actor_id=uuid4(),
        now=datetime(2026, 10, 6, 12, tzinfo=timezone.utc), statements=[],
        returned=object(), active_transaction=True,
    )
    async def execute(statement):
        state.statements.append(statement)
        return SimpleNamespace(scalar_one_or_none=lambda: state.returned)
    state.session = SimpleNamespace(in_transaction=lambda: state.active_transaction, execute=execute)
    for name in ("commit", "rollback", "flush", "add"):
        setattr(state.session, name, AsyncMock(side_effect=AssertionError("Repository must not own transaction or ORM history mutations.")))
    state.repository = ConstructionAccessMutationRepository(state.session)
    return state

async def revoke(state, **changes):
    values = {
        "tenant_id": state.tenant_id, "grant_id": state.grant_id,
        "revoked_by_user_id": state.actor_id, "as_of": state.now,
    }
    values.update(changes)
    return await state.repository.revoke_if_effective(**values)

def no_transaction_close(state):
    for name in ("commit", "rollback", "flush", "add"):
        getattr(state.session, name).assert_not_called()

@pytest.mark.asyncio
async def test_conditional_revoke_is_one_update_scoped_to_original_active_grant(mutation_case):
    state = mutation_case
    assert await revoke(state) is state.returned
    assert len(state.statements) == 1
    statement = state.statements[0]
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert sql.startswith("UPDATE construction_access_grants SET ")
    assert str(state.tenant_id) in sql and str(state.grant_id) in sql and str(state.actor_id) in sql
    where = sql.split(" WHERE ", 1)[1].split(" RETURNING ", 1)[0]
    assert "status = 'active'" in where
    assert "expires_at IS NULL OR construction_access_grants.expires_at >" in where
    assert "revoked_at IS NULL" in where and "revoked_by_user_id IS NULL" in where
    assert "RETURNING construction_access_grants.id" in sql
    options = statement.get_execution_options()
    assert options["populate_existing"] is True
    assert options["synchronize_session"] is False and options["autoflush"] is False
    no_transaction_close(state)

@pytest.mark.asyncio
async def test_revoke_never_changes_meaning_of_historical_grant(mutation_case):
    state = mutation_case
    await revoke(state)
    statement = state.statements[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    names = {item.strip().split("=", 1)[0].strip() for item in assignments.split(",")}
    assert names == {"status", "revoked_by_user_id", "revoked_at"}
    for name in ("role", "scope_type", "scope_id", "expires_at", "granted_at", "user_id", "tenant_id"):
        assert name not in names
    assert "row_version" not in sql
    no_transaction_close(state)

@pytest.mark.asyncio
async def test_no_updated_row_is_returned_to_service_without_audit_or_retry(mutation_case):
    state = mutation_case
    state.returned = None
    assert await revoke(state) is None
    assert len(state.statements) == 1
    no_transaction_close(state)

@pytest.mark.asyncio
async def test_revoke_normalizes_server_instant_to_utc(mutation_case):
    state = mutation_case
    deadline = datetime(2026, 10, 6, 15, tzinfo=timezone(timedelta(hours=3)))
    await revoke(state, as_of=deadline)
    datetimes = [value for value in state.statements[0].compile().params.values() if isinstance(value, datetime)]
    assert datetimes and all(value == state.now and value.tzinfo is timezone.utc for value in datetimes)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "grant", "actor", "naive_time", "missing_time", "no_transaction"])
async def test_invalid_internal_context_cannot_issue_update(mutation_case, failure):
    state = mutation_case
    changes = {}
    if failure == "tenant":
        changes["tenant_id"] = str(state.tenant_id)
    elif failure == "grant":
        changes["grant_id"] = None
    elif failure == "actor":
        changes["revoked_by_user_id"] = True
    elif failure == "naive_time":
        changes["as_of"] = state.now.replace(tzinfo=None)
    elif failure == "missing_time":
        changes["as_of"] = None
    else:
        state.active_transaction = False
    with pytest.raises(ValueError):
        await revoke(state, **changes)
    assert state.statements == []
    no_transaction_close(state)

def test_grant_conflict_contract_has_no_row_version_dependency():
    error = ConstructionAccessGrantConflictError()
    assert error.status_code == 409 and error.code == "ACCESS_GRANT_CONFLICT"
    assert "ROW_VERSION" not in error.code
    assert "row_version" not in ConstructionAccessGrant.__table__.columns

def test_existing_history_uniqueness_is_preserved():
    constraints = {constraint.name for constraint in ConstructionAccessGrant.__table__.constraints}
    assert "ex_construction_access_grants_active_period" in constraints
