from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import ConstructionLead
from database.repositories.construction.construction_leads import ConstructionLeadCursor, ConstructionLeadRepository
from services.construction.construction_lead_rules import ConstructionLeadInput
from test_construction_1a_lead_model import TABLE, lead_db, values


@pytest.fixture
def repo_case():
    c = SimpleNamespace(tenant_id=uuid4(), lead_id=uuid4(), now=datetime(2026, 10, 7, 12, tzinfo=timezone.utc))
    c.result = SimpleNamespace(
        scalar_one_or_none=Mock(return_value=None),
        scalars=Mock(return_value=SimpleNamespace(all=Mock(return_value=[]))),
    )
    c.session = SimpleNamespace(
        execute=AsyncMock(return_value=c.result), add=Mock(), flush=AsyncMock(),
        in_transaction=Mock(return_value=True), commit=AsyncMock(), rollback=AsyncMock(),
    )
    c.repo = ConstructionLeadRepository(c.session)
    return c


def caller_owns_transaction(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("locked", [False, True])
async def test_read_is_tenant_scoped_non_deleted_and_refreshes_without_autoflush(repo_case, locked):
    c = repo_case
    expected = object()
    c.result.scalar_one_or_none.return_value = expected
    method = c.repo.get_active_lead_for_update if locked else c.repo.get_active_lead
    assert await method(tenant_id=c.tenant_id, lead_id=c.lead_id) is expected
    statement = c.session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "construction_leads.tenant_id =" in str(statement.whereclause)
    assert "construction_leads.id =" in str(statement.whereclause)
    assert "construction_leads.deleted_at IS NULL" in sql
    assert c.tenant_id in compiled.params.values() and c.lead_id in compiled.params.values()
    assert ("FOR UPDATE" in sql) is locked
    assert statement.get_execution_options()["populate_existing"] is True
    assert statement.get_execution_options()["autoflush"] is False
    c.session.flush.assert_not_called()
    c.session.add.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_create_uses_normalized_input_server_initial_fields_and_one_flush(repo_case):
    c = repo_case
    responsible = uuid4()
    data = ConstructionLeadInput(
        source=" PHONE ", name=" Example ", work_type_description=" Ремонт ", phone=" +351 123 ",
        email=" Person@Example.com ", responsible_user_id=responsible, comment=" Detail ",
    )
    result = await c.repo.create_lead(
        tenant_id=c.tenant_id, data=data, as_of=c.now.astimezone(timezone(timedelta(hours=3))),
    )
    assert isinstance(result, ConstructionLead) and result.tenant_id == c.tenant_id
    assert (result.source, result.name, result.work_type_description, result.phone, result.email) == (
        "phone", "Example", "Ремонт", "+351 123", "Person@Example.com",
    )
    assert result.status == "new" and result.row_version == 1
    assert result.converted_client_id is None and result.converted_project_id is None
    assert result.deleted_at is None and result.responsible_user_id == responsible
    assert result.created_at == result.updated_at == c.now and result.created_at.tzinfo is timezone.utc
    c.session.add.assert_called_once_with(result)
    c.session.flush.assert_awaited_once()
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "input", "clock", "naive", "transaction"])
async def test_invalid_creation_context_cannot_add_or_flush(repo_case, failure):
    c = repo_case
    fields = dict(tenant_id=c.tenant_id, data=ConstructionLeadInput(
        source="manual", name="Example", work_type_description="Painting", email="test@example.com",
    ), as_of=c.now)
    if failure == "tenant":
        fields["tenant_id"] = "invalid"
    elif failure == "input":
        fields["data"] = {"source": "manual"}
    elif failure == "clock":
        fields["as_of"] = None
    elif failure == "naive":
        fields["as_of"] = c.now.replace(tzinfo=None)
    else:
        c.session.in_transaction.return_value = False
    with pytest.raises(ValueError):
        await c.repo.create_lead(**fields)
    c.session.add.assert_not_called()
    c.session.flush.assert_not_called()
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_repository_leaves_flush_failure_and_rollback_to_caller(repo_case):
    c = repo_case
    error = RuntimeError("flush failure")
    c.session.flush.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await c.repo.create_lead(
            tenant_id=c.tenant_id, data=ConstructionLeadInput(
                source="manual", name="Example", work_type_description="Painting", phone="123",
            ), as_of=c.now,
        )
    assert caught.value is error
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("field", ["tenant_id", "lead_id"])
async def test_invalid_read_uuid_cannot_execute(repo_case, locked, field):
    c = repo_case
    fields = dict(tenant_id=c.tenant_id, lead_id=c.lead_id)
    fields[field] = "unparsed UUID"
    with pytest.raises(ValueError):
        await (c.repo.get_active_lead_for_update if locked else c.repo.get_active_lead)(**fields)
    c.session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_lock_requires_transaction_but_plain_read_does_not_own_it(repo_case):
    c = repo_case
    c.session.in_transaction.return_value = False
    with pytest.raises(ValueError):
        await c.repo.get_active_lead_for_update(tenant_id=c.tenant_id, lead_id=c.lead_id)
    c.session.execute.assert_not_called()
    assert await c.repo.get_active_lead(tenant_id=c.tenant_id, lead_id=c.lead_id) is None
    caller_owns_transaction(c)


def test_cursor_normalizes_explicit_offset_to_utc_and_is_immutable(repo_case):
    c = repo_case
    cursor = ConstructionLeadCursor(c.tenant_id, c.now.astimezone(timezone(timedelta(hours=3))), c.lead_id)
    assert cursor.updated_at == c.now and cursor.updated_at.tzinfo is timezone.utc
    with pytest.raises(FrozenInstanceError):
        cursor.tenant_id = uuid4()


@pytest.mark.parametrize("failure", ["tenant", "lead", "naive", "timestamp"])
def test_invalid_cursor_rejected(repo_case, failure):
    c = repo_case
    fields = dict(tenant_id=c.tenant_id, updated_at=c.now, lead_id=c.lead_id)
    fields[{"tenant": "tenant_id", "lead": "lead_id", "naive": "updated_at", "timestamp": "updated_at"}[failure]] = (
        c.now.replace(tzinfo=None) if failure == "naive" else "invalid"
    )
    with pytest.raises(ValueError):
        ConstructionLeadCursor(**fields)


@pytest.mark.asyncio
async def test_list_uses_descending_keyset_lookahead_and_conjunctive_bound_filters(repo_case):
    c = repo_case
    responsible, client, project = uuid4(), uuid4(), uuid4()
    cursor = ConstructionLeadCursor(c.tenant_id, c.now, c.lead_id)
    await c.repo.list_active_leads(
        tenant_id=c.tenant_id, limit=20, after=cursor, status="contacted", source=" PHONE ",
        responsible_user_id=responsible, converted_client_id=client, converted_project_id=project,
    )
    statement = c.session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    for field in ("tenant_id", "responsible_user_id", "converted_client_id", "converted_project_id"):
        assert "construction_leads."+field+" =" in str(statement.whereclause)
    assert all(value in compiled.params.values() for value in (
        c.tenant_id, c.lead_id, responsible, client, project, c.now, "contacted", "phone",
    ))
    assert "construction_leads.updated_at <" in sql and "construction_leads.id <" in sql
    assert "ORDER BY construction_leads.updated_at DESC, construction_leads.id DESC" in sql
    assert statement._limit_clause.value == 21 and "OFFSET" not in sql
    assert statement.get_execution_options()["autoflush"] is False
    assert statement.get_execution_options()["populate_existing"] is True
    c.session.flush.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,expected", [(None, 21), (1, 2), (100, 101)])
async def test_list_default_and_boundaries_return_one_lookahead_row(repo_case, limit, expected):
    c = repo_case
    fields = {} if limit is None else {"limit": limit}
    assert await c.repo.list_active_leads(tenant_id=c.tenant_id, **fields) == []
    assert c.session.execute.call_args.args[0]._limit_clause.value == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['tenant', 'zero', 'over_max', 'bool', 'text', 'cursor_shape', 'foreign_cursor', 'status', 'source', 'responsible', 'client', 'project'])
async def test_invalid_list_context_rejected_before_sql(repo_case, failure):
    c = repo_case
    fields = dict(tenant_id=c.tenant_id)
    if failure == 'tenant':
        fields['tenant_id'] = 'invalid'
    elif failure in ('zero', 'over_max', 'bool', 'text'):
        fields['limit'] = {'zero': 0, 'over_max': 101, 'bool': True, 'text': '20'}[failure]
    elif failure == 'cursor_shape':
        fields['after'] = {'updated_at': c.now}
    elif failure == 'foreign_cursor':
        fields['after'] = ConstructionLeadCursor(uuid4(), c.now, c.lead_id)
    elif failure in ('status', 'source'):
        fields[failure] = 'qualified' if failure == 'status' else ''
    else:
        fields[{'responsible': 'responsible_user_id', 'client': 'converted_client_id', 'project': 'converted_project_id'}[failure]] = 'invalid'
    with pytest.raises(ValueError):
        await c.repo.list_active_leads(**fields)
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


class AsyncSessionAdapter:
    def __init__(self, engine):
        self.sync = Session(engine, expire_on_commit=False)
        self.commit = AsyncMock(side_effect=self.sync.commit)
        self.rollback = AsyncMock(side_effect=self.sync.rollback)

    def in_transaction(self):
        return self.sync.in_transaction()

    def add(self, value):
        self.sync.add(value)

    async def execute(self, statement):
        return self.sync.execute(statement)

    async def flush(self):
        self.sync.flush()


@pytest.mark.asyncio
async def test_real_create_flushes_without_commit_and_caller_can_rollback(lead_db):
    engine, ids = lead_db
    adapter = AsyncSessionAdapter(engine)
    try:
        adapter.sync.begin()
        repository = ConstructionLeadRepository(adapter)
        result = await repository.create_lead(
            tenant_id=ids["a"], data=ConstructionLeadInput(
                source="manual", name="Example", work_type_description="Painting", phone="123",
            ), as_of=datetime(2026, 10, 7, 12, tzinfo=timezone.utc),
        )
        assert isinstance(result.id, UUID) and result.row_version == 1
        assert await repository.get_active_lead(tenant_id=ids["a"], lead_id=result.id) is result
        assert await repository.get_active_lead(tenant_id=ids["b"], lead_id=result.id) is None
        adapter.commit.assert_not_called()
        adapter.rollback.assert_not_called()
        adapter.sync.rollback()
        assert adapter.sync.execute(select(TABLE.c.id)).all() == []
    finally:
        adapter.sync.close()


@pytest.mark.asyncio
async def test_real_keyset_pages_include_terminal_states_and_hide_foreign_deleted_rows(lead_db):
    engine, ids = lead_db
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    with engine.begin() as connection:
        for number in (7, 2, 3, 4, 5):
            connection.execute(insert(TABLE).values(**values(
                ids, id=UUID(int=number),
                updated_at=now+(
                    timedelta(hours=1) if number == 5
                    else timedelta(hours=-1) if number == 7
                    else timedelta()
                ),
                status="won" if number == 5 else "new",
            )))
        connection.execute(insert(TABLE).values(**values(
            ids, id=UUID(int=8), updated_at=now, deleted_at=now,
        )))
        connection.execute(insert(TABLE).values(**values(
            ids, id=UUID(int=9), tenant_id=ids["b"], updated_at=now,
        )))
    adapter = AsyncSessionAdapter(engine)
    try:
        repository = ConstructionLeadRepository(adapter)
        first = await repository.list_active_leads(tenant_id=ids["a"], limit=2)
        assert [row.id.int for row in first] == [5, 4, 3]
        second = await repository.list_active_leads(
            tenant_id=ids["a"], limit=2,
            after=ConstructionLeadCursor(ids["a"], now, UUID(int=4)),
        )
        assert [row.id.int for row in second] == [3, 2, 7]
        last = await repository.list_active_leads(
            tenant_id=ids["a"], limit=2,
            after=ConstructionLeadCursor(ids["a"], now, UUID(int=2)),
        )
        assert [row.id.int for row in last] == [7]
        assert await repository.get_active_lead(tenant_id=ids["a"], lead_id=UUID(int=8)) is None
        adapter.commit.assert_not_called()
        adapter.rollback.assert_not_called()
    finally:
        adapter.sync.close()


@pytest.mark.asyncio
async def test_locked_read_discards_stale_pending_state_without_autoflush(lead_db):
    engine, ids = lead_db
    identifier = uuid4()
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids, id=identifier, comment="Initial")))
    adapter = AsyncSessionAdapter(engine)
    try:
        stale = adapter.sync.get(ConstructionLead, identifier)
        stale.comment = "Pending value must not be flushed by the read"
        with engine.begin() as connection:
            connection.execute(update(TABLE).where(TABLE.c.id == identifier).values(
                comment="Committed elsewhere", row_version=2,
            ))
        refreshed = await ConstructionLeadRepository(adapter).get_active_lead_for_update(
            tenant_id=ids["a"], lead_id=identifier,
        )
        assert refreshed is stale and refreshed.comment == "Committed elsewhere" and refreshed.row_version == 2
        assert not adapter.sync.is_modified(refreshed)
        adapter.commit.assert_not_called()
        adapter.rollback.assert_not_called()
    finally:
        adapter.sync.close()
