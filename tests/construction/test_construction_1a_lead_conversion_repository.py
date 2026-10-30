from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects import postgresql

from database.models import ConstructionLead
from database.repositories.construction.construction_leads import ConstructionLeadRepository
from test_construction_1a_lead_model import TABLE, lead_db, values
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.fixture
def convert_repo_case():
    c = SimpleNamespace(tenant=uuid4(), lead=uuid4(), client=uuid4(), project=uuid4(),
        now=datetime(2026, 10, 12, 12, tzinfo=timezone.utc), expected=object())
    result = SimpleNamespace(scalar_one_or_none=Mock(return_value=c.expected))
    c.session = SimpleNamespace(execute=AsyncMock(return_value=result), add=Mock(), flush=AsyncMock(),
        in_transaction=lambda: True, commit=AsyncMock(), rollback=AsyncMock())
    c.repo = ConstructionLeadRepository(c.session)
    return c


async def convert(c, **changes):
    data = dict(tenant_id=c.tenant, lead_id=c.lead, expected_row_version=1,
        client_id=c.client, project_id=c.project, as_of=c.now)
    data.update(changes)
    return await c.repo.complete_conversion(**data)


def caller_owns_transaction(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_conditional_sql_uses_tenant_version_state_and_related_resource_guards(convert_repo_case):
    c = convert_repo_case
    assert await convert(c, as_of=c.now.astimezone(timezone(timedelta(hours=3)))) is c.expected
    c.session.execute.assert_awaited_once()
    statement = c.session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.startswith("UPDATE construction_leads SET ")
    assert "construction_leads.tenant_id =" in sql and "construction_leads.id =" in sql
    assert "construction_leads.row_version =" in sql and "row_version=(construction_leads.row_version +" in sql
    assert "construction_leads.deleted_at IS NULL" in sql
    assert "construction_leads.converted_client_id IS NULL" in sql
    assert "construction_leads.converted_project_id IS NULL" in sql
    assert "construction_clients.tenant_id =" in sql and "construction_clients.deleted_at IS NULL" in sql
    assert "construction_projects.tenant_id =" in sql and "construction_projects.deleted_at IS NULL" in sql
    assert "construction_projects.client_id =" in sql and sql.count("EXISTS") == 2
    assert "RETURNING construction_leads.id" in sql
    assert c.tenant in compiled.params.values() and c.lead in compiled.params.values()
    assert c.client in compiled.params.values() and c.project in compiled.params.values()
    assert "won" in compiled.params.values() and c.now in compiled.params.values()
    status_values = next(value for value in compiled.params.values() if isinstance(value, list))
    assert set(status_values) == {
        "new", "contacted", "measurement_scheduled", "measurement_completed",
        "estimate_preparation", "proposal_sent", "negotiation"}
    options = statement.get_execution_options()
    assert options["populate_existing"] is True and options["autoflush"] is False
    c.session.add.assert_not_called()
    c.session.flush.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "lead_id", "client_id", "project_id"])
async def test_invalid_ids_cannot_execute_a_write(convert_repo_case, field):
    c = convert_repo_case
    with pytest.raises(ValueError):
        await convert(c, **{field: "unparsed-uuid"})
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, False, True, 0, -1, 1.0, "1", 2**63-1, 2**63])
async def test_invalid_or_unincrementable_version_is_rejected(convert_repo_case, version):
    c = convert_repo_case
    with pytest.raises(ValueError):
        await convert(c, expected_row_version=version)
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["clock", "naive", "transaction"])
async def test_clock_and_transaction_are_required_before_write(convert_repo_case, failure):
    c = convert_repo_case
    changes = {}
    if failure == "transaction":
        c.session.in_transaction = lambda: False
    else:
        changes["as_of"] = None if failure == "clock" else c.now.replace(tzinfo=None)
    with pytest.raises(ValueError):
        await convert(c, **changes)
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_repository_propagates_database_failure_without_own_rollback(convert_repo_case):
    c = convert_repo_case
    failure = RuntimeError("database-error")
    c.session.execute.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        await convert(c)
    assert caught.value is failure
    caller_owns_transaction(c)


@pytest.fixture
def sql_convert(lead_db):
    engine, ids = lead_db
    c = SimpleNamespace(tenant=ids["a"], client=ids["client_a"], project=ids["project_a"],
        lead=uuid4(), now=datetime(2026, 10, 12, 12, tzinfo=timezone.utc), ids=ids, engine=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE construction_clients ADD COLUMN deleted_at DATETIME")
        connection.exec_driver_sql("ALTER TABLE construction_projects ADD COLUMN deleted_at DATETIME")
        connection.exec_driver_sql("CREATE TABLE lead_conversion_checkpoint(id TEXT PRIMARY KEY)")
        connection.execute(insert(TABLE).values(**values(ids, id=c.lead,
            comment="Original comment", created_at=c.now-timedelta(days=1), updated_at=c.now-timedelta(days=1))))
    c.session = AsyncSessionAdapter(engine)
    c.repo = ConstructionLeadRepository(c.session)
    try:
        yield c
    finally:
        c.session.sync.close()


def row(c):
    return dict(c.session.sync.execute(select(TABLE).where(TABLE.c.id == c.lead)).mappings().one())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    "new", "contacted", "measurement_scheduled", "measurement_completed",
    "estimate_preparation", "proposal_sent", "negotiation",
])
async def test_each_non_terminal_state_is_converted_atomically_and_refreshes_loaded_identity(sql_convert, status):
    c = sql_convert
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(status=status))
    before = row(c)
    loaded = c.session.sync.get(ConstructionLead, c.lead)
    result = await convert(c)
    assert result is loaded and result.status == "won" and result.row_version == 2
    assert result.converted_client_id == c.client and result.converted_project_id == c.project
    after = row(c)
    changed = {"status", "converted_client_id", "converted_project_id", "updated_at", "row_version"}
    assert {field: value for field, value in after.items() if field not in changed} == {
        field: value for field, value in before.items() if field not in changed}
    assert after["updated_at"].replace(tzinfo=timezone.utc) == c.now
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "version", "tenant", "deleted_lead", "lost", "won", "client_link", "project_link",
    "missing_client", "missing_project", "foreign_client", "foreign_project",
    "archived_client", "archived_project", "wrong_client", "unassigned_project",
])
async def test_failed_preconditions_return_none_and_preserve_all_lead_fields(sql_convert, failure):
    c = sql_convert
    changes = {}
    with c.engine.begin() as connection:
        if failure == "deleted_lead":
            connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(deleted_at=c.now))
        elif failure in ("lost", "won"):
            connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(status=failure))
        elif failure in ("client_link", "project_link"):
            field = "converted_client_id" if failure == "client_link" else "converted_project_id"
            connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(
                **{field: c.client if failure == "client_link" else c.project}))
        elif failure.startswith("archived_"):
            table = "construction_clients" if failure == "archived_client" else "construction_projects"
            connection.execute(text("UPDATE "+table+" SET deleted_at=:time"), dict(time=c.now.isoformat()))
        elif failure in ("wrong_client", "unassigned_project"):
            other_client = uuid4()
            if failure == "wrong_client":
                connection.execute(text("INSERT INTO construction_clients(id,tenant_id) VALUES(:id,:tenant)"),
                    dict(id=other_client.hex, tenant=c.tenant.hex))
            connection.execute(text("UPDATE construction_projects SET client_id=:id WHERE id=:project"),
                dict(id=other_client.hex if failure == "wrong_client" else None, project=c.project.hex))
    if failure == "version":
        changes["expected_row_version"] = 2
    elif failure == "tenant":
        changes["tenant_id"] = c.ids["b"]
    elif failure in ("missing_client", "foreign_client"):
        changes["client_id"] = uuid4() if failure == "missing_client" else c.ids["client_b"]
    elif failure in ("missing_project", "foreign_project"):
        changes["project_id"] = uuid4() if failure == "missing_project" else c.ids["project_b"]
    before = row(c)
    assert await convert(c, **changes) is None
    assert row(c) == before
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_repeated_conditional_write_cannot_increment_a_completed_lead(sql_convert):
    c = sql_convert
    row(c)
    assert (await convert(c)).row_version == 2
    before = row(c)
    assert await convert(c, expected_row_version=2) is None
    assert row(c) == before
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_sql_uses_latest_database_version_and_does_not_flush_stale_python_edits(sql_convert):
    c = sql_convert
    stale = c.session.sync.get(ConstructionLead, c.lead)
    stale.name = "Pending Python edit"
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(
            row_version=2, name="Current database name", comment="Committed elsewhere"))
    result = await convert(c, expected_row_version=2)
    assert result is stale and result.row_version == 3
    assert result.name == "Current database name" and result.comment == "Committed elsewhere"
    assert not c.session.sync.is_modified(result)
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_caller_can_roll_back_conversion_and_later_work_as_one_transaction(sql_convert):
    c = sql_convert
    before = row(c)
    assert await convert(c) is not None
    c.session.sync.execute(text("INSERT INTO lead_conversion_checkpoint VALUES(:id)"), dict(id=str(c.lead)))
    c.session.sync.rollback()
    assert row(c) == before
    assert c.session.sync.execute(text("SELECT count(*) FROM lead_conversion_checkpoint")).scalar_one() == 0
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_largest_incrementable_bigint_version_advances_once(sql_convert):
    c = sql_convert
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id == c.lead).values(row_version=2**63-2))
    row(c)
    result = await convert(c, expected_row_version=2**63-2)
    assert result.row_version == 2**63-1
    caller_owns_transaction(c)
