from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4
import json
import re

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import BinaryExpression, Extract
from sqlalchemy.sql.functions import Function
from sqlalchemy.dialects import postgresql

from database.models import EventLog
from database.repositories.construction_client_history import ConstructionClientHistoryRepository, ConstructionClientHistoryCursor, CLIENT_HISTORY_RECORD_FIELDS
from services.construction_client_history_contract import construction_history_event_belongs_to_client
from test_construction_1a_client_history_contract import event as make_event, TENANT, CLIENT, OTHER, ENTITIES


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def row(kind='construction_project_created', marker=True, **changes):
    item = make_event(kind, marker)
    item.created_at = NOW
    item.user_id = uuid4()
    item.platform, item.trace_id = 'api', 'history-test'
    item.scope_country_id = item.scope_language_code = None
    for field, value in changes.items(): setattr(item, field, value)
    return item


@pytest.fixture
def repo_case():
    c = SimpleNamespace()
    c.result = SimpleNamespace(mappings=Mock(return_value=SimpleNamespace(all=Mock(return_value=[]))))
    c.session = SimpleNamespace(execute=AsyncMock(return_value=c.result), commit=AsyncMock(), rollback=AsyncMock(),
        add=Mock(), flush=AsyncMock())
    c.repo = ConstructionClientHistoryRepository(c.session)
    return c


@pytest.mark.asyncio
async def test_postgresql_compiles_exact_domain_evidence_filters_before_limit(repo_case):
    c = repo_case
    await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, limit=2)
    statement = c.session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert 'event_logs.tenant_id =' in sql and 'event_logs.entity_id IS NOT NULL' in sql
    assert 'jsonb_array_elements' in sql and 'CASE WHEN' in sql
    assert 'GROUP BY' in sql and 'HAVING count(*) >' in sql
    assert 'ORDER BY event_logs.created_at DESC, event_logs.id DESC' in sql
    assert 'FOR UPDATE' not in sql and ' LIKE ' not in sql and 'construction_projects' not in sql
    assert set(ENTITIES) <= set(value for value in compiled.params.values() if isinstance(value, str))
    assert TENANT in compiled.params.values() and str(CLIENT) in compiled.params.values()
    assert compiled.params['param_1'] == 3 or 3 in compiled.params.values()
    assert statement.get_execution_options()['autoflush'] is False
    assert statement.get_execution_options()['populate_existing'] is True
    for method in ('add', 'flush', 'commit', 'rollback'): getattr(c.session, method).assert_not_called()


@pytest.mark.asyncio
async def test_cursor_has_client_tenant_binding_and_exact_microsecond_epoch(repo_case):
    cursor = ConstructionClientHistoryCursor(TENANT, CLIENT, NOW.replace(microsecond=123456), UUID(int=50))
    assert cursor.epoch % 1 == Decimal('0.123456')
    shifted = cursor.created_at.astimezone(timezone(timedelta(hours=3)))
    assert ConstructionClientHistoryCursor(TENANT, CLIENT, shifted, cursor.event_id).created_at == cursor.created_at
    naive = cursor.created_at.replace(tzinfo=None)
    assert ConstructionClientHistoryCursor(TENANT, CLIENT, naive, cursor.event_id).created_at == cursor.created_at
    with pytest.raises(FrozenInstanceError): cursor.client_id = OTHER
    await repo_case.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, after=cursor)
    compiled = repo_case.session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert 'EXTRACT(epoch FROM event_logs.created_at)' in str(compiled)
    assert cursor.epoch in compiled.params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['tenant', 'client', 'limit_bool', 'limit_zero', 'limit_large', 'cursor_type',
    'cursor_tenant', 'cursor_client', 'cursor_tampered'])
async def test_invalid_input_never_executes(repo_case, fault):
    c = repo_case
    kwargs = dict(tenant_id=TENANT, client_id=CLIENT)
    if fault == 'tenant': kwargs['tenant_id'] = 'invalid'
    if fault == 'client': kwargs['client_id'] = None
    if fault.startswith('limit_'): kwargs['limit'] = {'limit_bool': True, 'limit_zero': 0, 'limit_large': 101}[fault]
    if fault == 'cursor_type': kwargs['after'] = {}
    if fault in ('cursor_tenant', 'cursor_client', 'cursor_tampered'):
        cursor = ConstructionClientHistoryCursor(OTHER if fault == 'cursor_tenant' else TENANT,
            OTHER if fault == 'cursor_client' else CLIENT, NOW, uuid4())
        if fault == 'cursor_tampered': object.__setattr__(cursor, 'created_at', 'invalid')
        kwargs['after'] = cursor
    with pytest.raises(ValueError): await c.repo.list_history_for_client(**kwargs)
    c.session.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['foreign', 'unrelated', 'wrong_pair', 'invalid_marker', 'duplicate', 'order', 'oversize'])
async def test_invalid_repository_response_fails_closed_instead_of_silent_post_limit_filter(repo_case, fault):
    c = repo_case
    item = row(id=UUID(int=20))
    if fault == 'foreign': item.tenant_id = OTHER
    if fault == 'unrelated': item.payload['related_client_id'] = str(OTHER)
    if fault == 'wrong_pair': item.entity_type = 'construction_lead'
    if fault == 'invalid_marker': item.payload['related_client_id'] = None
    items = [item]
    if fault == 'duplicate': items *= 2
    if fault == 'order': items.append(row(id=UUID(int=21)))
    if fault == 'oversize': items *= 22
    c.result.mappings.return_value.all.return_value = [{field:getattr(item,field) for field in CLIENT_HISTORY_RECORD_FIELDS} for item in items]
    with pytest.raises(ValueError): await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT)


@pytest.mark.asyncio
async def test_valid_records_return_without_mutation_or_transaction_ownership(repo_case):
    c = repo_case
    item = row()
    old = deepcopy(vars(item))
    c.result.mappings.return_value.all.return_value = [{field:getattr(item,field) for field in CLIENT_HISTORY_RECORD_FIELDS}]
    saved = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT)
    assert len(saved) == 1 and saved[0].id == item.id and saved[0].payload == item.payload
    assert vars(item) == old
    for method in ('add', 'flush', 'commit', 'rollback'): getattr(c.session, method).assert_not_called()


# Local SQL surrogate: adapt only PostgreSQL JSONB operations/table function to
# SQLite. The production membership predicate, WHERE, order and limit execute
# unchanged. This does not claim native PostgreSQL/RLS acceptance.
@compiles(BinaryExpression, 'sqlite')
def sqlite_json_operators(element, compiler, **kwargs):
    op = getattr(element.operator, 'opstring', None)
    if op in ('?', '-', '~'):
        left, right = compiler.process(element.left, **kwargs), compiler.process(element.right, **kwargs)
        name = {'?': 'pg_json_has_key', '-': 'pg_json_remove_key', '~': 'pg_regex'}[op]
        return name+'('+left+', '+right+')'
    return compiler.visit_binary(element, **kwargs)


@compiles(Function, 'sqlite')
def sqlite_json_array_function(element, compiler, **kwargs):
    if element.name == 'jsonb_array_elements':
        return 'json_each('+compiler.process(element.clauses, **kwargs)+')'
    return compiler.visit_function(element, **kwargs)


@compiles(Extract, 'sqlite')
def sqlite_epoch(element, compiler, **kwargs):
    if element.field == 'epoch': return 'pg_epoch('+compiler.process(element.expr, **kwargs)+')'
    return compiler.visit_extract(element, **kwargs)


def decode(value):
    if isinstance(value, str):
        try: return json.loads(value)
        except (ValueError, TypeError): return value
    return value


def json_type(value):
    if value is None: return None
    item = decode(value)
    if item is None: return 'null'
    if isinstance(item, bool): return 'boolean'
    if isinstance(item, dict): return 'object'
    if isinstance(item, list): return 'array'
    if isinstance(item, str): return 'string'
    return 'number'


@pytest.fixture
def sql_case():
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def setup(connection, record):
        connection.create_function('jsonb_typeof', 1, json_type)
        connection.create_function('pg_json_has_key', 2, lambda value, key: int(isinstance(decode(value), dict) and key in decode(value)))
        def remove_key(value, key):
            item = decode(value)
            return json.dumps({k:v for k,v in item.items() if k != key}) if isinstance(item, dict) else '{}'
        connection.create_function('pg_json_remove_key', 2, remove_key)
        def regex(value, pattern):
            if value is None: return None
            if pattern == '[^[:space:]]': return int(any(not char.isspace() for char in value))
            return int(bool(re.search(pattern, value)))
        connection.create_function('pg_regex', 2, regex)
        connection.create_function('pg_epoch', 1, lambda value: datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE event_logs (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), user_id CHAR(32), '
            'event_type TEXT NOT NULL, entity_type TEXT, entity_id CHAR(32), payload TEXT, platform TEXT, trace_id TEXT, '
            'scope_country_id CHAR(32), scope_language_code VARCHAR(10), created_at DATETIME NOT NULL)')
        connection.exec_driver_sql('CREATE TABLE construction_activities (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), '
            'client_id CHAR(32), lead_id CHAR(32), project_id CHAR(32), created_at DATETIME, deleted_at DATETIME)')
    class Adapter:
        def __init__(self):
            self.sync = Session(engine, expire_on_commit=False)
            self.commit, self.rollback = AsyncMock(), AsyncMock()
        async def execute(self, statement): return self.sync.execute(statement)
    c = SimpleNamespace(engine=engine, session=Adapter())
    c.repo = ConstructionClientHistoryRepository(c.session)
    try: yield c
    finally: c.session.sync.close(); engine.dispose()


def seed(c, items):
    with c.engine.begin() as connection:
        for item in items:
            data = {field: getattr(item, field, None) for field in EventLog.__table__.columns.keys()}
            connection.execute(insert(EventLog.__table__).values(**data))


@pytest.mark.asyncio
async def test_sql_predicate_matches_contract_for_marker_legacy_invalid_and_unrelated_events(sql_case):
    c = sql_case
    items = [row(kind) for kind in ENTITIES]
    items += [row(kind, marker=False) for kind in ENTITIES]
    # Event-data proof survives later mutable relationship changes.
    legacy = row('construction_project_updated', marker=False)
    legacy.payload['changes'] = [dict(field='client_id', old_value=str(OTHER), new_value=str(CLIENT))]
    items.append(legacy)
    for marker in (None, '', 'invalid', CLIENT.hex, str(OTHER), True, [], {}):
        item = row('construction_client_updated')
        item.payload['related_client_id'] = marker
        items.append(item)
    for kind in ('admin_action', 'hr_event', 'financial_event', 'security_event', 'construction_invitation_created'):
        item = row(); item.event_type = kind; items.append(item)
    item = row(); item.tenant_id = OTHER; items.append(item)
    item = row(); item.entity_type = 'construction_client'; items.append(item)
    for payload in (None, [], True, 'invalid', 123, {'operation':'create','changes':{},'related_client_id':str(CLIENT)}):
        items.append(row(payload=payload))
    for changes in ([None], [True], ['field'], [{}], [dict(field='', old_value=None, new_value='x')],
        [dict(field='name', old_value=None, new_value='x')]*2,
        [dict(field='name', old_value=None, new_value='x', secret='extra')]):
        item = row(); item.payload['changes'] = changes; items.append(item)
    for old_value, new_value in ((None, str(CLIENT)), (str(OTHER), str(CLIENT)), ('invalid', str(CLIENT)),
        (None, None), (None, str(OTHER)), (True, str(CLIENT)), ([], str(CLIENT))):
        for marker in (True, False):
            item = row('construction_project_updated', marker=marker)
            item.payload['changes'] = [dict(field='client_id', old_value=old_value, new_value=new_value)]
            items.append(item)
    for fault in ('status', 'client', 'created', 'duplicate', 'marker'):
        item = row('construction_lead_converted')
        if fault == 'status': item.payload['changes'][0]['new_value'] = 'lost'
        if fault == 'client': item.payload['changes'][1]['new_value'] = str(OTHER)
        if fault == 'created': item.payload['created_client_id'] = str(OTHER)
        if fault == 'duplicate': item.payload['changes'].append(deepcopy(item.payload['changes'][1]))
        if fault == 'marker': item.payload['related_client_id'] = None
        items.append(item)
    for index, item in enumerate(items): item.id = UUID(int=index+1)
    seed(c, items)
    expected = [item.id for item in reversed(items) if construction_history_event_belongs_to_client(item, tenant_id=TENANT, client_id=CLIENT)]
    rows = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, limit=100)
    assert [item.id for item in rows] == expected
    c.session.commit.assert_not_called(); c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_filters_apply_before_limit_and_cursor_has_no_duplicates(sql_case):
    c = sql_case
    good = [row(id=UUID(int=100+i), created_at=NOW-timedelta(microseconds=i)) for i in range(7)]
    unrelated = []
    for index in range(35):
        item = row(id=UUID(int=1000+index), created_at=NOW+timedelta(seconds=index+1))
        item.payload['related_client_id'] = str(OTHER)
        unrelated.append(item)
    seed(c, good+unrelated)
    first = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, limit=3)
    assert [item.id for item in first] == [item.id for item in good[:4]]
    cursor = ConstructionClientHistoryCursor(TENANT, CLIENT, first[2].created_at, first[2].id)
    later = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, limit=3, after=cursor)
    assert [item.id for item in later] == [item.id for item in good[3:7]]


@pytest.mark.asyncio
async def test_equal_timestamps_use_uuid_tie_break_and_reads_do_not_flush_pending_changes(sql_case):
    c = sql_case
    items = [row(id=UUID(int=index), created_at=NOW) for index in range(1,6)]
    seed(c, items)
    pending = c.session.sync.get(EventLog, UUID(int=1))
    pending.trace_id = 'Pending change must not be flushed'
    first = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, limit=2)
    assert [item.id for item in first] == [UUID(int=i) for i in (5,4,3)]
    cursor = ConstructionClientHistoryCursor(TENANT, CLIENT, first[1].created_at, first[1].id)
    later = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, after=cursor)
    assert [item.id for item in later] == [UUID(int=i) for i in (3,2,1)]
    with c.engine.connect() as connection:
        assert connection.execute(select(EventLog.trace_id).where(EventLog.id==UUID(int=1))).scalar_one() == 'history-test'
