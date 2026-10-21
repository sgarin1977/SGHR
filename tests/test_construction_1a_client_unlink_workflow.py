from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select,text,update

from database.models import ConstructionClient,ConstructionClientUserLinkRequest as Request
from services.api_identity import ApiActorContext,ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_client_unlink import ConstructionClientUnlinkService as Service,ConstructionClientUnlinkDeniedError
from services.construction_client_unlink_workflow import ConstructionClientUnlinkWorkflow as Workflow,build_construction_client_unlink_workflow,UNLINK
from services.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction_clients import ConstructionClientOperationError,ConstructionClientValidationError
from services.construction_permissions import ConstructionAccessContext,ConstructionGrantContext,ConstructionPermissionDeniedError,ConstructionScopeDeniedError
from test_construction_1a_client_link_confirmation_workflow import pipeline,invoke as confirm_pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation,seed_request,NOW
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db


@pytest.fixture
async def unlink_pipeline(pipeline):
    p,c=pipeline,pipeline.c
    await confirm_pipeline(p)
    p.service=Service(session=c.session,repository=c.clients,access_repository=c.access,event_repository=c.events,
        identity_reader=c.identity,now_provider=lambda:c.now)
    p.service.unlink_self=AsyncMock(wraps=p.service.unlink_self)
    p.service.unlink_as_editor=AsyncMock(wraps=p.service.unlink_as_editor)
    p.workflow=Workflow(session=c.session,unlink_service=p.service,idempotency_service=p.idempotency,now_provider=lambda:c.now)
    c.editor=ApiActorContext(c.operator,c.tenant,None,(),'ru',None,'active')
    c.editor_context=ConstructionAccessContext(c.operator,c.tenant,('OWNER',),('construction.projects.edit',),
        (ConstructionGrantContext('OWNER','tenant',c.tenant),))
    c.session.commit.reset_mock();c.session.rollback.reset_mock()
    c.access.has_active_platform_membership.reset_mock();c.access.list_active_grants.reset_mock()
    return p


async def unlink(p,**changes):
    c=p.c
    if not c.sync.in_transaction():c.sync.begin()
    kw=dict(actor=c.actor,tenant_id=c.tenant,client_id=c.client,expected_row_version=2,
        idempotency_key='client-unlink-key',trace_id='self-unlink-workflow')
    kw.update(changes)
    return await p.workflow.unlink_self(**kw)


async def edit(p,**changes):
    c=p.c
    if not c.sync.in_transaction():c.sync.begin()
    kw=dict(actor=c.editor,access_context=c.editor_context,client_id=c.client,expected_row_version=2,
        idempotency_key='client-unlink-key',trace_id='editor-unlink-workflow')
    kw.update(changes)
    return await p.workflow.unlink_as_editor(**kw)


def snapshot(p):
    c=p.c
    return (c.sync.execute(select(ConstructionClient.__table__).order_by(ConstructionClient.id)).all(),
        c.sync.execute(select(Request.__table__).order_by(Request.id)).all(),
        c.sync.execute(text('SELECT * FROM users ORDER BY id')).all(),
        c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one(),
        c.sync.execute(text('SELECT data FROM client_link_replay_ledger ORDER BY id')).all())


@pytest.mark.asyncio
async def test_self_unlink_commits_link_audit_and_encrypted_receipt_without_membership(unlink_pipeline):
    p,c=unlink_pipeline,unlink_pipeline.c
    requests_before=c.sync.execute(select(Request.__table__)).all()
    users_before=c.sync.execute(text('SELECT * FROM users ORDER BY id')).all()
    c.access.has_active_platform_membership.side_effect=AssertionError('No membership required.')
    c.access.list_active_grants.side_effect=AssertionError('No Construction grants required.')
    result=await unlink(p)
    assert result.changed and result.previous_linked_user_id==c.target and result.linked_user_id is None and result.row_version==3
    assert c.sync.get(ConstructionClient,c.client).linked_user_id is None
    assert c.sync.execute(select(Request.__table__)).all()==requests_before
    assert c.sync.execute(text('SELECT * FROM users ORDER BY id')).all()==users_before
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==3
    rows=[r for r in p.ledger.all() if r.operation==UNLINK]
    assert len(rows)==1 and rows[0].principal_id==c.target and rows[0].response_status==200
    saved=p.codec.decrypt(rows[0].response_ciphertext)
    assert saved['binding']['principal_id']==str(c.target) and saved['binding']['tenant_id']==str(c.tenant)
    assert saved['data']['previous_linked_user_id']==str(c.target)
    assert 'token' not in json.dumps(saved) and 'recipient_context' not in json.dumps(saved)
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_self_replay_returns_original_receipt_after_link_has_disappeared_without_new_audit(unlink_pipeline):
    p=unlink_pipeline
    first=await unlink(p)
    before=snapshot(p)
    assert await unlink(p,trace_id='response-lost-retry')==first
    assert snapshot(p)==before and p.service.unlink_self.await_count==1
    assert p.c.session.commit.await_count==2


@pytest.mark.asyncio
@pytest.mark.parametrize('later_change',['other_user','same_user_relinked','archive'])
async def test_self_replay_never_unlinks_or_reversions_a_later_client_state(unlink_pipeline,later_change):
    p,c=unlink_pipeline,unlink_pipeline.c
    first=await unlink(p)
    values={'row_version':4}
    if later_change=='archive':values['deleted_at']=NOW+timedelta(seconds=2)
    else:values['linked_user_id']=c.foreign_user if later_change=='other_user' else c.target
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(**values))
    c.sync.commit()
    before=snapshot(p)
    assert await unlink(p)==first and snapshot(p)==before
    assert p.service.unlink_self.await_count==1


@pytest.mark.asyncio
async def test_editor_fresh_unlink_and_replay_have_one_business_mutation(unlink_pipeline):
    p=unlink_pipeline
    first=await edit(p)
    before=snapshot(p)
    assert first.changed and first.row_version==3
    assert await edit(p)==first and snapshot(p)==before
    assert p.service.unlink_as_editor.await_count==1
    assert p.c.audit_rows[-1].payload['identity_link_action']=='editor_unlink'


@pytest.mark.asyncio
async def test_editor_noop_replay_does_not_remove_a_link_created_after_the_original_noop(unlink_pipeline):
    p,c=unlink_pipeline,unlink_pipeline.c
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(linked_user_id=None,row_version=3))
    c.sync.commit()
    first=await edit(p,expected_row_version=3)
    assert not first.changed and first.audit_id is None and first.row_version==3
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(linked_user_id=c.target,row_version=4))
    c.sync.commit()
    before=snapshot(p)
    assert await edit(p,expected_row_version=3)==first and snapshot(p)==before
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==2


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['identity','membership','revoked','scope'])
async def test_editor_replay_refreshes_current_identity_and_edit_authority(unlink_pipeline,denial):
    p,c=unlink_pipeline,unlink_pipeline.c
    await edit(p)
    before=snapshot(p)
    if denial=='identity':c.identity.require_active_user.side_effect=ApiIdentityAccessError('inactive')
    elif denial=='membership':c.access.has_active_platform_membership.return_value=False
    elif denial=='revoked':c.grant.status='revoked'
    else:c.grant.scope_type,c.grant.scope_id='project',uuid4()
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError) if denial!='identity' else ApiIdentityAccessError):await edit(p)
    assert snapshot(p)==before and p.service.unlink_as_editor.await_count==1


@pytest.mark.asyncio
async def test_inactive_platform_identity_cannot_replay_its_previous_self_receipt(unlink_pipeline):
    p=unlink_pipeline
    await unlink(p)
    before=snapshot(p)
    p.c.identity.require_active_user.side_effect=ApiIdentityAccessError('inactive')
    with pytest.raises(ApiIdentityAccessError):await unlink(p)
    assert snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',['client','version'])
async def test_same_key_with_changed_request_is_key_reuse_conflict(unlink_pipeline,changes):
    p=unlink_pipeline
    await unlink(p)
    before=snapshot(p)
    kw={'client_id':p.c.other_client} if changes=='client' else {'expected_row_version':3}
    with pytest.raises(ApiIdempotencyKeyReusedError):await unlink(p,**kw)
    assert snapshot(p)==before


@pytest.mark.asyncio
async def test_mode_switch_cannot_reuse_an_editor_receipt_as_proof_of_self_ownership(unlink_pipeline):
    p=unlink_pipeline
    await edit(p)
    before=snapshot(p)
    with pytest.raises(ApiIdempotencyKeyReusedError):await unlink(p,actor=p.c.editor)
    assert snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['complete','commit','wrong_completion_id','audit','final_identity','cancel_complete'])
async def test_required_failure_rolls_back_client_unlink_audit_and_replay_reservation(unlink_pipeline,failure):
    p,c=unlink_pipeline,unlink_pipeline.c
    before=snapshot(p)
    if failure=='complete':p.flags.fail_complete=True
    elif failure=='commit':p.flags.fail_commit=True
    elif failure=='wrong_completion_id':p.flags.corrupt_complete=True
    elif failure=='cancel_complete':p.flags.cancel_complete=True
    elif failure=='audit':c.events.create_event.side_effect=RuntimeError('private audit information')
    else:
        original=p.ledger.complete_record
        async def complete(**kw):
            row=await original(**kw)
            c.identity.require_active_user.side_effect=ApiIdentityAccessError('inactive')
            return row
        p.ledger.complete_record=complete
    if failure=='cancel_complete':
        import asyncio
        expected=asyncio.CancelledError
    else:expected=ApiIdentityAccessError if failure=='final_identity' else ConstructionClientOperationError
    with pytest.raises(expected) as caught:await unlink(p)
    assert 'private' not in str(caught.value)
    assert snapshot(p)==before


@pytest.mark.asyncio
async def test_wrong_platform_user_cannot_read_another_users_unlink_receipt_or_unlink_empty_client(unlink_pipeline):
    p=unlink_pipeline
    await unlink(p)
    before=snapshot(p)
    with pytest.raises(ConstructionClientUnlinkDeniedError):await unlink(p,actor=replace(p.c.actor,user_id=p.c.foreign_user))
    assert snapshot(p)==before


@pytest.mark.asyncio
async def test_new_key_or_expired_key_cannot_claim_a_new_self_unlink_after_ownership_is_gone(unlink_pipeline):
    p=unlink_pipeline
    await unlink(p)
    before=snapshot(p)
    with pytest.raises(ConstructionClientUnlinkDeniedError):await unlink(p,idempotency_key='new-key')
    assert snapshot(p)==before
    p.c.now=NOW+timedelta(hours=25)
    with pytest.raises(ConstructionClientUnlinkDeniedError):await unlink(p)
    assert snapshot(p)==before


@pytest.mark.asyncio
async def test_without_a_key_self_unlink_remains_protected_by_current_ownership_and_version(unlink_pipeline):
    p=unlink_pipeline
    first=await unlink(p,idempotency_key=None)
    assert first.changed and not [r for r in p.ledger.all() if r.operation==UNLINK]
    before=snapshot(p)
    with pytest.raises(ConstructionClientUnlinkDeniedError):await unlink(p,idempotency_key=None)
    assert snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['previous_user','version','binding','changed_flag'])
async def test_invalid_historical_receipt_cannot_authorize_self_replay(unlink_pipeline,bad):
    p=unlink_pipeline
    await unlink(p)
    row=next(r for r in p.ledger.all() if r.operation==UNLINK)
    saved=p.codec.decrypt(row.response_ciphertext)
    if bad=='previous_user':saved['data']['previous_linked_user_id']=str(p.c.foreign_user)
    elif bad=='version':saved['data']['row_version']=900
    elif bad=='binding':saved['binding']['principal_id']=str(p.c.foreign_user)
    else:saved['data']['changed']=1
    row.response_ciphertext=p.codec.encrypt(saved)
    p.ledger.save(row);p.c.sync.commit()
    before=snapshot(p)
    with pytest.raises(ConstructionClientOperationError):await unlink(p)
    assert snapshot(p)==before and p.service.unlink_self.await_count==1


@pytest.mark.asyncio
async def test_busy_reservation_never_calls_live_unlink_again(unlink_pipeline):
    p=unlink_pipeline
    await unlink(p)
    row=next(r for r in p.ledger.all() if r.operation==UNLINK)
    row.status='processing';row.response_status=row.response_ciphertext=None
    p.ledger.save(row);p.c.sync.commit()
    before=snapshot(p)
    with pytest.raises(ConstructionClientLinkIdempotencyInProgressError):await unlink(p)
    assert snapshot(p)==before and p.service.unlink_self.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',[{'expected_row_version':True},{'expected_row_version':0},{'tenant_id':'caller-tenant'},
    {'client_id':'client'},{'idempotency_key':''},{'idempotency_key':'x'*256},{'trace_id':' '}])
async def test_invalid_request_does_not_reserve_or_unlink(unlink_pipeline,bad):
    p=unlink_pipeline
    calls=p.ledger.calls
    with pytest.raises(ConstructionClientValidationError):await unlink(p,**bad)
    assert p.ledger.calls==calls and p.service.unlink_self.await_count==0


def test_factory_reuses_existing_idempotency_and_separate_platform_connection(unlink_pipeline):
    p,c=unlink_pipeline,unlink_pipeline.c
    platform=SimpleNamespace(execute=AsyncMock(),no_autoflush=nullcontext())
    workflow=build_construction_client_unlink_workflow(session=c.session,platform_session=platform,encryption_key=p.key)
    assert workflow.service.session is workflow.idempotency.repository.session is c.session
    assert workflow.service.identity.session is platform
