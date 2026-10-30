import asyncio
from base64 import urlsafe_b64encode
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select,text,update
from sqlalchemy.orm import Session

from database.models import ConstructionClient,ConstructionClientUserLinkRequest as Request
from database.repositories.construction.construction_client_user_links import ConstructionClientUserLinkMutation as Mutation,MAX_CLIENT_ROW_VERSION
from services.api_identity import ApiActorContext,ApiIdentityAccessError
from services.construction.construction_client_history_contract import construction_history_event_belongs_to_client
from services.construction.construction_client_link_request_issuance import hash_client_link_confirmation_token
from services.construction.construction_client_unlink import ConstructionClientUnlinkService as Service,ConstructionClientUnlinkDeniedError as Denied,build_construction_client_unlink_service
from services.construction.construction_clients import ConstructionClientNotFoundError,ConstructionClientOperationError,ConstructionClientValidationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionGrantContext,ConstructionPermissionDeniedError,ConstructionScopeDeniedError
from test_construction_1a_client_self_link import self_case,setup
from test_construction_1a_client_link_confirmation_workflow import pipeline,invoke as confirm_pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation,seed_request,NOW
from test_construction_1a_client_link_request_repository import request_repo,arguments
from test_construction_1a_client_link_request_model import requests_db


@pytest.fixture
def unlink_case(self_case):
    c=self_case
    c.state=replace(c.state,linked_user_id=c.target)
    c.actor=replace(c.actor,tenant_id=None)
    c.identity=SimpleNamespace(session=object(),require_active_user=AsyncMock(side_effect=lambda **kw:kw['user_id']))
    c.editor=ApiActorContext(c.other_user,c.tenant,None,(),'ru',None,'active')
    c.editor_context=replace(c.context,user_id=c.editor.user_id)
    c.unlink=Service(session=c.session,repository=c.repo,access_repository=c.access,event_repository=c.events,
        identity_reader=c.identity,now_provider=lambda:c.now)
    return c


async def unlink(c,**changes):
    fields=dict(actor=c.actor,tenant_id=c.tenant,client_id=c.client_id,expected_row_version=1,trace_id='self-unlink')
    fields.update(changes)
    return await c.unlink.unlink_self(**fields)


async def edit(c,**changes):
    fields=dict(actor=c.editor,access_context=c.editor_context,client_id=c.client_id,expected_row_version=1,trace_id='editor-unlink')
    fields.update(changes)
    return await c.unlink.unlink_as_editor(**fields)


@pytest.mark.asyncio
async def test_self_unlink_needs_only_current_link_ownership_and_active_platform_identity(unlink_case):
    c=unlink_case
    c.access.has_active_platform_membership.side_effect=AssertionError('Self unlink does not require membership.')
    c.access.list_active_grants.side_effect=AssertionError('Self unlink does not require Construction grants.')
    result=await unlink(c)
    assert result.changed and result.previous_linked_user_id==c.target and result.linked_user_id is None
    assert result.row_version==2 and c.state.linked_user_id is None
    assert c.repo.change_linked_user.await_args.kwargs==dict(tenant_id=c.tenant,client_id=c.client_id,
        expected_row_version=1,expected_linked_user_id=c.target,linked_user_id=None,as_of=c.now)
    event=c.audit_rows[0]
    assert event.id==result.audit_id and event.user_id==c.target and event.tenant_id==c.tenant
    assert event.event_type=='construction_client_updated' and event.entity_type=='construction_client'
    assert event.payload==dict(operation='update',changes=[
        dict(field='linked_user_id',old_value=str(c.target),new_value=None),
        dict(field='row_version',old_value=1,new_value=2)],reason=None,
        related_client_id=str(c.client_id),identity_link_action='self_unlink',authorization_method='authenticated_link_owner')
    assert construction_history_event_belongs_to_client(event,tenant_id=c.tenant,client_id=c.client_id)
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('role',['OWNER','ADMIN'])
async def test_editor_unlink_uses_fresh_existing_permission_and_scope(unlink_case,role):
    c=unlink_case
    c.grant.role=role
    result=await edit(c)
    assert result.changed and result.previous_linked_user_id==c.target and result.linked_user_id is None
    assert all(a.kwargs==dict(user_id=c.editor.user_id,tenant_id=c.tenant) for a in c.access.has_active_platform_membership.await_args_list)
    assert c.audit_rows[0].user_id==c.editor.user_id
    assert c.audit_rows[0].payload['identity_link_action']=='editor_unlink'
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('link',['foreign','null','missing'])
async def test_self_cannot_unlink_other_identity_or_infer_authority_from_empty_client_link(unlink_case,link):
    c=unlink_case
    c.state=None if link=='missing' else replace(c.state,linked_user_id=c.other_user if link=='foreign' else None)
    with pytest.raises(Denied):await unlink(c)
    c.repo.change_linked_user.assert_not_called();c.events.create_event.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_self_repeat_without_a_verified_workflow_receipt_does_not_get_implicit_edit_authority(unlink_case):
    c=unlink_case
    await unlink(c)
    with pytest.raises(Denied):await unlink(c,expected_row_version=2)
    assert c.repo.change_linked_user.await_count==1 and len(c.audit_rows)==1


@pytest.mark.asyncio
async def test_editor_noop_preserves_version_and_audit_but_stale_version_still_conflicts(unlink_case):
    c=unlink_case
    c.state=replace(c.state,linked_user_id=None,row_version=7)
    result=await edit(c,expected_row_version=7)
    assert not result.changed and result.row_version==7 and result.audit_id is None
    c.repo.change_linked_user.assert_not_called();c.events.create_event.assert_not_called()
    with pytest.raises(ConstructionRowVersionConflictError):await edit(c,expected_row_version=6)


@pytest.mark.asyncio
async def test_own_link_requires_current_version_before_unlink(unlink_case):
    c=unlink_case
    c.state=replace(c.state,row_version=3)
    with pytest.raises(ConstructionRowVersionConflictError):await unlink(c)
    c.repo.change_linked_user.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['permission','project_scope','wrong_user','wrong_tenant'])
async def test_editor_context_denial_precedes_client_lookup(unlink_case,denial):
    c=unlink_case
    context=c.editor_context
    if denial=='permission':context=replace(context,permissions=())
    elif denial=='project_scope':context=replace(context,grants=(ConstructionGrantContext('OWNER','project',uuid4()),))
    elif denial=='wrong_user':context=replace(context,user_id=c.target)
    else:context=replace(context,tenant_id=uuid4())
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await edit(c,access_context=context)
    c.repo.get_state_for_update.assert_not_called();c.repo.change_linked_user.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['membership','revoked','expired','scope','inactive'])
async def test_editor_refreshes_membership_grants_scope_and_canonical_identity(unlink_case,denial):
    c=unlink_case
    if denial=='membership':c.access.has_active_platform_membership.return_value=False
    elif denial=='revoked':c.grant.status='revoked'
    elif denial=='expired':c.grant.expires_at=c.now
    elif denial=='scope':c.grant.scope_type,c.grant.scope_id='project',uuid4()
    else:c.identity.require_active_user.side_effect=ApiIdentityAccessError('inactive')
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError,ApiIdentityAccessError)):await edit(c)
    c.repo.get_state_for_update.assert_not_called();c.repo.change_linked_user.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['object','inactive','body_id'])
async def test_self_requires_authenticated_platform_actor_not_body_identity(unlink_case,bad):
    c=unlink_case
    actor=SimpleNamespace(**vars(c.actor)) if bad=='object' else replace(c.actor,**({'status':'blocked'} if bad=='inactive' else {'user_id':'body-user'}))
    with pytest.raises(ApiIdentityAccessError):await unlink(c,actor=actor)
    c.repo.get_state_for_update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'client_id':'client'},{'tenant_id':'tenant'},{'expected_row_version':True},
    {'expected_row_version':0},{'trace_id':' '},{'trace_id':'t'*129}])
async def test_invalid_unlink_inputs_do_not_issue_sql_changes(unlink_case,changes):
    c=unlink_case
    with pytest.raises(ConstructionClientValidationError):await unlink(c,**changes)
    c.repo.get_state_for_update.assert_not_called();c.repo.change_linked_user.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['cas','mutation_receipt','audit_write','audit_receipt','post_audit_identity','cancelled'])
async def test_unlink_failures_and_cancellation_roll_back_and_never_commit(unlink_case,failure):
    c=unlink_case
    expected=ConstructionClientOperationError
    if failure=='cas':c.repo.change_linked_user.side_effect=None;c.repo.change_linked_user.return_value=None;expected=ConstructionRowVersionConflictError
    elif failure=='mutation_receipt':
        c.repo.change_linked_user.side_effect=None
        c.repo.change_linked_user.return_value=Mutation(replace(c.state,row_version=2),True)
    else:
        original=c.events.create_event.side_effect
        async def audit(**kw):
            row=await original(**kw)
            if failure=='audit_write':raise RuntimeError('private audit information')
            if failure=='audit_receipt':row.tenant_id=uuid4()
            elif failure=='post_audit_identity':c.identity.require_active_user.side_effect=ApiIdentityAccessError('inactive')
            elif failure=='cancelled':raise asyncio.CancelledError()
            return row
        c.events.create_event.side_effect=audit
        if failure=='post_audit_identity':expected=ApiIdentityAccessError
        elif failure=='cancelled':expected=asyncio.CancelledError
    with pytest.raises(expected) as caught:await unlink(c)
    assert 'private audit information' not in str(caught.value)
    c.session.rollback.assert_awaited_once();c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_editor_losing_rights_after_lock_cannot_change_or_noop_the_client(unlink_case):
    c=unlink_case
    c.access.has_active_platform_membership.side_effect=[True,False]
    with pytest.raises(ConstructionPermissionDeniedError):await edit(c)
    c.repo.change_linked_user.assert_not_called()


@pytest.mark.asyncio
async def test_missing_editor_client_and_invalid_store_receipt_fail_without_write(unlink_case):
    c=unlink_case
    c.state=None
    with pytest.raises(ConstructionClientNotFoundError):await edit(c)
    c.state=SimpleNamespace(client_id=c.client_id,tenant_id=c.tenant,linked_user_id=c.target,row_version=1)
    with pytest.raises(ConstructionClientOperationError):await edit(c)
    c.repo.change_linked_user.assert_not_called()


@pytest.mark.asyncio
async def test_version_exhaustion_and_missing_transaction_are_checked(unlink_case):
    c=unlink_case
    c.state=replace(c.state,row_version=MAX_CLIENT_ROW_VERSION)
    with pytest.raises(ConstructionClientValidationError):await unlink(c,expected_row_version=MAX_CLIENT_ROW_VERSION)
    c.session.in_transaction=lambda:False
    with pytest.raises(ConstructionClientOperationError):await unlink(c)
    c.repo.change_linked_user.assert_not_called()


@pytest.fixture
async def sql_unlink(pipeline):
    p,c=pipeline,pipeline.c
    await confirm_pipeline(p)
    c.client_id=c.client
    c.unlink=Service(session=c.session,repository=c.clients,access_repository=c.access,event_repository=c.events,
        identity_reader=c.identity,now_provider=lambda:c.now)
    c.session.commit.reset_mock();c.session.rollback.reset_mock()
    c.events.create_event.reset_mock();c.access.has_active_platform_membership.reset_mock();c.access.list_active_grants.reset_mock()
    c.sync.begin()
    return c


@pytest.mark.asyncio
async def test_real_self_unlink_changes_only_client_link_version_and_correlated_audit(sql_unlink):
    c=sql_unlink
    before_request=c.sync.execute(select(Request.__table__)).all()
    before_users=c.sync.execute(text('SELECT * FROM users ORDER BY id')).all()
    before_ledger=c.sync.execute(text('SELECT * FROM client_link_replay_ledger')).all()
    c.access.has_active_platform_membership.side_effect=AssertionError('No membership required for own unlink.')
    c.access.list_active_grants.side_effect=AssertionError('No grants required for own unlink.')
    result=await unlink(c,expected_row_version=2)
    assert result.row_version==3 and result.previous_linked_user_id==c.target
    assert c.sync.execute(select(Request.__table__)).all()==before_request
    assert c.sync.execute(text('SELECT * FROM users ORDER BY id')).all()==before_users
    assert c.sync.execute(text('SELECT * FROM client_link_replay_ledger')).all()==before_ledger
    c.session.commit.assert_not_called()
    c.sync.commit()
    with Session(c.engine) as reader:
        client=reader.get(ConstructionClient,c.client)
        assert client.linked_user_id is None and client.row_version==3
        assert reader.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==3


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['audit_after_real_insert','lost_identity','later_caller_work'])
async def test_real_unlink_and_audit_rollback_together(sql_unlink,failure):
    c=sql_unlink
    original=c.events.create_event.side_effect
    async def audit(**kw):
        row=await original(**kw)
        assert c.sync.execute(select(ConstructionClient.linked_user_id).where(ConstructionClient.id==c.client)).scalar_one() is None
        if failure=='audit_after_real_insert':raise RuntimeError('after actual unlink audit')
        if failure=='lost_identity':c.identity.require_active_user.side_effect=ApiIdentityAccessError('inactive')
        return row
    c.events.create_event.side_effect=audit
    if failure=='later_caller_work':
        await unlink(c,expected_row_version=2);c.sync.rollback()
    else:
        with pytest.raises((ConstructionClientOperationError,ApiIdentityAccessError)):await unlink(c,expected_row_version=2)
    client=c.sync.get(ConstructionClient,c.client)
    assert client.linked_user_id==c.target and client.row_version==2
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==2


@pytest.mark.asyncio
async def test_unlink_invalidates_an_older_pending_replacement_without_consuming_its_token(sql_unlink):
    c=sql_unlink
    token='ccl1_'+urlsafe_b64encode(b'z'*32).decode('ascii').rstrip('=')
    pending=await c.requests.create_request(**arguments(c,id=uuid4(),target_user_id=c.foreign_user,
        expected_client_row_version=2,expected_linked_user_id=c.target,token_hash=hash_client_link_confirmation_token(token),
        issued_at=NOW,expires_at=NOW+timedelta(minutes=3)))
    c.sync.commit();c.sync.begin()
    await unlink(c,expected_row_version=2)
    c.sync.commit();c.sync.begin()
    with pytest.raises(ConstructionRowVersionConflictError):
        await c.service.confirm_request(actor=ApiActorContext(c.foreign_user,None,None,(),'ru',None,'active'),
            tenant_id=c.tenant,client_id=c.client,request_id=pending.request_id,token=token,expected_request_row_version=1)
    request=c.sync.get(Request,pending.request_id)
    client=c.sync.get(ConstructionClient,c.client)
    assert request.consumed_at is None and request.row_version==1
    assert client.linked_user_id is None and client.row_version==3


def test_factory_keeps_platform_reads_separate_and_reuses_shared_construction_writes(unlink_case):
    c=unlink_case
    platform=SimpleNamespace(execute=AsyncMock(),no_autoflush=nullcontext())
    service=build_construction_client_unlink_service(session=c.session,platform_session=platform)
    assert service.repository.session is service.access.session is service.audit.event_repository.session is c.session
    assert service.identity.session is platform
    with pytest.raises(ValueError):build_construction_client_unlink_service(session=c.session,platform_session=c.session)
