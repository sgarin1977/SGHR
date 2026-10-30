import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4
import json

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from database.repositories.construction.construction_client_history import (
    ConstructionClientHistoryRepository, ConstructionClientHistoryRecord, ConstructionClientHistoryCursor,
    CLIENT_HISTORY_RECORD_FIELDS,
)
from services.construction.construction_client_history_read import (
    ConstructionClientHistoryReadService, ConstructionClientHistoryReadCursor, ConstructionClientHistoryReadOperationError,
    PUBLIC_CLIENT_HISTORY_FIELDS, build_construction_client_history_read_service,
)
from services.construction.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_client_history_repository import sql_case, seed, row, NOW
from test_construction_1a_client_history_contract import TENANT, CLIENT, OTHER, ENTITIES


def record(item):
    return ConstructionClientHistoryRecord(**{field: getattr(item, field) for field in CLIENT_HISTORY_RECORD_FIELDS})


@pytest.fixture
def read_case():
    c = SimpleNamespace(tenant=TENANT, client=CLIENT, user=uuid4(), now=NOW)
    c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status='active')
    c.context = ConstructionAccessContext(c.user, c.tenant, ('OWNER',), ('construction.projects.read',),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    c.session = SimpleNamespace(in_transaction=lambda: True, no_autoflush=nullcontext(),
        execute=AsyncMock(), add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    c.grant = SimpleNamespace(role='OWNER', scope_type='tenant', scope_id=c.tenant, status='active', expires_at=None)
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    c.identity = dict(id=c.client, tenant_id=c.tenant)
    c.row = record(row(id=UUID(int=30)))
    c.repo = SimpleNamespace(session=c.session, get_client_identity_for_history=AsyncMock(return_value=c.identity),
        list_history_for_client=AsyncMock(return_value=[c.row]))
    c.service = ConstructionClientHistoryReadService(session=c.session, repository=c.repo,
        access_repository=c.access, now_provider=lambda: c.now)
    return c


async def read(c, **changes):
    kwargs = dict(actor=c.actor, access_context=c.context, client_id=c.client)
    kwargs.update(changes)
    return await c.service.list_history_for_client(**kwargs)


def no_writes(c):
    for method in ('add', 'flush', 'commit', 'rollback'): getattr(c.session, method).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', tuple(ENTITIES))
async def test_all_domain_history_types_publish_bounded_projection_with_current_access(read_case, kind):
    c = read_case
    c.row = record(row(kind, id=UUID(int=30)))
    c.repo.list_history_for_client.return_value = [c.row]
    page = await read(c)
    assert len(page.items) == 1 and page.next_cursor is None
    assert set(page.items[0]) == PUBLIC_CLIENT_HISTORY_FIELDS
    assert page.items[0]['event_type'] == kind and page.items[0]['tenant_id'] == str(c.tenant)
    assert page.items[0]['payload']['related_client_id'] == str(c.client)
    assert c.repo.get_client_identity_for_history.await_count == 2
    assert c.access.has_active_platform_membership.await_count == 3
    assert c.context.permissions == ('construction.projects.read',)
    c.repo.list_history_for_client.assert_awaited_once_with(tenant_id=c.tenant, client_id=c.client, limit=20, after=None)
    no_writes(c)


@pytest.mark.asyncio
async def test_unknown_payload_keys_and_changes_are_not_published_and_snapshot_is_detached(read_case):
    c = read_case
    item = row('construction_client_updated', id=UUID(int=30))
    item.payload.update(token='never-public-token', headers={'Authorization': 'never-public-header'},
        created_client_id='never-public-other-kind', financial_secret='never-public-financial')
    item.payload['changes'] = [dict(field='display_name', old_value='Before', new_value='After'),
        dict(field='password', old_value=None, new_value={'credential': 'never-public-password'})]
    c.repo.list_history_for_client.return_value = [record(item)]
    page = await read(c)
    serialized = json.dumps(page.items)
    assert 'never-public' not in serialized and 'password' not in serialized
    assert page.items[0]['payload']['changes'] == [dict(field='display_name', old_value='Before', new_value='After')]
    item.payload['changes'][0]['new_value'] = 'Later mutation'
    item.payload['related_client_id'] = str(OTHER)
    assert page.items[0]['payload']['changes'][0]['new_value'] == 'After'
    assert page.items[0]['payload']['related_client_id'] == str(CLIENT)
    no_writes(c)


@pytest.mark.asyncio
async def test_legacy_and_archived_client_event_is_kept_without_synthesized_marker(read_case):
    c = read_case
    item = row('construction_client_archived', marker=False)
    item.payload['changes'] = [dict(field='deleted_at', old_value=None, new_value=NOW.isoformat())]
    c.repo.list_history_for_client.return_value = [record(item)]
    page = await read(c)
    assert page.items[0]['event_type'] == 'construction_client_archived'
    assert 'related_client_id' not in page.items[0]['payload']
    assert set(c.identity) == {'id', 'tenant_id'}


@pytest.mark.asyncio
@pytest.mark.parametrize('created_at', [NOW, NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=3)))])
async def test_existing_naive_utc_and_aware_timestamps_publish_same_utc_instant(read_case, created_at):
    c = read_case
    c.repo.list_history_for_client.return_value = [replace(c.row, created_at=created_at)]
    assert (await read(c)).items[0]['created_at'] == NOW.isoformat().replace('+00:00', 'Z')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['actor', 'user', 'tenant', 'permission', 'project_scope', 'no_grants'])
async def test_initial_denial_precedes_history_and_client_queries(read_case, failure):
    c = read_case
    if failure == 'actor': c.actor.status = 'blocked'
    else:
        c.context = ConstructionAccessContext(OTHER if failure == 'user' else c.user,
            OTHER if failure == 'tenant' else c.tenant, ('PROJECT_MANAGER',) if failure == 'project_scope' else ('OWNER',),
            () if failure == 'permission' else ('construction.projects.read',),
            () if failure == 'no_grants' else (ConstructionGrantContext('PROJECT_MANAGER', 'project', OTHER),)
            if failure == 'project_scope' else (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)): await read(c)
    c.repo.get_client_identity_for_history.assert_not_called()
    c.repo.list_history_for_client.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['membership', 'revoked', 'expired', 'foreign_grant'])
async def test_current_platform_membership_and_effective_grant_are_required(read_case, failure):
    c = read_case
    if failure == 'membership': c.access.has_active_platform_membership.return_value = False
    if failure == 'revoked': c.grant.status = 'revoked'
    if failure == 'expired': c.grant.expires_at = NOW
    if failure == 'foreign_grant': c.grant.scope_id = OTHER
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)): await read(c)
    c.repo.list_history_for_client.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', [None, {}, dict(id=OTHER, tenant_id=TENANT), dict(id=CLIENT, tenant_id=OTHER)])
async def test_missing_or_foreign_client_cannot_query_history(read_case, identity):
    c = read_case
    c.repo.get_client_identity_for_history.return_value = identity
    with pytest.raises(ConstructionClientNotFoundError): await read(c)
    c.repo.list_history_for_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['membership', 'grant', 'client', 'final_identity', 'scope'])
@pytest.mark.parametrize('empty', [False, True])
async def test_loss_during_read_cannot_publish_data_count_or_cursor_even_for_empty_pages(read_case, failure, empty):
    c = read_case
    async def query(**kwargs):
        if failure == 'membership': c.access.has_active_platform_membership.return_value = False
        if failure == 'grant': c.grant.status = 'revoked'
        if failure == 'client': c.repo.get_client_identity_for_history.return_value = None
        if failure == 'scope': c.grant.role, c.grant.scope_type, c.grant.scope_id = 'PROJECT_MANAGER', 'project', OTHER
        return [] if empty else [c.row]
    c.repo.list_history_for_client.side_effect = query
    if failure == 'final_identity':
        async def identity(**kwargs):
            if c.repo.get_client_identity_for_history.await_count == 2: c.grant.status = 'revoked'
            return c.identity
        c.repo.get_client_identity_for_history.side_effect = identity
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ConstructionClientNotFoundError)):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_access_revoked_during_initial_identity_check_prevents_history_query(read_case):
    c = read_case
    async def identity(**kwargs): c.grant.status = 'revoked'; return c.identity
    c.repo.get_client_identity_for_history.side_effect = identity
    with pytest.raises(ConstructionPermissionDeniedError): await read(c)
    c.repo.list_history_for_client.assert_not_called()


@pytest.mark.asyncio
async def test_cursor_binds_actor_tenant_client_and_uses_last_visible_item(read_case):
    c = read_case
    second = replace(c.row, id=UUID(int=20))
    c.repo.list_history_for_client.return_value = [c.row, second]
    page = await read(c, limit=1)
    expected = ConstructionClientHistoryReadCursor(c.tenant, c.user, c.client, NOW, c.row.id)
    assert page.next_cursor == expected and len(page.items) == 1
    c.repo.list_history_for_client.return_value = [second]
    assert (await read(c, limit=1, after=expected)).items[0]['id'] == str(second.id)
    assert c.repo.list_history_for_client.await_args.kwargs['after'] == ConstructionClientHistoryCursor(c.tenant, c.client, NOW, c.row.id)
    for field in ('user_id', 'tenant_id', 'client_id'):
        with pytest.raises(ConstructionClientValidationError): await read(c, after=replace(expected, **{field: OTHER}))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['foreign', 'unrelated', 'wrong_pair', 'user', 'timestamp', 'bad_reason',
    'nested_value', 'bad_result_changed', 'bad_created_project', 'duplicate', 'order', 'oversize', 'wrong_type', 'lookahead'])
async def test_malformed_or_unrelated_history_cannot_be_published(read_case, fault):
    c = read_case
    item = row('construction_activity_completed' if fault == 'bad_result_changed' else
        'construction_lead_converted' if fault == 'bad_created_project' else 'construction_client_updated', id=c.row.id)
    if fault == 'foreign': item.tenant_id = OTHER
    if fault == 'unrelated': item.payload['related_client_id'] = str(OTHER)
    if fault == 'wrong_pair': item.entity_type = 'construction_project'
    if fault == 'user': item.user_id = str(uuid4())
    if fault == 'timestamp': item.created_at = 'not-a-time'
    if fault == 'bad_reason': item.payload['reason'] = dict(password='private')
    if fault == 'nested_value': item.payload['changes'] = [dict(field='notes', old_value=None, new_value=['private'])]
    if fault == 'bad_result_changed': item.payload['result_changed'] = 'private'
    if fault == 'bad_created_project': item.payload['created_project_id'] = 'private'
    data = record(item)
    rows = [data]
    if fault == 'duplicate': rows *= 2
    if fault == 'order': rows.append(replace(data, id=UUID(int=31)))
    if fault == 'oversize': rows *= 22
    if fault == 'wrong_type': rows = (data,)
    if fault == 'lookahead': rows.append(replace(data, id=UUID(int=20), tenant_id=OTHER))
    c.repo.list_history_for_client.return_value = rows
    with pytest.raises(ConstructionClientHistoryReadOperationError): await read(c, limit=1 if fault == 'lookahead' else 20)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(client_id='invalid'), dict(limit=True), dict(limit=0), dict(limit=101), dict(after={})])
async def test_invalid_request_precedes_io(read_case, changes):
    c = read_case
    with pytest.raises(ConstructionClientValidationError): await read(c, **changes)
    c.repo.get_client_identity_for_history.assert_not_called()
    c.repo.list_history_for_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['clock', 'transaction', 'query', 'cancelled'])
async def test_errors_sanitized_and_caller_keeps_transaction(read_case, failure):
    c = read_case
    if failure == 'clock': c.service.now_provider = lambda: NOW.replace(tzinfo=None)
    if failure == 'transaction': c.session.in_transaction = lambda: False
    if failure == 'query': c.repo.list_history_for_client.side_effect = RuntimeError('private-database-details')
    if failure == 'cancelled': c.repo.list_history_for_client.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError if failure == 'cancelled' else ConstructionClientHistoryReadOperationError) as caught:
        await read(c)
    assert 'private-' not in str(caught.value)
    no_writes(c)


def test_factory_shares_session_without_new_permission_or_idempotency(read_case):
    c = read_case
    service = build_construction_client_history_read_service(session=c.session)
    assert service.repository.session is service.access_repository.session is c.session
    c.access.session = object()
    with pytest.raises(ValueError):
        ConstructionClientHistoryReadService(session=c.session, repository=c.repo, access_repository=c.access)


@pytest.mark.asyncio
async def test_history_client_identity_query_is_tenant_bound_includes_archived_and_has_no_locks():
    result = SimpleNamespace(mappings=Mock(return_value=SimpleNamespace(one_or_none=Mock(return_value=dict(id=CLIENT, tenant_id=TENANT)))))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    repo = ConstructionClientHistoryRepository(session)
    assert await repo.get_client_identity_for_history(tenant_id=TENANT, client_id=CLIENT) == dict(id=CLIENT, tenant_id=TENANT)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert 'construction_clients.id' in sql and 'construction_clients.tenant_id =' in sql
    assert 'deleted_at' not in sql and 'FOR UPDATE' not in sql
    assert statement.get_execution_options()['autoflush'] is False
    with pytest.raises(ValueError): await repo.get_client_identity_for_history(tenant_id='invalid', client_id=CLIENT)
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_actual_repository_service_keeps_archived_history_and_never_flushes_or_commits(sql_case, read_case):
    c = read_case
    sql = sql_case
    with sql.engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE construction_clients(id CHAR(32) PRIMARY KEY,tenant_id CHAR(32),deleted_at DATETIME)')
        connection.exec_driver_sql('INSERT INTO construction_clients VALUES(?,?,?)', (CLIENT.hex, TENANT.hex, '2026-10-13 12:00:00'))
    events = [row('construction_client_archived', marker=False, id=UUID(int=30)),
        row('construction_project_updated', id=UUID(int=20)), row('construction_activity_completed', id=UUID(int=10))]
    events[0].payload['changes'] = [dict(field='deleted_at', old_value=None, new_value=NOW.isoformat())]
    events[2].payload['result_changed'] = False
    seed(sql, events)
    c.session = sql.session
    c.session.in_transaction = c.session.sync.in_transaction
    type(c.session).no_autoflush = property(lambda self: self.sync.no_autoflush)
    c.access.session = c.session
    c.repo = sql.repo
    c.service = ConstructionClientHistoryReadService(session=c.session, repository=c.repo, access_repository=c.access,
        now_provider=lambda: NOW)
    statements = []
    from sqlalchemy import event as sql_event
    def capture(conn, cursor, statement, params, context, many): statements.append(statement)
    sql_event.listen(sql.engine, 'before_cursor_execute', capture)
    try:
        c.session.sync.begin()
        first = await read(c, limit=2)
        second = await read(c, limit=2, after=first.next_cursor)
        assert [item['id'] for item in first.items+second.items] == [str(item.id) for item in events]
        assert second.next_cursor is None and 'related_client_id' not in first.items[0]['payload']
        assert not any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for s in statements)
        c.session.commit.assert_not_called(); c.session.rollback.assert_not_called()
        assert c.session.sync.execute(text('SELECT count(*) FROM event_logs')).scalar_one() == 3
    finally: sql_event.remove(sql.engine, 'before_cursor_execute', capture)
