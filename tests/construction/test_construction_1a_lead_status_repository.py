from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects import postgresql

from database.models import ConstructionLead
from database.repositories.construction.construction_leads import ConstructionLeadRepository
from services.construction.construction_lead_rules import ConstructionLeadTransitionError, ConstructionLeadValidationError
from test_construction_1a_lead_model import TABLE, lead_db, values
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.fixture
def status_case(lead_db):
    engine, ids = lead_db
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    identifier = uuid4()
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(
            ids, id=identifier, comment="Preserve comment", responsible_user_id=ids["user"],
            created_at=now-timedelta(days=1), updated_at=now-timedelta(days=1),
        )))
        connection.exec_driver_sql("CREATE TABLE status_checkpoint(id TEXT PRIMARY KEY)")
    session = AsyncSessionAdapter(engine)
    case = SimpleNamespace(engine=engine, ids=ids, tenant=ids["a"], lead=identifier,
        now=now, session=session, repo=ConstructionLeadRepository(session))
    try:
        yield case
    finally:
        session.sync.close()


def row(c):
    return dict(c.session.sync.execute(select(TABLE).where(TABLE.c.id == c.lead)).mappings().one())


async def transition(c, **changes):
    command = dict(tenant_id=c.tenant, lead_id=c.lead, expected_row_version=1,
        current_status="new", next_status="contacted", as_of=c.now)
    command.update(changes)
    return await c.repo.transition_status(**command)


def caller_owns_transaction(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_entire_approved_chain_preserves_fields_and_refreshes_loaded_identity(status_case):
    c = status_case
    original = row(c)
    loaded = c.session.sync.get(ConstructionLead, c.lead)
    chain = ["new", "contacted", "measurement_scheduled", "measurement_completed",
        "estimate_preparation", "proposal_sent", "negotiation", "won"]
    for version, (current, following) in enumerate(zip(chain, chain[1:]), 1):
        instant = c.now + timedelta(minutes=version)
        result = await transition(c, current_status=current, next_status=following,
            expected_row_version=version, as_of=instant)
        assert result is loaded and result.status == following and result.row_version == version+1
        after = row(c)
        assert after["updated_at"].replace(tzinfo=timezone.utc) == instant
        stable = set(original) - {"status", "row_version", "updated_at"}
        assert {key: after[key] for key in stable} == {key: original[key] for key in stable}
    assert loaded.converted_client_id is None and loaded.converted_project_id is None
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["new", "contacted", "measurement_scheduled",
    "measurement_completed", "estimate_preparation", "proposal_sent", "negotiation"])
async def test_lost_is_allowed_from_every_non_terminal_state(status_case, current):
    c = status_case
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(status=current))
    row(c)
    result = await transition(c, current_status=current, next_status="lost")
    assert result.status == "lost" and result.row_version == 2
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign_tenant", "missing", "deleted", "version",
    "state_changed", "terminal_won", "terminal_lost"])
async def test_conditional_preconditions_preserve_record_and_return_none(status_case, failure):
    c = status_case
    changes = {}
    stored = {}
    if failure == "foreign_tenant":
        changes["tenant_id"] = c.ids["b"]
    elif failure == "missing":
        changes["lead_id"] = uuid4()
    elif failure == "deleted":
        stored["deleted_at"] = c.now
    elif failure == "version":
        stored["row_version"] = 2
    else:
        stored["status"] = {"state_changed": "contacted", "terminal_won": "won", "terminal_lost": "lost"}[failure]
    if stored:
        with c.engine.begin() as connection:
            connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(**stored))
    before = row(c)
    assert await transition(c, **changes) is None
    assert row(c) == before
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_repeated_old_command_cannot_increment_version_twice(status_case):
    c = status_case
    row(c)
    assert (await transition(c)).row_version == 2
    before = row(c)
    assert await transition(c) is None
    assert row(c) == before


@pytest.mark.asyncio
async def test_pending_python_edits_do_not_flush_and_latest_record_replaces_stale_state(status_case):
    c = status_case
    stale = c.session.sync.get(ConstructionLead, c.lead)
    stale.comment = "Do not persist"
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(
            row_version=2, name="Current name", comment="Committed elsewhere"))
    result = await transition(c, expected_row_version=2)
    assert result is stale and result.row_version == 3
    assert result.name == "Current name" and result.comment == "Committed elsewhere"
    assert not c.session.sync.is_modified(result)
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_caller_rollback_reverts_status_version_and_later_transaction_work(status_case):
    c = status_case
    before = row(c)
    await transition(c)
    c.session.sync.execute(text("INSERT INTO status_checkpoint VALUES (:id)"), {"id": str(c.lead)})
    c.session.sync.rollback()
    assert row(c) == before
    assert c.session.sync.execute(text("SELECT count(*) FROM status_checkpoint")).scalar_one() == 0
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_largest_incrementable_bigint_version(status_case):
    c = status_case
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(row_version=2**63-2))
    row(c)
    result = await transition(c, expected_row_version=2**63-2,
        as_of=c.now.astimezone(timezone(timedelta(hours=3))))
    assert result.row_version == 2**63-1
    assert row(c)["updated_at"].replace(tzinfo=timezone.utc) == c.now


@pytest.fixture
def statement_case():
    result = SimpleNamespace(scalar_one_or_none=Mock(return_value=object()))
    session = SimpleNamespace(execute=AsyncMock(return_value=result), add=Mock(), flush=AsyncMock(),
        in_transaction=Mock(return_value=True), commit=AsyncMock(), rollback=AsyncMock())
    return SimpleNamespace(tenant=uuid4(), lead=uuid4(), now=datetime(2026,10,13,tzinfo=timezone.utc),
        session=session, repo=ConstructionLeadRepository(session))


@pytest.mark.asyncio
async def test_postgresql_statement_contains_conditional_guards_and_only_status_fields(statement_case):
    c = statement_case
    await transition(c)
    statement = c.session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.startswith("UPDATE construction_leads SET ")
    for field in ("tenant_id", "id", "row_version", "status"):
        assert "construction_leads."+field+" =" in str(statement.whereclause)
    assert "construction_leads.deleted_at IS NULL" in sql
    assert "RETURNING construction_leads.id" in sql
    assert {column.key for column in statement._values} == {"status", "row_version", "updated_at"}
    assert "contacted" in compiled.params.values() and c.tenant in compiled.params.values()
    assert statement.get_execution_options()["populate_existing"] is True
    assert statement.get_execution_options()["autoflush"] is False
    c.session.flush.assert_not_called()
    c.session.add.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("current,following", [
    ("won","new"), ("won","lost"), ("lost","new"), ("lost","won"),
    ("contacted","new"), ("new","measurement_scheduled"), ("new","won"),
    ("contacted","proposal_sent"), ("new","new"), ("won","won"), ("lost","lost"),
    ("qualified","contacted"), ("new","proposal"), (None,"contacted"), ("new",None),
])
async def test_invalid_skipped_reverse_terminal_and_no_op_writes_are_not_executed(statement_case, current, following):
    c = statement_case
    with pytest.raises((ConstructionLeadTransitionError, ConstructionLeadValidationError)):
        await transition(c, current_status=current, next_status=following)
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, False, True, 0, -1, 1.0, "1", 2**63-1, 2**63])
async def test_invalid_or_unincrementable_versions_rejected_before_sql(statement_case, version):
    c = statement_case
    with pytest.raises(ValueError):
        await transition(c, expected_row_version=version)
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "lead", "clock", "naive", "transaction"])
async def test_invalid_context_rejected_before_sql(statement_case, failure):
    c = statement_case
    command = {}
    if failure in ("tenant", "lead"):
        command[failure+"_id"] = "unparsed UUID"
    elif failure == "clock":
        command["as_of"] = None
    elif failure == "naive":
        command["as_of"] = c.now.replace(tzinfo=None)
    else:
        c.session.in_transaction.return_value = False
    with pytest.raises(ValueError):
        await transition(c, **command)
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_database_failure_propagates_for_caller_rollback(statement_case):
    c = statement_case
    failure = RuntimeError("database failure")
    c.session.execute.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        await transition(c)
    assert caught.value is failure
    caller_owns_transaction(c)
