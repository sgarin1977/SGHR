import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from services.construction.construction_lead_create import ConstructionLeadOperationError
from services.construction.construction_lead_status import ConstructionLeadStatusCommand, ConstructionLeadStatusOperationError
from services.construction.construction_lead_conversion import ConstructionLeadConversionOperationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from test_construction_1a_lead_create_service import lead_create_case, create
from test_construction_1a_lead_status_service import status_case, run as status_run
from test_construction_1a_lead_conversion_service import conversion_case, sql_conversion, run as convert_run
from test_construction_1a_lead_conversion_workflow import workflow_case, run as workflow_run


@pytest.mark.asyncio
async def test_new_lead_does_not_guess_client_from_contacts(lead_create_case):
    c = lead_create_case
    await create(c)
    payload = c.events.create_event.await_args.kwargs['payload']
    assert payload == dict(operation='create', changes=[], reason=None)
    assert c.row.converted_client_id is None
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_creation_cannot_attribute_client_from_invalid_created_row(lead_create_case):
    c = lead_create_case
    original = c.repo.create_lead.side_effect
    async def invalid(**kwargs):
        row = await original(**kwargs)
        row.converted_client_id = uuid4()
        return row
    c.repo.create_lead.side_effect = invalid
    with pytest.raises(ConstructionLeadOperationError):
        await create(c)
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('linked', [False, True])
async def test_status_snapshots_only_persisted_client_reference(status_case, linked):
    # This tests correlation of stored rows, without introducing reopening or relinking.
    c = status_case
    client_id = uuid4() if linked else None
    c.lead.converted_client_id = client_id
    result = await status_run(c)
    event = c.events.create_event.await_args.kwargs
    assert event['payload']['changes'] == [dict(field='status', old_value='contacted', new_value='measurement_scheduled')]
    if linked:
        assert event['payload']['related_client_id'] == str(client_id)
        result.lead.converted_client_id = uuid4()
        assert event['payload']['related_client_id'] == str(client_id)
    else:
        assert 'related_client_id' not in event['payload']
    assert c.events.create_event.await_count == 1
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', ['bad', 123, True])
async def test_status_invalid_correlation_reference_cannot_write_audit(status_case, reference):
    c = status_case
    c.lead.converted_client_id = reference
    with pytest.raises(ConstructionLeadStatusOperationError):
        await status_run(c)
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('stale', [False, True])
async def test_linked_terminal_no_op_or_stale_has_no_new_audit(status_case, stale):
    c = status_case
    c.lead.status = 'won'
    c.lead.converted_client_id = uuid4()
    command = ConstructionLeadStatusCommand(3 if stale else 4, 'won')
    if stale:
        with pytest.raises(ConstructionRowVersionConflictError):
            await status_run(c, command=command)
    else:
        result = await status_run(c, command=command)
        assert not result.changed and result.lead.row_version == 4
    c.events.create_event.assert_not_called()
    c.repo.transition_status.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['new', 'client', 'existing'])
async def test_conversion_final_event_snapshots_validated_completed_client(conversion_case, mode):
    c = conversion_case
    result = await convert_run(c, mode)
    event = c.events.create_event.await_args.kwargs
    assert event['event_type'] == 'construction_lead_converted'
    assert event['payload']['related_client_id'] == str(result.lead.converted_client_id) == str(result.client.id)
    assert event['payload']['created_client_id'] == (str(c.client.id) if mode == 'new' else None)
    assert event['payload']['created_project_id'] == (str(c.project.id) if mode != 'existing' else None)
    assert c.events.create_event.await_count == 1
    before = json.dumps(event['payload'], sort_keys=True)
    result.project.client_id = uuid4()
    result.lead.converted_client_id = uuid4()
    assert json.dumps(event['payload'], sort_keys=True) == before
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['new', 'client', 'existing'])
async def test_idempotency_replay_does_not_replace_correlation_or_snapshot(workflow_case, mode):
    c = workflow_case
    first = await workflow_run(c, mode)
    event = c.events.create_event.await_args.kwargs
    payload = json.dumps(event['payload'], sort_keys=True)
    record = next(iter(c.records.rows.values()))
    encrypted = record.response_ciphertext
    c.lead.row_version = 9
    assert await workflow_run(c, mode) == first
    assert c.events.create_event.await_count == 1
    assert event['payload']['related_client_id'] == first['client_id']
    assert json.dumps(event['payload'], sort_keys=True) == payload
    assert record.response_ciphertext == encrypted
    assert 'related_client_id' not in first


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['new', 'client', 'existing'])
async def test_real_conversion_commits_one_correlated_audit_and_preserves_legacy(sql_conversion, mode):
    c = sql_conversion
    legacy = '{"operation":"convert","reason":null,"changes":[]}'
    with c.sync.begin():
        c.sync.execute(text('INSERT INTO conversion_test_audit VALUES(:id,:event,:payload)'),
            dict(id='legacy-event', event='construction_lead_converted', payload=legacy))
        result = await convert_run(c, mode)
        expected_client = str(result.client.id)
    rows = dict(c.sync.execute(text('SELECT id,payload FROM conversion_test_audit')).all())
    assert rows['legacy-event'] == legacy
    assert len(rows) == 2
    new = next(payload for key, payload in rows.items() if key != 'legacy-event')
    assert json.loads(new)['related_client_id'] == expected_client
    c.sync.rollback()
    with c.sync.begin():
        replay = await convert_run(c, mode)
        assert replay.is_replay
    assert dict(c.sync.execute(text('SELECT id,payload FROM conversion_test_audit')).all()) == rows


@pytest.mark.asyncio
async def test_real_correlated_audit_failure_rolls_back_conversion(sql_conversion):
    c = sql_conversion
    c.events.fail_after_insert = True
    with pytest.raises(ConstructionLeadConversionOperationError):
        with c.sync.begin():
            await convert_run(c)
    assert c.sync.execute(text('SELECT count(*) FROM conversion_test_audit')).scalar_one() == 0
    row = c.sync.execute(text('SELECT status, converted_client_id, converted_project_id, row_version FROM construction_leads')).one()
    assert tuple(row) == ('contacted', None, None, 4)
    assert c.sync.execute(text('SELECT count(*) FROM construction_clients')).scalar_one() == 1
    assert c.sync.execute(text('SELECT count(*) FROM construction_projects')).scalar_one() == 1
