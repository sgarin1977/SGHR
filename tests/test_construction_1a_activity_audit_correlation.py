import json
from itertools import product
from uuid import uuid4

import pytest
from sqlalchemy import text

from database.models import ConstructionLead
from services.construction_activity_rules import ConstructionActivityLinks
from services.construction_activity_create import ConstructionActivityCreateOperationError
from services.construction_activity_terminal import ConstructionActivityTerminalOperationError
from test_construction_1a_activity_create_service import (
    activity_create_case, sql_activity_create, command, run as create_run,
)
from test_construction_1a_activity_terminal_service import terminal_case, sql_terminal, run as terminal_run
from test_construction_1a_activity_model import activity_db
from test_construction_1a_lead_model import lead_db
from test_construction_1a_activity_create_workflow import workflow_case as create_workflow_case, run as create_workflow_run
from test_construction_1a_activity_terminal_workflow import workflow_case as terminal_workflow_case, run as terminal_workflow_run


LINKS = [flags for flags in product((False, True), repeat=3) if any(flags)]


def configure(c, flags, terminal=False):
    c.lead.converted_client_id = c.client.id
    c.project.client_id = c.client.id
    links = ConstructionActivityLinks(*[getattr(c, name).id if selected else None
        for name, selected in zip(('lead', 'client', 'project'), flags)])
    if terminal:
        for field in ('lead_id', 'client_id', 'project_id'):
            setattr(c.row, field, getattr(links, field))
    return links


def only_linked_locks(c, flags):
    for name, selected in zip(('lead', 'client', 'project'), flags):
        method = getattr(getattr(c, name+'_repo'), 'get_active_'+name+'_for_update')
        assert method.await_count == int(selected)


@pytest.mark.asyncio
@pytest.mark.parametrize('flags', LINKS)
async def test_create_correlates_all_link_combinations_without_new_reads(activity_create_case, flags):
    c = activity_create_case
    links = configure(c, flags)
    row = await create_run(c, command=command(c, links=links))
    payload = c.events.create_event.await_args.kwargs['payload']
    assert payload == dict(operation='create', changes=[], reason=None, related_client_id=str(c.client.id))
    only_linked_locks(c, flags)
    assert c.events.create_event.await_count == 1
    c.lead.converted_client_id = c.project.client_id = uuid4()
    row.client_id = uuid4()
    assert payload['related_client_id'] == str(c.client.id)
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['complete', 'cancel'])
@pytest.mark.parametrize('flags', LINKS)
async def test_terminal_correlates_all_link_combinations_preserving_result_audit(terminal_case, operation, flags):
    c = terminal_case
    configure(c, flags, terminal=True)
    row = await terminal_run(c, operation)
    event = c.events.create_event.await_args.kwargs
    assert event['payload']['related_client_id'] == str(c.client.id)
    assert event['payload']['result_changed'] is True
    assert event['payload']['operation'] == operation
    assert len(event['payload']['changes']) == 2
    assert 'Private' not in json.dumps(event['payload'])
    only_linked_locks(c, flags)
    assert c.events.create_event.await_count == 1
    row.client_id = c.lead.converted_client_id = c.project.client_id = uuid4()
    assert event['payload']['related_client_id'] == str(c.client.id)
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['create', 'complete', 'cancel'])
@pytest.mark.parametrize('evidence', ['unknown', 'conflicting'])
async def test_unknown_or_conflicting_client_attribution_is_not_guessed(activity_create_case, terminal_case, operation, evidence):
    c = activity_create_case if operation == 'create' else terminal_case
    links = configure(c, (True, False, True), terminal=operation != 'create')
    c.lead.converted_client_id = None if evidence == 'unknown' else uuid4()
    c.project.client_id = None if evidence == 'unknown' else uuid4()
    if operation == 'create':
        await create_run(c, command=command(c, links=links))
    else:
        await terminal_run(c, operation)
    assert 'related_client_id' not in c.events.create_event.await_args.kwargs['payload']
    assert c.events.create_event.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['create', 'complete', 'cancel'])
@pytest.mark.parametrize('parent', ['lead', 'project'])
async def test_malformed_correlation_evidence_fails_without_audit_or_commit(activity_create_case, terminal_case, operation, parent):
    c = activity_create_case if operation == 'create' else terminal_case
    flags = (parent == 'lead', False, parent == 'project')
    links = configure(c, flags, terminal=operation != 'create')
    setattr(getattr(c, parent), 'converted_client_id' if parent == 'lead' else 'client_id', 'untrusted-uuid-string')
    error = ConstructionActivityCreateOperationError if operation == 'create' else ConstructionActivityTerminalOperationError
    with pytest.raises(error):
        if operation == 'create':
            await create_run(c, command=command(c, links=links))
        else:
            await terminal_run(c, operation)
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_create_replay_retains_original_event_before_later_lead_conversion(create_workflow_case):
    c = create_workflow_case
    first = await create_workflow_run(c)
    event = c.events.create_event.await_args.kwargs
    assert 'related_client_id' not in event['payload']
    record = next(iter(c.records.rows.values()))
    ciphertext = record.response_ciphertext
    c.lead.converted_client_id = c.client.id
    assert await create_workflow_run(c) == first
    assert c.events.create_event.await_count == 1
    assert 'related_client_id' not in event['payload']
    assert record.response_ciphertext == ciphertext


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['complete', 'cancel'])
async def test_terminal_replay_keeps_operation_time_client_and_encrypted_response(terminal_workflow_case, operation):
    c = terminal_workflow_case
    configure(c, (True, False, False), terminal=True)
    first = await terminal_workflow_run(c, operation)
    event = c.events.create_event.await_args.kwargs
    marker = str(c.client.id)
    record = next(iter(c.records.rows.values()))
    encrypted = record.response_ciphertext
    c.lead.converted_client_id = uuid4()
    assert await terminal_workflow_run(c, operation) == first
    assert event['payload']['related_client_id'] == marker
    assert c.events.create_event.await_count == 1
    assert record.response_ciphertext == encrypted
    assert 'related_client_id' not in first


def converted_fixture(c, ids):
    # Fixture setup models a completed conversion; it does not expose a new operation.
    c.session.sync.execute(ConstructionLead.__table__.update().where(
        ConstructionLead.id == c.lead.id).values(status='won', converted_client_id=ids['client_a'],
        converted_project_id=ids['project_a']))


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['create', 'complete', 'cancel'])
async def test_real_domain_and_correlated_audit_commit_preserving_old_events(sql_activity_create, sql_terminal, activity_db, operation):
    c = sql_activity_create if operation == 'create' else sql_terminal
    _, ids = activity_db
    table = 'activity_creation_audit' if operation == 'create' else 'terminal_service_audit'
    legacy = '{"operation":"create","changes":[],"reason":null}'
    with c.session.sync.begin():
        converted_fixture(c, ids)
        c.session.sync.execute(text('INSERT INTO '+table+' VALUES(:id,:payload)'), dict(id='legacy', payload=legacy))
        row = await create_run(c) if operation == 'create' else await terminal_run(c, operation)
        identifier = row.id
    rows = dict(c.session.sync.execute(text('SELECT id,payload FROM '+table)).all())
    assert rows['legacy'] == legacy and len(rows) == 2
    new = next(payload for key, payload in rows.items() if key != 'legacy')
    assert json.loads(new)['related_client_id'] == str(ids['client_a'])
    status = c.session.sync.execute(text('SELECT status FROM construction_activities WHERE id=:id'),
        dict(id=identifier.hex)).scalar_one()
    assert status == {'create': 'planned', 'complete': 'completed', 'cancel': 'cancelled'}[operation]


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['create', 'complete', 'cancel'])
async def test_actual_correlated_audit_insert_failure_rolls_back_activity(sql_activity_create, sql_terminal, activity_db, operation):
    c = sql_activity_create if operation == 'create' else sql_terminal
    _, ids = activity_db
    table = 'activity_creation_audit' if operation == 'create' else 'terminal_service_audit'
    c.events.fail = True
    with pytest.raises(ConstructionActivityCreateOperationError if operation == 'create' else ConstructionActivityTerminalOperationError):
        with c.session.sync.begin():
            converted_fixture(c, ids)
            if operation == 'create':
                await create_run(c)
            else:
                await terminal_run(c, operation)
    assert c.session.sync.execute(text('SELECT count(*) FROM '+table)).scalar_one() == 0
    if operation == 'create':
        # sql_terminal has seeded its own planned row in the same fixture database.
        assert c.session.sync.execute(text('SELECT count(*) FROM construction_activities')).scalar_one() == 1
    else:
        row = c.session.sync.execute(text('SELECT status,row_version FROM construction_activities')).one()
        assert tuple(row) == ('planned', 4)
