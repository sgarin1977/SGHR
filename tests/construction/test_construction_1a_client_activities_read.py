from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, update, text, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ConstructionActivity, ConstructionLead
from database.repositories.construction.construction_client_activities import (
    ConstructionClientActivitiesRepository, ConstructionClientActivitiesCursor, ConstructionClientActivityRecord,
)
from services.construction.construction_client_activities_read import (
    ConstructionClientActivitiesReadService, ConstructionClientActivitiesReadCursor,
    ConstructionClientActivitiesReadOperationError, build_construction_client_activities_read_service,
)
from services.construction.construction_activity_read import PUBLIC_ACTIVITY_PLANNED_FIELDS
from services.construction.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_activity_model import activity_db, values
from test_construction_1a_lead_model import lead_db
from test_construction_1a_lead_repository import AsyncSessionAdapter


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
FLAGS = [flags for flags in product((False, True), repeat=3) if any(flags)]


@pytest.fixture
def read_case():
    c = SimpleNamespace(tenant=uuid4(), user=uuid4(), client=uuid4(), lead=uuid4(), project=uuid4(), now=NOW)
    c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status='active')
    c.context = ConstructionAccessContext(c.user, c.tenant, ('OWNER',), ('construction.projects.read',),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    c.session = SimpleNamespace(execute=AsyncMock(), add=Mock(), flush=AsyncMock(), commit=AsyncMock(),
        rollback=AsyncMock(), in_transaction=lambda: True, no_autoflush=nullcontext())
    c.grant = SimpleNamespace(role='OWNER', scope_type='tenant', scope_id=c.tenant, status='active', expires_at=None)
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    c.client_row = SimpleNamespace(id=c.client, tenant_id=c.tenant, deleted_at=None)
    c.clients = SimpleNamespace(session=c.session, get_active_client=AsyncMock(return_value=c.client_row))
    c.row = SimpleNamespace(id=UUID(int=20), tenant_id=c.tenant, type='call', status='planned', lead_id=None,
        client_id=c.client, project_id=None, due_at=NOW-timedelta(days=1), timezone='Europe/Lisbon',
        responsible_user_id=c.user, comment='Task comment', result=None, completed_at=None, cancelled_at=None,
        created_by_user_id=c.user, created_at=NOW, updated_at=NOW, row_version=1, deleted_at=None,
        internal_secret='never-public')
    c.record = ConstructionClientActivityRecord(c.row, None, None, None, None, None, None)
    c.repo = SimpleNamespace(session=c.session, list_planned_activities_for_client=AsyncMock(return_value=[c.record]))
    c.service = ConstructionClientActivitiesReadService(session=c.session, repository=c.repo,
        client_repository=c.clients, access_repository=c.access, now_provider=lambda: c.now)
    return c


async def read(c, **changes):
    options = dict(actor=c.actor, access_context=c.context, client_id=c.client)
    options.update(changes)
    return await c.service.list_planned_activities_for_client(**options)


def no_writes(c):
    for name in ('add', 'flush', 'commit', 'rollback'):
        getattr(c.session, name).assert_not_called()


def configure(c, flags):
    lead, client, project = flags
    c.row.lead_id = c.lead if lead else None
    c.row.client_id = c.client if client else None
    c.row.project_id = c.project if project else None
    c.record = ConstructionClientActivityRecord(c.row, c.lead if lead else None,
        c.tenant if lead else None, c.client if lead else None,
        c.project if project else None, c.tenant if project else None, c.client if project else None)
    c.repo.list_planned_activities_for_client.return_value = [c.record]


@pytest.mark.asyncio
@pytest.mark.parametrize('flags', FLAGS)
async def test_all_client_associations_return_one_public_activity_without_mutation(read_case, flags):
    c = read_case
    configure(c, flags)
    page = await read(c)
    assert len(page.items) == 1 and page.next_cursor is None
    item = page.items[0]
    assert set(item) == PUBLIC_ACTIVITY_PLANNED_FIELDS
    assert item['id'] == str(c.row.id) and item['due_at'].startswith('2026-10-13')
    assert 'internal_secret' not in item and 'linked_lead_client_id' not in item and 'related_client_id' not in item
    c.row.comment = 'Later change'
    assert item['comment'] == 'Task comment'
    assert c.clients.get_active_client.await_count == 2
    assert c.access.has_active_platform_membership.await_count == 3
    c.repo.list_planned_activities_for_client.assert_awaited_once_with(tenant_id=c.tenant, client_id=c.client, limit=20, after=None)
    no_writes(c)


@pytest.mark.asyncio
async def test_or_contract_can_match_one_link_when_other_links_belong_to_other_clients(read_case):
    c = read_case
    configure(c, (True, True, True))
    c.record = replace(c.record, linked_lead_client_id=uuid4(), linked_project_client_id=uuid4())
    c.repo.list_planned_activities_for_client.return_value = [c.record]
    assert len((await read(c)).items) == 1


@pytest.mark.asyncio
async def test_empty_page_is_allowed_for_existing_authorized_client(read_case):
    c = read_case
    c.repo.list_planned_activities_for_client.return_value = []
    page = await read(c)
    assert page.items == () and page.next_cursor is None
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['actor', 'tenant', 'user', 'permission', 'project_scope'])
async def test_initial_access_denial_precedes_client_and_activity_queries(read_case, failure):
    c = read_case
    if failure == 'actor':
        c.actor.status = 'blocked'
    else:
        c.context = ConstructionAccessContext(uuid4() if failure == 'user' else c.user,
            uuid4() if failure == 'tenant' else c.tenant,
            ('PROJECT_MANAGER',) if failure == 'project_scope' else ('OWNER',),
            () if failure == 'permission' else ('construction.projects.read',),
            (ConstructionGrantContext('PROJECT_MANAGER', 'project', c.project),) if failure == 'project_scope'
            else (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    c.clients.get_active_client.assert_not_called()
    c.repo.list_planned_activities_for_client.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['membership', 'revoked', 'expired'])
async def test_current_membership_and_effective_grants_required(read_case, failure):
    c = read_case
    if failure == 'membership': c.access.has_active_platform_membership.return_value = False
    if failure == 'revoked': c.grant.status = 'revoked'
    if failure == 'expired': c.grant.expires_at = NOW
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    c.repo.list_planned_activities_for_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'foreign', 'archived', 'wrong_id'])
async def test_client_must_be_available_before_activity_read(read_case, failure):
    c = read_case
    if failure == 'missing': c.clients.get_active_client.return_value = None
    if failure == 'foreign': c.client_row.tenant_id = uuid4()
    if failure == 'archived': c.client_row.deleted_at = NOW
    if failure == 'wrong_id': c.client_row.id = uuid4()
    with pytest.raises(ConstructionClientNotFoundError): await read(c)
    c.repo.list_planned_activities_for_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['revoked', 'client_archived'])
async def test_access_or_client_lost_during_query_cannot_publish_even_empty_page(read_case, failure):
    c = read_case
    async def query(**kwargs):
        if failure == 'revoked': c.grant.status = 'revoked'
        else: c.client_row.deleted_at = NOW
        return []
    c.repo.list_planned_activities_for_client.side_effect = query
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ConstructionClientNotFoundError)):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['unrelated', 'foreign', 'deleted', 'completed', 'cancelled', 'naive',
    'evidence_foreign', 'evidence_wrong_id', 'evidence_malformed', 'missing_parent', 'duplicate', 'order', 'oversized'])
async def test_invalid_or_out_of_query_result_is_not_published(read_case, failure):
    c = read_case
    configure(c, (True, True, True))
    if failure == 'unrelated':
        c.row.client_id = uuid4()
        c.record = replace(c.record, linked_lead_client_id=uuid4(), linked_project_client_id=uuid4())
    elif failure == 'foreign': c.row.tenant_id = uuid4()
    elif failure == 'deleted': c.row.deleted_at = NOW
    elif failure in ('completed', 'cancelled'): c.row.status = failure
    elif failure == 'naive': c.row.due_at = NOW.replace(tzinfo=None)
    elif failure == 'evidence_foreign': c.record = replace(c.record, linked_lead_tenant_id=uuid4())
    elif failure == 'evidence_wrong_id': c.record = replace(c.record, linked_project_id=uuid4())
    elif failure == 'evidence_malformed': c.record = replace(c.record, linked_lead_client_id='not-a-uuid')
    elif failure == 'missing_parent': c.record = replace(c.record, linked_project_id=None, linked_project_tenant_id=None)
    rows = [c.record]
    if failure == 'duplicate': rows *= 2
    if failure == 'order':
        other = SimpleNamespace(**{**vars(c.row), 'id': UUID(int=1)})
        rows.append(replace(c.record, activity=other))
    if failure == 'oversized': rows *= 22
    c.repo.list_planned_activities_for_client.return_value = rows
    with pytest.raises(ConstructionClientActivitiesReadOperationError): await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_cursor_uses_last_visible_row_and_binds_actor_client_tenant(read_case):
    c = read_case
    second = SimpleNamespace(**{**vars(c.row), 'id': UUID(int=21)})
    c.repo.list_planned_activities_for_client.return_value = [c.record, replace(c.record, activity=second)]
    page = await read(c, limit=1)
    assert len(page.items) == 1 and page.next_cursor.activity_id == c.row.id
    assert page.next_cursor.client_id == c.client and page.next_cursor.user_id == c.user
    c.repo.list_planned_activities_for_client.return_value = [replace(c.record, activity=second)]
    next_page = await read(c, after=page.next_cursor, limit=1)
    assert next_page.items[0]['id'] == str(second.id) and next_page.next_cursor is None
    for key in ('tenant_id', 'user_id', 'client_id'):
        bad = replace(page.next_cursor, **{key: uuid4()})
        with pytest.raises(ConstructionClientValidationError): await read(c, after=bad)


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [0, 101, True, '20'])
async def test_invalid_limit_rejected_before_query(read_case, limit):
    c = read_case
    with pytest.raises(ConstructionClientValidationError): await read(c, limit=limit)
    c.repo.list_planned_activities_for_client.assert_not_called()


@pytest.mark.asyncio
async def test_errors_sanitized_and_read_transaction_remains_owned_by_caller(read_case):
    c = read_case
    c.repo.list_planned_activities_for_client.side_effect = RuntimeError('password-private')
    with pytest.raises(ConstructionClientActivitiesReadOperationError) as caught: await read(c)
    assert 'password' not in str(caught.value)
    no_writes(c)


@pytest.mark.asyncio
async def test_caller_transaction_is_required_before_any_read(read_case):
    c = read_case
    c.session.in_transaction = lambda: False
    with pytest.raises(ConstructionClientActivitiesReadOperationError): await read(c)
    c.clients.get_active_client.assert_not_called()
    c.repo.list_planned_activities_for_client.assert_not_called()


def test_factory_and_shared_session_guard(read_case):
    c = read_case
    built = build_construction_client_activities_read_service(session=c.session)
    assert built.repository.session is built.client_repository.session is built.access_repository.session is c.session
    c.clients.session = object()
    with pytest.raises(ValueError):
        ConstructionClientActivitiesReadService(session=c.session, repository=c.repo,
            client_repository=c.clients, access_repository=c.access)


@pytest.mark.asyncio
async def test_postgresql_query_has_tenant_or_dedup_order_cursor_and_no_locks_or_flush():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=Mock(return_value=[]))))
    repo = ConstructionClientActivitiesRepository(session)
    tenant, client, activity = uuid4(), uuid4(), uuid4()
    cursor = ConstructionClientActivitiesCursor(tenant, client, NOW, activity)
    await repo.list_planned_activities_for_client(tenant_id=tenant, client_id=client, limit=2, after=cursor)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert sql.count('LEFT OUTER JOIN') == 2 and 'FOR UPDATE' not in sql
    where = sql.split(' WHERE ')[-1] if ' WHERE ' in sql else sql.split('WHERE ')[1]
    assert 'construction_activities.tenant_id =' in where and 'construction_activities.deleted_at IS NULL' in where
    assert 'construction_activities.status =' in where
    assert 'construction_activities.client_id =' in where and 'construction_leads.converted_client_id =' in where
    assert 'construction_projects.client_id =' in where and ' OR ' in where
    assert 'construction_leads.tenant_id = construction_activities.tenant_id' in sql
    assert 'construction_projects.tenant_id = construction_activities.tenant_id' in sql
    assert 'ORDER BY construction_activities.due_at ASC, construction_activities.id ASC' in sql
    assert statement.compile().params['param_1'] == 3
    assert statement.get_execution_options()['autoflush'] is False
    assert statement.get_execution_options()['populate_existing'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['tenant', 'client', 'limit', 'foreign_cursor', 'tampered_cursor'])
async def test_invalid_repository_inputs_cannot_execute(failure):
    session = SimpleNamespace(execute=AsyncMock())
    repo = ConstructionClientActivitiesRepository(session)
    tenant, client = uuid4(), uuid4()
    kwargs = dict(tenant_id=tenant, client_id=client)
    if failure in ('tenant', 'client'): kwargs[failure+'_id'] = 'not-a-uuid'
    if failure == 'limit': kwargs['limit'] = True
    if failure == 'foreign_cursor': kwargs['after'] = ConstructionClientActivitiesCursor(tenant, uuid4(), NOW, uuid4())
    if failure == 'tampered_cursor':
        cursor = ConstructionClientActivitiesCursor(tenant, client, NOW, uuid4())
        object.__setattr__(cursor, 'due_at', NOW.replace(tzinfo=None))
        kwargs['after'] = cursor
    with pytest.raises(ValueError): await repo.list_planned_activities_for_client(**kwargs)
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_real_query_includes_seven_combinations_once_and_filters_before_pagination(activity_db):
    engine, ids = activity_db
    expected = []
    with engine.begin() as connection:
        connection.execute(update(ConstructionLead.__table__).where(ConstructionLead.id == ids['lead_a']).values(
            converted_client_id=ids['client_a'], converted_project_id=ids['project_a'], status='won', deleted_at=NOW))
        for index, flags in enumerate(FLAGS):
            identifier = UUID(int=100+index)
            expected.append(identifier)
            lead, client, project = flags
            connection.execute(insert(ConstructionActivity.__table__).values(**values(ids, id=identifier,
                lead_id=ids['lead_a'] if lead else None, client_id=ids['client_a'] if client else None,
                project_id=ids['project_a'] if project else None, due_at=NOW)))
        for index, changes in enumerate([
            dict(status='completed', completed_at=NOW), dict(status='cancelled', cancelled_at=NOW),
            dict(deleted_at=NOW), dict(lead_id=ids['lead_b'], tenant_id=ids['b']),
            dict(lead_id=None, client_id=None, project_id=ids['project_b'], tenant_id=ids['b']),
            dict(lead_id=None, client_id=ids['client_b'], tenant_id=ids['b'])]):
            connection.execute(insert(ConstructionActivity.__table__).values(**values(ids, id=UUID(int=10+index), due_at=NOW-timedelta(days=2), **changes)))
    session = AsyncSessionAdapter(engine)
    repo = ConstructionClientActivitiesRepository(session)
    try:
        first = await repo.list_planned_activities_for_client(tenant_id=ids['a'], client_id=ids['client_a'], limit=3)
        assert [r.activity.id for r in first] == expected[:4]
        cursor = ConstructionClientActivitiesCursor(ids['a'], ids['client_a'], NOW, first[2].activity.id)
        later = await repo.list_planned_activities_for_client(tenant_id=ids['a'], client_id=ids['client_a'], limit=100, after=cursor)
        assert [r.activity.id for r in later] == expected[3:]
        all_rows = await repo.list_planned_activities_for_client(tenant_id=ids['a'], client_id=ids['client_a'], limit=100)
        assert len(all_rows) == len({r.activity.id for r in all_rows}) == 7
        assert any(r.linked_lead_client_id == ids['client_a'] for r in all_rows)
        assert any(r.linked_project_client_id == ids['client_a'] for r in all_rows)
    finally: session.sync.close()


@pytest.mark.asyncio
async def test_real_or_query_includes_activity_on_each_explicitly_linked_client_card(activity_db):
    engine, ids = activity_db
    other_client, identifier = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.exec_driver_sql('INSERT INTO construction_clients VALUES(?,?)', (other_client.hex, ids['a'].hex))
        connection.execute(update(ConstructionLead.__table__).where(ConstructionLead.id == ids['lead_a']).values(
            converted_client_id=ids['client_a'], converted_project_id=ids['project_a'], status='won'))
        connection.execute(insert(ConstructionActivity.__table__).values(**values(ids, id=identifier, client_id=other_client)))
    session = AsyncSessionAdapter(engine)
    repo = ConstructionClientActivitiesRepository(session)
    try:
        for client in (ids['client_a'], other_client):
            rows = await repo.list_planned_activities_for_client(tenant_id=ids['a'], client_id=client)
            assert [r.activity.id for r in rows] == [identifier]
    finally: session.sync.close()


@pytest.mark.asyncio
async def test_service_with_actual_repository_returns_transitive_link_without_flushing_pending_orm(activity_db, read_case):
    c = read_case
    engine, ids = activity_db
    identifier, unrelated = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(update(ConstructionLead.__table__).where(ConstructionLead.id == ids['lead_a']).values(
            converted_client_id=ids['client_a'], converted_project_id=ids['project_a'], status='won'))
        connection.execute(insert(ConstructionActivity.__table__).values(**values(ids, id=identifier,
            comment='Stored comment', created_at=NOW, updated_at=NOW)))
        connection.execute(insert(ConstructionActivity.__table__).values(**values(ids, id=unrelated,
            tenant_id=ids['b'], lead_id=ids['lead_b'], comment='Unrelated stored comment')))
    class Adapter(AsyncSessionAdapter):
        @property
        def no_autoflush(self): return self.sync.no_autoflush
    class SQLiteRepository(ConstructionClientActivitiesRepository):
        async def list_planned_activities_for_client(self, **kwargs):
            records = await super().list_planned_activities_for_client(**kwargs)
            # SQLite's driver loses timezone metadata; PostgreSQL retains it.
            for record in records:
                for field in ('due_at', 'created_at', 'updated_at'):
                    value = getattr(record.activity, field)
                    if value.tzinfo is None:
                        set_committed_value(record.activity, field, value.replace(tzinfo=timezone.utc))
            return records
    c.tenant, c.user, c.client = ids['a'], ids['user'], ids['client_a']
    c.actor.tenant_id, c.actor.user_id = c.tenant, c.user
    c.client_row.id, c.client_row.tenant_id = c.client, c.tenant
    c.grant.scope_id = c.tenant
    c.context = ConstructionAccessContext(c.user, c.tenant, ('OWNER',), tuple(CONSTRUCTION_ROLE_PERMISSIONS['OWNER']),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    c.session = Adapter(engine)
    c.clients.session = c.access.session = c.session
    c.repo = SQLiteRepository(c.session)
    c.service = ConstructionClientActivitiesReadService(session=c.session, repository=c.repo,
        client_repository=c.clients, access_repository=c.access, now_provider=lambda: NOW)
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        c.session.sync.begin()
        pending = c.session.sync.get(ConstructionActivity, unrelated)
        pending.comment = 'Pending comment must not be flushed by reads'
        page = await read(c)
        assert [item['id'] for item in page.items] == [str(identifier)]
        assert page.items[0]['client_id'] is None and page.items[0]['lead_id'] == str(ids['lead_a'])
        assert page.items[0]['comment'] == 'Stored comment'
        assert not any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for s in statements)
        c.session.commit.assert_not_called()
        c.session.rollback.assert_not_called()
        c.session.sync.rollback()
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
        c.session.sync.close()
