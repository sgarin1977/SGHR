"""Distinct request invalidation, conditional SQL and historical replay compatibility."""
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from database.models import ConstructionClient, ConstructionClientUserLinkRequest as Request
from database.repositories.construction_client_link_requests import ConstructionClientLinkRequestState as State, MAX_VERSION
from services.api_identity import ApiActorContext
from services.construction_client_link_confirmation import ConstructionClientLinkConfirmationDeniedError as Denied
from services.construction_client_link_issuance_workflow import ConstructionClientLinkIssuanceWorkflow
from services.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec
from test_construction_1a_client_link_request_model import requests_db, values, NOW
from test_construction_1a_client_link_request_repository import request_repo, arguments, get, caller_owns
from test_construction_1a_client_link_request_issuance import issuance
from test_construction_1a_client_link_confirmation import confirmation, confirm, sql_confirmation, seed_request
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_issuance_workflow import issuance_pipeline, invoke, snapshot
from test_construction_1a_client_confirmation_http import confirmation_http, post as confirm_post


def test_column_and_checks_match_applied_invalidation_schema():
    table = Request.__table__
    assert len(table.c) == 14 and len(table.constraints) == 12
    assert table.c.invalidated_at.nullable and table.c.invalidated_at.type.timezone is True
    assert table.c.invalidated_at.default is table.c.invalidated_at.server_default is None
    constraints = {c.name for c in table.constraints}
    assert {'ck_con_client_link_requests_invalidated_at','ck_con_client_link_requests_terminal'} <= constraints


@pytest.mark.parametrize('changes', [
    {'invalidated_at': NOW-timedelta(microseconds=1)},
    {'invalidated_at': NOW+timedelta(minutes=3)},
    {'invalidated_at': NOW, 'consumed_at': NOW},
])
def test_database_rejects_invalid_time_or_both_terminal_states(requests_db, changes):
    c = requests_db
    with pytest.raises(IntegrityError):
        with c.engine.begin() as connection:
            connection.execute(insert(Request.__table__).values(**values(c,**changes)))


@pytest.mark.asyncio
async def test_old_positional_state_construction_defaults_invalidation_to_none(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    legacy = [getattr(state,n) for n in state.__dataclass_fields__ if n != 'invalidated_at']
    assert State(*legacy) == state and state.invalidated_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'invalidated_at':NOW.replace(tzinfo=None)},
    {'invalidated_at':NOW-timedelta(microseconds=1)},
    {'invalidated_at':NOW+timedelta(minutes=3)},
    {'invalidated_at':NOW,'consumed_at':NOW},
])
async def test_typed_state_rejects_invalid_invalidation_before_sql(request_repo, change):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    with pytest.raises(ValueError):replace(state, **change)


@pytest.mark.asyncio
async def test_invalidation_changes_only_terminal_time_version_and_updated_at(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    at = NOW+timedelta(seconds=1)
    invalidated = await c.repo.invalidate_request(state=state,as_of=at)
    assert invalidated == replace(state,invalidated_at=at,updated_at=at,row_version=2)
    assert invalidated.consumed_at is None and invalidated.expires_at == state.expires_at
    sql = str(c.statements[-1].compile(dialect=postgresql.dialect())).lower()
    changed = sql.split(' set ',1)[1].split(' where ',1)[0]
    assert changed.count('=') == 3
    assert all(name+'=' in changed for name in ('invalidated_at','updated_at','row_version'))
    assert 'invalidated_at is null' in sql and 'consumed_at is null' in sql
    assert 'token_hash =' in sql and 'row_version =' in sql
    assert await get(c,state) == invalidated
    assert c.sync.execute(select(ConstructionClient.linked_user_id).where(ConstructionClient.id==c.client)).scalar_one() is None
    caller_owns(c)


@pytest.mark.asyncio
async def test_invalidation_prevents_consumption_even_with_an_older_active_snapshot(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    invalidated = await c.repo.invalidate_request(state=state,as_of=NOW)
    assert await c.repo.consume_request(state=state,as_of=NOW) is None
    sql = str(c.statements[-1].compile(dialect=postgresql.dialect())).lower()
    assert 'invalidated_at is null' in sql
    before = len(c.statements)
    assert await c.repo.consume_request(state=invalidated,as_of=NOW) is None
    assert await c.repo.invalidate_request(state=invalidated,as_of=NOW) is None
    assert len(c.statements) == before
    assert await c.repo.invalidate_request(state=state,as_of=NOW) is None
    assert await get(c,state) == invalidated


@pytest.mark.asyncio
async def test_consumed_request_cannot_be_invalidated_by_any_snapshot(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    consumed = await c.repo.consume_request(state=state,as_of=NOW)
    assert await c.repo.invalidate_request(state=state,as_of=NOW) is None
    before = len(c.statements)
    assert await c.repo.invalidate_request(state=consumed,as_of=NOW) is None
    assert len(c.statements) == before and await get(c,state) == consumed


@pytest.mark.asyncio
@pytest.mark.parametrize('as_of',[NOW-timedelta(microseconds=1),NOW+timedelta(minutes=3)])
async def test_invalidation_obeys_live_time_window_without_a_write(request_repo,as_of):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    c.statements.clear()
    assert await c.repo.invalidate_request(state=state,as_of=as_of) is None
    assert c.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', ['tenant','client','target','initiator','version','hash'])
async def test_altered_request_snapshot_cannot_invalidate_another_context(request_repo,changes):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    replacement = {'tenant':{'tenant_id':c.foreign},'client':{'client_id':c.other_client},
        'target':{'target_user_id':c.foreign_user},'initiator':{'initiated_by_user_id':c.foreign_user},
        'version':{'row_version':2},'hash':{'token_hash':'0'*64}}[changes]
    assert await c.repo.invalidate_request(state=replace(state,**replacement),as_of=NOW) is None
    assert await get(c,state) == state


@pytest.mark.asyncio
async def test_stale_client_base_does_not_preserve_a_live_old_token(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(
        row_version=2,linked_user_id=c.foreign_user))
    invalidated = await c.repo.invalidate_request(state=state,as_of=NOW)
    assert invalidated.invalidated_at == NOW and invalidated.expected_client_row_version == 1
    assert c.sync.execute(select(ConstructionClient.linked_user_id).where(ConstructionClient.id==c.client)).scalar_one() == c.foreign_user


@pytest.mark.asyncio
async def test_caller_rollback_restores_pending_state_and_version(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    c.sync.commit(); c.sync.begin()
    assert (await c.repo.invalidate_request(state=state,as_of=NOW)).row_version == 2
    c.sync.rollback()
    c.sync.begin()
    assert await get(c,state) == state
    caller_owns(c)


@pytest.mark.asyncio
async def test_active_list_is_locked_scoped_and_excludes_all_terminal_or_nonlive_requests(request_repo):
    c = request_repo
    active = [await c.repo.create_request(**arguments(c)) for _ in range(2)]
    consumed = await c.repo.create_request(**arguments(c))
    await c.repo.consume_request(state=consumed,as_of=NOW)
    invalidated = await c.repo.create_request(**arguments(c))
    await c.repo.invalidate_request(state=invalidated,as_of=NOW)
    await c.repo.create_request(**arguments(c,issued_at=NOW-timedelta(minutes=2),expires_at=NOW-timedelta(seconds=1)))
    await c.repo.create_request(**arguments(c,issued_at=NOW+timedelta(seconds=1),expires_at=NOW+timedelta(minutes=3)))
    await c.repo.create_request(**arguments(c,client_id=c.other_client))
    await c.repo.create_request(**arguments(c,target_user_id=c.foreign_user))
    states = await c.repo.list_active_requests_for_update(tenant_id=c.tenant,client_id=c.client,target_user_id=c.target,as_of=NOW)
    assert states == tuple(sorted(active,key=lambda s:(s.issued_at,s.request_id)))
    query = c.statements[-1]
    sql = str(query.compile(dialect=postgresql.dialect())).lower()
    assert 'for update' in sql and 'skip locked' not in sql and 'limit' not in sql
    assert 'invalidated_at is null' in sql and 'consumed_at is null' in sql
    assert query.get_execution_options()['autoflush'] is False
    assert await c.repo.list_active_requests_for_update(tenant_id=c.foreign,client_id=c.client,target_user_id=c.target,as_of=NOW) == ()
    caller_owns(c)


@pytest.mark.asyncio
async def test_maximum_version_and_missing_transaction_do_not_write(request_repo):
    c = request_repo
    state = await c.repo.create_request(**arguments(c))
    maximum = replace(state,row_version=MAX_VERSION)
    c.statements.clear()
    with pytest.raises(ValueError):await c.repo.invalidate_request(state=maximum,as_of=NOW)
    assert c.statements == []
    c.sync.rollback()
    for action in (
        lambda:c.repo.invalidate_request(state=state,as_of=NOW),
        lambda:c.repo.list_active_requests_for_update(tenant_id=c.tenant,client_id=c.client,target_user_id=c.target,as_of=NOW)):
        with pytest.raises(ValueError):await action()
    assert c.statements == []


@pytest.mark.asyncio
async def test_confirmation_service_rejects_invalidated_token_before_version_or_write(confirmation):
    c = confirmation
    c.request = replace(c.request,invalidated_at=c.now,updated_at=c.now,row_version=2)
    with pytest.raises(Denied):await confirm(c)
    c.requests.consume_request.assert_not_awaited()
    c.clients.change_linked_user.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_encrypted_issuance_snapshot_replays_without_being_rewritten(issuance_pipeline):
    p = issuance_pipeline
    first = await invoke(p)
    row = p.ledger.all()[0]
    receipt = p.codec.decrypt(row.response_ciphertext)
    assert receipt['material']['request'].pop('invalidated_at') is None
    row.response_ciphertext = p.codec.encrypt(receipt)
    p.ledger.save(row); p.c.sync.commit()
    before = snapshot(p)
    assert await invoke(p) == first
    assert snapshot(p) == before
    assert 'invalidated_at' not in p.codec.decrypt(p.ledger.all()[0].response_ciphertext)['material']['request']


@pytest.mark.asyncio
async def test_issuance_replay_returns_original_receipt_without_reviving_invalidated_token(issuance_pipeline):
    p, c = issuance_pipeline, issuance_pipeline.c
    first = await invoke(p)
    c.now += timedelta(seconds=1)
    if not c.sync.in_transaction():c.sync.begin()
    assert (await c.requests.invalidate_request(state=first.request,as_of=c.now)).invalidated_at == c.now
    c.sync.commit()
    before = snapshot(p)
    assert await invoke(p) == first and snapshot(p) == before
    envelope = ConstructionClientLinkRecipientContextCodec(encryption_key=p.key).issue_for_request(material=first)
    if not c.sync.in_transaction():c.sync.begin()
    with pytest.raises(Denied):
        await p.confirm_workflow.confirm_request(actor=ApiActorContext(c.target,None,None,(),'ru',None,'active'),
            recipient_context=envelope.recipient_context,token=envelope.token,idempotency_key='invalidated-confirmation')
    assert snapshot(p) == before


@pytest.mark.asyncio
async def test_terminal_material_is_not_a_new_recipient_envelope(issuance_pipeline):
    p = issuance_pipeline
    first = await invoke(p)
    state = replace(first.request,invalidated_at=first.request.issued_at)
    with pytest.raises(ValueError):
        ConstructionClientLinkRecipientContextCodec(encryption_key=p.key).issue_for_request(material=replace(first,request=state))


@pytest.mark.asyncio
async def test_http_rejects_invalidated_token_without_a_second_write(confirmation_http):
    p, c = confirmation_http, confirmation_http.c
    c.sync.execute(update(Request.__table__).where(Request.id==c.request_id).values(
        invalidated_at=c.now,updated_at=c.now,row_version=2))
    c.sync.commit()
    before = snapshot(p)
    response = await confirm_post(p)
    assert response.status_code == 403, response.text
    assert snapshot(p) == before
    assert c.service.confirm_request.await_count == 1
