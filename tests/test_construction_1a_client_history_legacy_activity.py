from copy import deepcopy
from dataclasses import replace
from datetime import timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from database.repositories.construction_client_history import ConstructionClientHistoryRepository, CLIENT_HISTORY_RECORD_FIELDS
from services.construction_client_history_contract import (
    ConstructionHistoryClientOnlyActivityEvidence as Evidence, construction_history_event_belongs_to_client as belongs,
)
from services.construction_client_history_read import ConstructionClientHistoryReadService, PUBLIC_CLIENT_HISTORY_FIELDS
from services.construction_client_card_read import ConstructionClientCardReadOperationError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_client_history_repository import sql_case, row, seed, NOW
from test_construction_1a_client_history_contract import TENANT, CLIENT, OTHER
from test_construction_1a_client_history_read_service import read_case
from test_construction_1a_client_card_read_service import card_case, read as read_card


KINDS = ('construction_activity_created', 'construction_activity_completed', 'construction_activity_cancelled')
ACTIVITY = UUID(int=501)


def candidate(kind=KINDS[0]):
    event = row(kind, marker=False, entity_id=ACTIVITY, id=UUID(int=90))
    event.activity_evidence = Evidence(ACTIVITY, TENANT, CLIENT, NOW-timedelta(seconds=1))
    return event


@pytest.mark.parametrize('kind', KINDS)
def test_direct_client_only_immutable_snapshot_proves_legacy_activity(kind):
    event = candidate(kind)
    old_payload = deepcopy(event.payload)
    assert belongs(event, tenant_id=TENANT, client_id=CLIENT)
    assert not belongs(event, tenant_id=TENANT, client_id=OTHER)
    assert event.payload == old_payload and 'related_client_id' not in event.payload


@pytest.mark.parametrize('fault', ['missing', 'dict', 'foreign', 'activity_id', 'client_type', 'created_type',
    'event_time_type', 'event_before_entity', 'client_change', 'lead_change', 'project_change',
    'invalid_marker', 'conflicting_marker', 'wrong_entity', 'wrong_operation', 'bad_changes'])
def test_ambiguous_or_conflicting_entity_evidence_fails_closed(fault):
    event = candidate()
    if fault == 'missing': event.activity_evidence = None
    if fault == 'dict': event.activity_evidence = vars(event.activity_evidence)
    if fault == 'foreign': event.activity_evidence = replace(event.activity_evidence, tenant_id=OTHER)
    if fault == 'activity_id': event.activity_evidence = replace(event.activity_evidence, activity_id=OTHER)
    if fault == 'client_type': event.activity_evidence = replace(event.activity_evidence, client_id=str(CLIENT))
    if fault == 'created_type': event.activity_evidence = replace(event.activity_evidence, created_at='private-invalid-time')
    if fault == 'event_time_type': event.created_at = None
    if fault == 'event_before_entity': event.activity_evidence = replace(event.activity_evidence, created_at=NOW+timedelta(seconds=1))
    if fault.endswith('_change'):
        event.payload['changes'] = [dict(field=fault.removesuffix('_change')+'_id', old_value=None, new_value=str(CLIENT))]
    if fault == 'invalid_marker': event.payload['related_client_id'] = None
    if fault == 'conflicting_marker': event.payload['related_client_id'] = str(OTHER)
    if fault == 'wrong_entity': event.entity_type = 'construction_project'
    if fault == 'wrong_operation': event.payload['operation'] = 'update'
    if fault == 'bad_changes': event.payload['changes'] = [{}]
    assert not belongs(event, tenant_id=TENANT, client_id=CLIENT)


def test_current_mutable_project_or_converted_lead_cannot_use_activity_proof():
    for kind in ('construction_project_created', 'construction_lead_created', 'construction_lead_status_changed'):
        event = row(kind, marker=False, entity_id=ACTIVITY)
        event.activity_evidence = Evidence(ACTIVITY, TENANT, CLIENT, NOW-timedelta(days=1))
        assert not belongs(event, tenant_id=TENANT, client_id=CLIENT)


def add_activity(c, *, identifier=ACTIVITY, tenant=TENANT, client=CLIENT, lead=None, project=None,
    created=NOW-timedelta(seconds=1), archived=True):
    with c.engine.begin() as connection:
        connection.execute(text('INSERT INTO construction_activities '
            '(id,tenant_id,client_id,lead_id,project_id,created_at,deleted_at) '
            'VALUES (:id,:tenant,:client,:lead,:project,:created,:deleted)'), dict(id=identifier.hex,
                tenant=tenant.hex, client=client.hex if client else None, lead=lead.hex if lead else None,
                project=project.hex if project else None, created=created.replace(tzinfo=None).isoformat(sep=' '),
                deleted=NOW.replace(tzinfo=None).isoformat(sep=' ') if archived else None))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', KINDS)
async def test_actual_query_includes_archived_direct_client_activity_without_rewriting_event(sql_case, kind):
    c = sql_case; event = candidate(kind)
    seed(c, [event]); add_activity(c)
    result = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT)
    assert len(result) == 1 and result[0].id == event.id
    assert isinstance(result[0].activity_evidence, Evidence)
    assert result[0].payload == event.payload and 'related_client_id' not in result[0].payload
    c.session.commit.assert_not_called(); c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'foreign_tenant', 'foreign_client', 'lead_link', 'project_link',
    'no_direct_client', 'future_entity', 'id_mismatch', 'invalid_marker', 'conflict', 'link_change'])
async def test_join_does_not_guess_from_mixed_foreign_or_future_links(sql_case, fault):
    c = sql_case; event = candidate()
    options = {}
    if fault == 'foreign_tenant': options['tenant'] = OTHER
    if fault == 'foreign_client': options['client'] = OTHER
    if fault == 'lead_link': options['lead'] = OTHER
    if fault == 'project_link': options['project'] = OTHER
    if fault == 'no_direct_client': options.update(client=None, project=OTHER)
    if fault == 'future_entity': options['created'] = NOW+timedelta(seconds=1)
    if fault == 'id_mismatch': options['identifier'] = OTHER
    if fault == 'invalid_marker': event.payload['related_client_id'] = None
    if fault == 'conflict': event.payload['related_client_id'] = str(OTHER)
    if fault == 'link_change': event.payload['changes'] = [dict(field='client_id', old_value=None, new_value=str(CLIENT))]
    if fault != 'missing': add_activity(c, **options)
    seed(c, [event])
    assert await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT) == []


@pytest.mark.asyncio
async def test_legacy_proofs_filter_before_limit_and_survive_cursor_pages(sql_case):
    c = sql_case; add_activity(c)
    good = [candidate(kind) for kind in KINDS]
    for index, item in enumerate(good): item.id = UUID(int=90-index)
    unrelated = [row(KINDS[0], marker=False, entity_id=uuid4(), created_at=NOW+timedelta(seconds=1), id=UUID(int=100+i)) for i in range(8)]
    seed(c, good+unrelated)
    first = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, limit=1)
    from database.repositories.construction_client_history import ConstructionClientHistoryCursor
    assert [item.id for item in first] == [item.id for item in good[:2]]
    after = ConstructionClientHistoryCursor(TENANT, CLIENT, first[0].created_at, first[0].id)
    second = await c.repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT, limit=1, after=after)
    assert [item.id for item in second] == [item.id for item in good[1:]]


@pytest.mark.asyncio
async def test_actual_repository_service_carries_private_proof_and_public_payload_stays_unchanged(sql_case, read_case):
    c, sql = read_case, sql_case
    with sql.engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE construction_clients(id CHAR(32) PRIMARY KEY,tenant_id CHAR(32),deleted_at DATETIME)')
        connection.exec_driver_sql('INSERT INTO construction_clients VALUES(?,?,?)', (CLIENT.hex, TENANT.hex, None))
    event = candidate(); seed(sql, [event]); add_activity(sql)
    c.session = sql.session
    c.session.in_transaction = c.session.sync.in_transaction
    type(c.session).no_autoflush = property(lambda self: self.sync.no_autoflush)
    c.access.session = c.session
    service = ConstructionClientHistoryReadService(session=c.session, repository=sql.repo,
        access_repository=c.access, now_provider=lambda: NOW)
    c.session.sync.begin()
    page = await service.list_history_for_client(actor=c.actor, access_context=c.context, client_id=CLIENT)
    assert len(page.items) == 1 and set(page.items[0]) == PUBLIC_CLIENT_HISTORY_FIELDS
    assert page.legacy_activity_evidence[0][0] == event.id
    assert isinstance(page.legacy_activity_evidence[0][1], Evidence)
    assert 'related_client_id' not in page.items[0]['payload']
    assert 'activity_evidence' not in page.items[0] and 'legacy_activity_evidence' not in repr(page)
    c.session.commit.assert_not_called(); c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_complete_card_revalidates_entity_proof_without_inventing_marker(card_case):
    c = card_case
    c.event = replace(c.event, event_type=KINDS[0], entity_type='construction_activity', entity_id=c.activity.id,
        payload=dict(operation='create', changes=[], reason=None),
        activity_evidence=Evidence(c.activity.id, c.tenant, c.client, c.now-timedelta(seconds=1)))
    c.repositories['history'].list_history_for_client.return_value = [c.event]
    result = await read_card(c)
    assert result.history.items[0]['id'] == str(c.event.id)
    assert 'related_client_id' not in result.history.items[0]['payload']
    assert result.history.legacy_activity_evidence[0][0] == c.event.id
    for name in ('flush', 'commit', 'rollback'): getattr(c.session, name).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'foreign', 'duplicate', 'unused', 'marker_conflict'])
async def test_card_does_not_trust_missing_or_corrupt_private_proof(card_case, fault):
    c = card_case
    c.repositories['history'].list_history_for_client.return_value = [replace(c.event,
        event_type=KINDS[0], entity_type='construction_activity', entity_id=c.activity.id,
        payload=dict(operation='create', changes=[], reason=None),
        activity_evidence=Evidence(c.activity.id, c.tenant, c.client, c.now-timedelta(seconds=1)))]
    history = c.services['history']; original = history.list_history_for_client
    async def corrupt(**kwargs):
        page = await original(**kwargs)
        proof = page.legacy_activity_evidence[0]
        if fault == 'missing': return replace(page, legacy_activity_evidence=())
        if fault == 'foreign': return replace(page, legacy_activity_evidence=((proof[0], replace(proof[1], tenant_id=uuid4())),))
        if fault == 'duplicate': return replace(page, legacy_activity_evidence=(proof, proof))
        if fault == 'unused': return replace(page, legacy_activity_evidence=((uuid4(), proof[1]),))
        page.items[0]['payload']['related_client_id'] = str(uuid4())
        return page
    history.list_history_for_client = corrupt
    with pytest.raises(ConstructionClientCardReadOperationError): await read_card(c)


@pytest.mark.asyncio
async def test_postgresql_projection_keeps_eight_audit_columns_and_narrow_guarded_join():
    from unittest.mock import AsyncMock
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[]))))
    repo = ConstructionClientHistoryRepository(session)
    await repo.list_history_for_client(tenant_id=TENANT, client_id=CLIENT)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert [column.name for column in statement.selected_columns][:8] == list(CLIENT_HISTORY_RECORD_FIELDS)
    assert 'LEFT OUTER JOIN construction_activities ON' in sql
    assert 'construction_activities.lead_id IS NULL' in sql and 'construction_activities.project_id IS NULL' in sql
    assert 'event_logs.created_at >= construction_activities.created_at' in sql
    assert 'construction_activities.deleted_at' not in sql
    assert 'construction_projects' not in sql and 'construction_leads' not in sql
