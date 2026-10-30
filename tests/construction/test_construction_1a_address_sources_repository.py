from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, Uuid, create_engine, event, insert
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import ConstructionClient, ConstructionProject
from database.repositories.construction.construction_address_sources import ConstructionAddressSourcesRepository as Repository


async def collect(c, **changes):
    values = dict(tenant_id=c.tenant, batch_size=2)
    values.update(changes)
    return [row async for row in c.repo.iter_address_sources(**values)]


@pytest.fixture
def source_case():
    c = SimpleNamespace(tenant=uuid4(), active=True, statements=[], batches=[])
    async def execute(statement):
        c.statements.append(statement)
        rows = c.batches.pop(0) if c.batches else []
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
    c.session = SimpleNamespace(execute=AsyncMock(side_effect=execute), in_transaction=lambda: c.active,
        no_autoflush=nullcontext(), add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    c.repo = Repository(c.session)
    return c


def no_writes(c):
    for name in ('add', 'flush', 'commit', 'rollback'):
        getattr(c.session, name).assert_not_called()


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))


def projection(c, identifier=1, **changes):
    return dict(dict(project_id=UUID(int=identifier), tenant_id=c.tenant, project_name='Existing project',
        project_status='draft', address_raw='Rua das Flores 12', address_formatted=None,
        client_id=None, client_name=None), **changes)


@pytest.mark.asyncio
async def test_select_is_tenant_scoped_undeleted_outer_join_and_has_no_workflow_status_filter(source_case):
    c = source_case
    await collect(c)
    statement = c.statements[0]
    query = sql(statement)
    assert 'LEFT OUTER JOIN construction_clients ON' in query
    for predicate in ('construction_projects.tenant_id =', 'construction_projects.deleted_at IS NULL',
            'construction_clients.tenant_id = construction_projects.tenant_id',
            'construction_clients.id = construction_projects.client_id', 'construction_clients.deleted_at IS NULL'):
        assert predicate in query
    assert query.count(str(c.tenant)) == 2
    assert 'construction_projects.status =' not in query and 'FOR UPDATE' not in query
    assert 'ORDER BY construction_projects.id' in query and statement._limit_clause.value == 2
    assert not any(field in query for field in ('phone', 'email', 'notes', 'comment', 'responsible_user_id'))
    assert statement.get_execution_options()['autoflush'] is False
    no_writes(c)


@pytest.mark.asyncio
async def test_batches_scan_beyond_first_page_without_an_arbitrary_candidate_cutoff(source_case):
    c = source_case
    c.batches = [[projection(c, 1), projection(c, 2)], [projection(c, 3), projection(c, 4)], [projection(c, 5)]]
    rows = await collect(c)
    assert [row.project_id.int for row in rows] == [1, 2, 3, 4, 5]
    assert 'construction_projects.id >' not in sql(c.statements[0])
    assert str(UUID(int=2)) in sql(c.statements[1]) and str(UUID(int=4)) in sql(c.statements[2])
    no_writes(c)


@pytest.mark.asyncio
async def test_scope_is_in_sql_before_batching_and_empty_scope_issues_no_query(source_case):
    c = source_case
    assert await collect(c, allowed_project_ids=()) == []
    c.session.execute.assert_not_called()
    await collect(c, allowed_project_ids=(UUID(int=4),))
    assert 'construction_projects.id IN' in sql(c.statements[-1]) and str(UUID(int=4)) in sql(c.statements[-1])
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(tenant_id='uuid'), dict(tenant_id=None), dict(batch_size=True),
    dict(batch_size=0), dict(batch_size=501), dict(allowed_project_ids=[]), dict(allowed_project_ids=('uuid',))])
async def test_invalid_context_cannot_issue_sql(source_case, changes):
    c = source_case
    with pytest.raises(ValueError):
        await collect(c, **changes)
    c.session.execute.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(tenant_id=uuid4()), dict(project_id='uuid'),
    dict(client_id=uuid4()), dict(client_name='orphan label'), dict(address_raw=None)])
async def test_bad_projection_never_leaks_to_the_caller(source_case, changes):
    c = source_case
    c.batches = [[projection(c, **changes)]]
    with pytest.raises(ValueError):
        await collect(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_transaction_and_storage_failures_remain_with_the_caller(source_case):
    c = source_case
    c.active = False
    with pytest.raises(ValueError):
        await collect(c)
    c.session.execute.assert_not_called()
    c.active = True
    failure = RuntimeError('storage failed')
    c.session.execute.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        await collect(c)
    assert caught.value is failure
    no_writes(c)


@pytest.mark.asyncio
async def test_invalid_keyset_receipts_and_extra_scope_rows_are_rejected(source_case):
    c = source_case
    c.batches = [[projection(c, 1), projection(c, 1)]]
    with pytest.raises(ValueError):
        await collect(c)
    c.batches = [[projection(c, 8)]]
    with pytest.raises(ValueError):
        await collect(c, allowed_project_ids=(UUID(int=9),))
    no_writes(c)


@pytest.fixture
def real_sources(source_case):
    c = source_case
    metadata = MetaData()
    Table('tenants', metadata, Column('id', Uuid, primary_key=True))
    Table('users', metadata, Column('id', Uuid, primary_key=True))
    clients = ConstructionClient.__table__.to_metadata(metadata)
    projects = ConstructionProject.__table__.to_metadata(metadata)
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    c.foreign, c.user, c.client, c.archived_client, c.foreign_client = (uuid4() for _ in range(5))
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(metadata.tables['tenants'].insert(), [{'id': c.tenant}, {'id': c.foreign}])
        connection.execute(metadata.tables['users'].insert(), [{'id': c.user}])
        for identifier, tenant, archived in ((c.client, c.tenant, False), (c.archived_client, c.tenant, True),
                (c.foreign_client, c.foreign, False)):
            connection.execute(clients.insert().values(id=identifier, tenant_id=tenant,
                display_name='Client '+str(identifier), client_type='person', deleted_at=now if archived else None))
        for number, status in enumerate(('draft', 'planning', 'active', 'completed', 'cancelled'), start=1):
            connection.execute(projects.insert().values(id=UUID(int=number*10), tenant_id=c.tenant,
                name=status, status=status, address_raw='Rua das Flores 12',
                address_formatted='Rua das Flores 12 Lisboa' if number==2 else None,
                created_by=c.user, client_id=None if number==3 else c.client))
        for number, tenant, client_id, archived in ((60, c.tenant, c.client, True),
                (70, c.tenant, c.archived_client, False), (80, c.foreign, c.foreign_client, False)):
            connection.execute(projects.insert().values(id=UUID(int=number), tenant_id=tenant, name='Other project',
                address_raw='Rua das Flores 12', created_by=c.user, client_id=client_id, deleted_at=now if archived else None))
    with Session(engine) as session:
        session.begin()
        c.sync = session
        async def execute(statement):
            c.statements.append(statement)
            return session.execute(statement)
        class Adapter(SimpleNamespace):
            @property
            def no_autoflush(self):
                return session.no_autoflush
        c.session = Adapter(execute=AsyncMock(side_effect=execute), in_transaction=session.in_transaction,
            add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
        c.repo = Repository(c.session)
        try:
            yield c
        finally:
            session.rollback()
    engine.dispose()


@pytest.mark.asyncio
async def test_local_sql_includes_all_statuses_and_hides_other_tenant_and_archived_projects(real_sources):
    c = real_sources
    rows = await collect(c)
    assert [row.project_id.int for row in rows] == [10, 20, 30, 40, 50, 70]
    assert {row.project_status for row in rows} == {'draft', 'planning', 'active', 'completed', 'cancelled'}
    assert all(row.tenant_id == c.tenant for row in rows)
    assert len(c.statements) == 4
    no_writes(c)


@pytest.mark.asyncio
async def test_client_sources_are_only_direct_undeleted_same_tenant_links_and_keep_every_matching_address(real_sources):
    c = real_sources
    rows = await collect(c)
    assert {row.project_id.int for row in rows if row.client_id == c.client} == {10, 20, 40, 50}
    assert next(row for row in rows if row.project_id.int==20).address_formatted == 'Rua das Flores 12 Lisboa'
    for identifier in (30, 70):
        row = next(row for row in rows if row.project_id.int==identifier)
        assert row.client_id is None and row.client_name is None
    assert not c.sync.dirty and not c.sync.new
    no_writes(c)


@pytest.mark.asyncio
async def test_project_scope_is_applied_across_every_batch(real_sources):
    c = real_sources
    rows = await collect(c, allowed_project_ids=(UUID(int=20), UUID(int=50)), batch_size=1)
    assert [row.project_id.int for row in rows] == [20, 50]
    assert len(c.statements) == 3
    no_writes(c)


def test_repository_rejects_invalid_session():
    with pytest.raises(ValueError):
        Repository(SimpleNamespace(execute=AsyncMock()))
