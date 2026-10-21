import asyncio
from base64 import urlsafe_b64encode
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session

from database.models import ConstructionClient, ConstructionClientUserLinkRequest as Request
from database.repositories.construction_client_link_requests import ConstructionClientLinkRequestState as RequestState
from database.repositories.construction_client_user_links import (
    ConstructionClientUserLinkRepository, ConstructionClientUserLinkState as ClientState,
    ConstructionClientUserLinkMutation as Mutation, MAX_CLIENT_ROW_VERSION,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction_client_history_contract import construction_history_event_belongs_to_client
from services.construction_client_link_request_issuance import hash_client_link_confirmation_token
from services.construction_client_link_confirmation import (
    ConstructionClientLinkConfirmationService as Service, ConstructionClientLinkConfirmationDeniedError as Denied,
    PlatformClientLinkConfirmationIdentityReader, CONFIRM_EVENT,
    build_construction_client_link_confirmation_service,
)
from services.construction_clients import ConstructionClientOperationError, ConstructionClientValidationError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_client_link_request_repository import request_repo, arguments
from test_construction_1a_client_link_request_model import requests_db

NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
TOKEN='ccl1_'+urlsafe_b64encode(bytes(range(32))).decode('ascii').rstrip('=')


def setup(c,clients,requests):
    # No selected/home tenant, roles or Construction membership for recipient.
    c.actor=ApiActorContext(c.target,None,None,(),'ru',None,'active')
    c.grant=SimpleNamespace(role='OWNER',scope_type='tenant',scope_id=c.tenant,expires_at=None,status='active')
    c.access=SimpleNamespace(session=c.session,has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(side_effect=lambda **_:[c.grant]))
    c.identity=SimpleNamespace(session=object(),require_active_user=AsyncMock(side_effect=lambda **kw:kw['user_id']))
    c.audit_rows=[]
    async def audit(**kw):
        row=SimpleNamespace(id=uuid4(),**kw)
        c.audit_rows.append(row)
        return row
    c.events=SimpleNamespace(session=c.session,create_event=AsyncMock(side_effect=audit))
    c.clients,c.requests,c.now=clients,requests,NOW
    c.service=Service(session=c.session,request_repository=requests,client_repository=clients,
        access_repository=c.access,event_repository=c.events,identity_reader=c.identity,now_provider=lambda:c.now)
    return c


@pytest.fixture
def confirmation():
    c=SimpleNamespace(tenant=uuid4(),client=uuid4(),operator=uuid4(),target=uuid4(),previous=uuid4(),request_id=uuid4())
    c.session=SimpleNamespace(in_transaction=lambda:True,no_autoflush=nullcontext(),execute=AsyncMock(),
        rollback=AsyncMock(),commit=AsyncMock())
    c.base=ClientState(c.client,c.tenant,None,1)
    c.request=RequestState(c.request_id,c.tenant,c.client,c.operator,c.target,None,1,
        hash_client_link_confirmation_token(TOKEN),NOW-timedelta(seconds=20),NOW+timedelta(minutes=3),
        None,NOW-timedelta(seconds=20),1)
    clients=SimpleNamespace(session=c.session,get_state_for_update=AsyncMock(side_effect=lambda **_:c.base))
    async def change(**kw):
        changed=c.base.linked_user_id!=kw['linked_user_id']
        c.base=replace(c.base,linked_user_id=kw['linked_user_id'],row_version=c.base.row_version+int(changed))
        return Mutation(c.base,changed)
    clients.change_linked_user=AsyncMock(side_effect=change)
    async def get(**kw):
        if (kw['tenant_id'],kw['request_id'],kw['target_user_id'],kw['token_hash']) == (
                c.request.tenant_id,c.request.request_id,c.request.target_user_id,c.request.token_hash):return c.request
        return None
    async def consume(**kw):
        c.request=replace(kw['state'],consumed_at=kw['as_of'],updated_at=kw['as_of'],row_version=kw['state'].row_version+1)
        return c.request
    requests=SimpleNamespace(session=c.session,get_request_for_update=AsyncMock(side_effect=get),consume_request=AsyncMock(side_effect=consume))
    return setup(c,clients,requests)


async def confirm(c,**changes):
    kw=dict(actor=c.actor,tenant_id=c.tenant,client_id=c.client,request_id=c.request_id,
        token=TOKEN,expected_request_row_version=1,trace_id='operator-confirm-test')
    kw.update(changes)
    return await c.service.confirm_request(**kw)


@pytest.mark.asyncio
async def test_authenticated_target_without_membership_links_with_correlated_audit_and_no_commit(confirmation):
    c=confirmation
    result=await confirm(c)
    assert result.client_changed and result.linked_user_id==c.target
    assert result.client_row_version==2 and result.request_row_version==2 and result.consumed_at==NOW
    assert result.request_audit_id==c.audit_rows[0].id and result.client_audit_id==c.audit_rows[1].id
    assert c.request.consumed_at==NOW and c.base.linked_user_id==c.target
    assert c.clients.change_linked_user.await_args.kwargs==dict(tenant_id=c.tenant,client_id=c.client,
        expected_row_version=1,expected_linked_user_id=None,linked_user_id=c.target,as_of=NOW)
    assert len(c.audit_rows)==2
    request_event,client_event=c.audit_rows
    assert request_event.event_type==CONFIRM_EVENT and request_event.entity_type=='construction_client_user_link_request'
    assert client_event.event_type=='construction_client_updated' and client_event.entity_id==c.client
    assert all(e.user_id==c.target and e.tenant_id==c.tenant for e in c.audit_rows)
    assert all(e.payload['initiated_by_user_id']==str(c.operator) for e in c.audit_rows)
    assert all(e.payload['related_client_id']==str(c.client) for e in c.audit_rows)
    assert all(e.payload['operation']=='update' for e in c.audit_rows)
    assert construction_history_event_belongs_to_client(client_event,tenant_id=c.tenant,client_id=c.client)
    assert TOKEN not in repr(result) and c.request.token_hash not in repr(result)
    for event in c.audit_rows:
        payload=json.dumps(event.payload)
        assert TOKEN not in payload and c.request.token_hash not in payload
    assert all(a.kwargs['user_id']==c.operator and a.kwargs['tenant_id']==c.tenant
        for a in c.access.has_active_platform_membership.await_args_list)
    assert all(a.kwargs['user_id']==c.operator for a in c.access.list_active_grants.await_args_list)
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_replacement_uses_saved_old_identity_and_retains_audited_history(confirmation):
    c=confirmation
    c.base=replace(c.base,linked_user_id=c.previous,row_version=5)
    c.request=replace(c.request,expected_linked_user_id=c.previous,expected_client_row_version=5)
    result=await confirm(c)
    assert result.client_row_version==6 and c.base.linked_user_id==c.target
    assert c.clients.change_linked_user.await_args.kwargs['expected_linked_user_id']==c.previous
    assert c.audit_rows[1].payload['changes'][0]=={'field':'linked_user_id','old_value':str(c.previous),'new_value':str(c.target)}
    assert c.audit_rows[1].payload['identity_link_action']=='operator_replacement'


@pytest.mark.asyncio
async def test_matching_link_consumes_request_once_without_client_version_or_client_update_audit(confirmation):
    c=confirmation
    c.base=replace(c.base,linked_user_id=c.target,row_version=8)
    c.request=replace(c.request,expected_linked_user_id=c.target,expected_client_row_version=8)
    result=await confirm(c)
    assert not result.client_changed and result.client_row_version==8 and result.client_audit_id is None
    assert result.request_row_version==2 and len(c.audit_rows)==1
    assert c.audit_rows[0].payload['client_changed'] is False
    with pytest.raises(Denied):await confirm(c)
    assert c.clients.change_linked_user.await_count==1 and len(c.audit_rows)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['object','blocked','no_user'])
async def test_target_requires_authenticated_platform_actor_before_resource_lookup(confirmation,bad):
    c=confirmation
    actor=SimpleNamespace(**vars(c.actor)) if bad=='object' else replace(c.actor,**({'status':'blocked'} if bad=='blocked' else {'user_id':'body-user'}))
    with pytest.raises(ApiIdentityAccessError):await confirm(c,actor=actor)
    c.clients.get_state_for_update.assert_not_called()
    c.requests.get_request_for_update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['other_target','wrong_token','unknown_request','different_tenant','different_client','malformed_token'])
async def test_wrong_recipient_secret_or_route_cannot_consume_or_mutate(confirmation,bad):
    c=confirmation
    kw={}
    if bad=='other_target':kw['actor']=replace(c.actor,user_id=c.previous)
    elif bad=='wrong_token':kw['token']='ccl1_'+urlsafe_b64encode(b'x'*32).decode('ascii').rstrip('=')
    elif bad=='unknown_request':kw['request_id']=uuid4()
    elif bad=='different_tenant':kw['tenant_id']=uuid4();c.clients.get_state_for_update.side_effect=None;c.clients.get_state_for_update.return_value=None
    elif bad=='different_client':kw['client_id']=uuid4();c.clients.get_state_for_update.side_effect=None;c.clients.get_state_for_update.return_value=None
    else:kw['token']='not-a-confirmation-token'
    with pytest.raises(Denied):await confirm(c,**kw)
    c.requests.consume_request.assert_not_called()
    c.clients.change_linked_user.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('state',['expired','expiry_boundary','not_yet_issued','used'])
async def test_lifetime_boundaries_and_consumed_token_are_denied(confirmation,state):
    c=confirmation
    if state=='expired':c.now=c.request.expires_at+timedelta(microseconds=1)
    elif state=='expiry_boundary':c.now=c.request.expires_at
    elif state=='not_yet_issued':c.now=c.request.issued_at-timedelta(microseconds=1)
    else:c.request=replace(c.request,consumed_at=NOW,updated_at=NOW,row_version=2)
    with pytest.raises(Denied):await confirm(c)
    c.requests.consume_request.assert_not_called()
    c.clients.change_linked_user.assert_not_called()


@pytest.mark.asyncio
async def test_issued_at_boundary_is_valid(confirmation):
    c=confirmation
    c.now=c.request.issued_at
    assert (await confirm(c)).consumed_at==c.request.issued_at


@pytest.mark.asyncio
@pytest.mark.parametrize('conflict',['request_version','client_version','client_identity'])
async def test_changed_base_is_conflict_without_consuming_the_request(confirmation,conflict):
    c=confirmation
    kw={}
    if conflict=='request_version':kw['expected_request_row_version']=2
    elif conflict=='client_version':c.base=replace(c.base,row_version=2)
    else:c.base=replace(c.base,linked_user_id=c.previous)
    with pytest.raises(ConstructionRowVersionConflictError):await confirm(c,**kw)
    c.requests.consume_request.assert_not_called()
    c.clients.change_linked_user.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['membership','revoked','expired_grant','scope','operator_inactive','target_inactive'])
async def test_fresh_initiator_edit_authority_and_active_target_are_required(confirmation,denial):
    c=confirmation
    if denial=='membership':c.access.has_active_platform_membership.return_value=False
    elif denial=='revoked':c.grant.status='revoked'
    elif denial=='expired_grant':c.grant.expires_at=NOW
    elif denial=='scope':c.grant.scope_type,c.grant.scope_id='project',uuid4()
    else:
        async def active(**kw):
            if kw['user_id']==(c.operator if denial=='operator_inactive' else c.target):raise ApiIdentityAccessError('inactive')
            return kw['user_id']
        c.identity.require_active_user.side_effect=active
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError,ApiIdentityAccessError)):await confirm(c)
    c.requests.consume_request.assert_not_called()
    c.clients.change_linked_user.assert_not_called()


@pytest.mark.asyncio
async def test_blocking_authorization_can_expire_token_before_consume(confirmation):
    c=confirmation
    async def roles(**_):c.now=c.request.expires_at;return [c.grant]
    c.access.list_active_grants.side_effect=roles
    with pytest.raises(Denied):await confirm(c)
    c.requests.consume_request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['consume_none','consume_receipt','client_none','client_receipt','first_audit','second_audit',
    'wrong_audit_receipt','lost_authority','lost_target','cancelled'])
async def test_every_post_consumption_failure_rolls_back_without_commit(confirmation,failure):
    c=confirmation
    if failure=='consume_none':c.requests.consume_request.side_effect=None;c.requests.consume_request.return_value=None
    elif failure=='consume_receipt':
        original=c.requests.consume_request.side_effect
        async def consume(**kw):return replace(await original(**kw),target_user_id=c.operator)
        c.requests.consume_request.side_effect=consume
    elif failure=='client_none':c.clients.change_linked_user.side_effect=None;c.clients.change_linked_user.return_value=None
    elif failure=='client_receipt':
        original=c.clients.change_linked_user.side_effect
        async def change(**kw):return replace(await original(**kw),changed=False)
        c.clients.change_linked_user.side_effect=change
    else:
        original=c.events.create_event.side_effect
        async def audit(**kw):
            row=await original(**kw)
            if (failure=='first_audit' and len(c.audit_rows)==1) or (failure=='second_audit' and len(c.audit_rows)==2):
                raise RuntimeError('private binding secret')
            if failure=='wrong_audit_receipt':row.user_id=c.operator
            elif failure=='lost_authority':c.access.has_active_platform_membership.return_value=False
            elif failure=='lost_target':c.identity.require_active_user.side_effect=ApiIdentityAccessError('inactive')
            elif failure=='cancelled':raise asyncio.CancelledError()
            return row
        c.events.create_event.side_effect=audit
    expected=asyncio.CancelledError if failure=='cancelled' else (
        ConstructionRowVersionConflictError if failure in ('consume_none','client_none') else
        ConstructionPermissionDeniedError if failure=='lost_authority' else
        ApiIdentityAccessError if failure=='lost_target' else ConstructionClientOperationError)
    with pytest.raises(expected) as caught:await confirm(c)
    assert 'private binding secret' not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'tenant_id':'body-tenant'},{'client_id':'client'},{'request_id':'request'},
    {'expected_request_row_version':True},{'expected_request_row_version':0},{'trace_id':' '}])
async def test_invalid_inputs_are_rejected_before_any_write(confirmation,changes):
    c=confirmation
    with pytest.raises(ConstructionClientValidationError):await confirm(c,**changes)
    c.requests.consume_request.assert_not_called()
    c.clients.change_linked_user.assert_not_called()


@pytest.mark.asyncio
async def test_untrusted_confirmation_boolean_or_target_user_id_is_not_an_input(confirmation):
    c=confirmation
    for kw in ({'confirmed':True},{'target_user_id':c.target}):
        with pytest.raises(TypeError):await confirm(c,**kw)
    c.requests.consume_request.assert_not_called()


@pytest.fixture
def sql_confirmation(request_repo):
    c=request_repo
    c.request_id=uuid4()
    seed=arguments(c,id=c.request_id,issued_at=NOW-timedelta(seconds=20),expires_at=NOW+timedelta(minutes=3),
        token_hash=hash_client_link_confirmation_token(TOKEN))
    c.seed=seed
    setup(c,ConstructionClientUserLinkRepository(c.session),c.repo)
    async def rollback():c.sync.rollback()
    c.session.rollback=AsyncMock(side_effect=rollback)
    original=c.events.create_event.side_effect
    async def audit(**kw):
        row=await original(**kw)
        c.sync.execute(text('INSERT INTO request_repo_test_audit VALUES(:id)'),{'id':str(row.id)})
        return row
    c.events.create_event.side_effect=audit
    return c


async def seed_request(c):
    c.request=await c.requests.create_request(**c.seed)
    c.sync.commit()
    c.sync.begin()


@pytest.mark.asyncio
async def test_real_confirm_consumes_links_and_audits_in_one_caller_transaction(sql_confirmation):
    c=sql_confirmation
    await seed_request(c)
    result=await confirm(c)
    request=c.sync.get(Request,c.request_id)
    client=c.sync.get(ConstructionClient,c.client)
    assert request.consumed_at is not None and request.row_version==2
    assert client.linked_user_id==c.target and client.row_version==2
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==2
    assert c.sync.execute(text('SELECT count(*) FROM users')).scalar_one()==3
    assert c.sync.execute(text('SELECT count(*) FROM users WHERE id=:id AND tenant_id IS NULL'),{'id':c.target.hex}).scalar_one()==1
    c.session.commit.assert_not_called()
    c.sync.commit()
    with Session(c.engine) as reader:
        assert reader.get(Request,c.request_id).row_version==2
        assert reader.get(ConstructionClient,c.client).linked_user_id==c.target
        assert reader.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==2
    assert result.client_changed


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['first_audit','second_audit','lost_authority','later_caller_work','client_cas'])
async def test_real_consumption_link_and_all_audits_roll_back_together(sql_confirmation,failure):
    c=sql_confirmation
    await seed_request(c)
    original=c.events.create_event.side_effect
    async def audit(**kw):
        row=await original(**kw)
        assert c.sync.execute(select(Request.consumed_at)).scalar_one() is not None
        assert c.sync.execute(select(ConstructionClient.linked_user_id).where(ConstructionClient.id==c.client)).scalar_one()==c.target
        if failure=='first_audit' or (failure=='second_audit' and len(c.audit_rows)==2):raise RuntimeError('after real insert')
        if failure=='lost_authority':c.access.has_active_platform_membership.return_value=False
        return row
    c.events.create_event.side_effect=audit
    if failure=='client_cas':
        c.clients.change_linked_user=AsyncMock(return_value=None)
    if failure=='later_caller_work':
        await confirm(c)
        c.sync.rollback()
    else:
        with pytest.raises((ConstructionClientOperationError,ConstructionPermissionDeniedError,ConstructionRowVersionConflictError)):
            await confirm(c)
    request=c.sync.get(Request,c.request_id)
    client=c.sync.get(ConstructionClient,c.client)
    assert request.consumed_at is None and request.row_version==1
    assert client.linked_user_id is None and client.row_version==1
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==0


@pytest.mark.asyncio
async def test_active_identity_reader_needs_no_membership_and_refreshes_canonical_status():
    engine=create_engine('sqlite://')
    user_id=uuid4()
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE users(id CHAR(32) PRIMARY KEY,status TEXT)')
        conn.exec_driver_sql('INSERT INTO users VALUES(?,?)',(user_id.hex,'active'))
    with Session(engine) as sync:
        statements=[]
        class Platform:
            @property
            def no_autoflush(self):return sync.no_autoflush
            async def execute(self,statement):statements.append(statement);return sync.execute(statement)
        reader=PlatformClientLinkConfirmationIdentityReader(Platform())
        assert await reader.require_active_user(user_id=user_id)==user_id
        sql=str(statements[0]).lower()
        assert 'tenant_id' not in sql and 'user_accounts' not in sql and 'user_roles' not in sql
        sync.execute(text("UPDATE users SET status='blocked'"))
        with pytest.raises(ApiIdentityAccessError):await reader.require_active_user(user_id=user_id)
    engine.dispose()


def test_factory_separates_platform_reads_and_construction_transaction(confirmation):
    c=confirmation
    platform=SimpleNamespace(execute=AsyncMock(),no_autoflush=nullcontext())
    service=build_construction_client_link_confirmation_service(session=c.session,platform_session=platform)
    assert service.clients.session is service.requests.session is service.access.session is c.session
    assert service.audit.event_repository.session is c.session and service.identity.session is platform
    with pytest.raises(ValueError):build_construction_client_link_confirmation_service(session=c.session,platform_session=c.session)
