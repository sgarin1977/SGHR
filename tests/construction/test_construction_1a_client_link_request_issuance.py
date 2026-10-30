import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import ConstructionClient, ConstructionClientUserLinkRequest as Request
from database.repositories.construction.construction_client_link_requests import ConstructionClientLinkRequestState as RequestState
from database.repositories.construction.construction_client_user_links import (
    ConstructionClientUserLinkRepository, ConstructionClientUserLinkState as ClientState, MAX_CLIENT_ROW_VERSION,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_client_link_request_issuance import (
    ConstructionClientLinkRequestIssuanceService as Service, ConstructionClientLinkRequestValidationError as Invalid,
    PlatformClientLinkIdentityReader, REQUEST_EVENT, REQUEST_ENTITY,
    build_construction_client_link_request_issuance_service, hash_client_link_confirmation_token,
)
from services.construction.construction_clients import ConstructionClientNotFoundError, ConstructionClientOperationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
LIFETIME = timedelta(hours=24)  # Approved operator-link confirmation contract.


def configure(c, *, clients, requests):
    c.actor = ApiActorContext(c.operator, c.tenant, None, (), 'ru', None, 'active')
    c.context = ConstructionAccessContext(c.operator, c.tenant, ('OWNER',), ('construction.projects.edit',),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    c.grant = SimpleNamespace(role='OWNER', scope_type='tenant', scope_id=c.tenant, expires_at=None, status='active')
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(side_effect=lambda **_: [c.grant]))
    c.identity = SimpleNamespace(session=object(), require_active_actor=AsyncMock(side_effect=lambda **kw: kw['actor']),
        require_existing_user=AsyncMock(side_effect=lambda **kw:kw['user_id']))
    c.audit_rows = []
    async def audit(**kwargs):
        row = SimpleNamespace(id=uuid4(), **kwargs)
        c.audit_rows.append(row)
        return row
    c.events = SimpleNamespace(session=c.session, create_event=AsyncMock(side_effect=audit))
    c.clients, c.requests, c.now = clients, requests, NOW
    c.service = Service(session=c.session, request_repository=requests, client_repository=clients,
        access_repository=c.access, event_repository=c.events, identity_reader=c.identity,
        confirmation_lifetime=LIFETIME, now_provider=lambda:c.now)
    return c


@pytest.fixture
def issuance():
    c = SimpleNamespace(tenant=uuid4(), client=uuid4(), operator=uuid4(), target=uuid4(), previous=uuid4())
    c.session = SimpleNamespace(in_transaction=lambda:True, no_autoflush=nullcontext(),
        execute=AsyncMock(), rollback=AsyncMock(), commit=AsyncMock())
    c.base = ClientState(c.client, c.tenant, None, 1)
    clients = SimpleNamespace(session=c.session, get_state_for_update=AsyncMock(side_effect=lambda **_:c.base),
        change_linked_user=AsyncMock())
    async def create(**kw):
        return RequestState(**kw, consumed_at=None, updated_at=kw['issued_at'], row_version=1)
    requests = SimpleNamespace(session=c.session, create_request=AsyncMock(side_effect=create),
        list_active_requests_for_update=AsyncMock(return_value=()), invalidate_request=AsyncMock())
    return configure(c,clients=clients,requests=requests)


async def issue(c, **changes):
    kw = dict(actor=c.actor, access_context=c.context, client_id=c.client, target_user_id=c.target,
        expected_row_version=1, trace_id='operator-issuance-test')
    kw.update(changes)
    return await c.service.request_link(**kw)


@pytest.mark.asyncio
async def test_pending_request_keeps_client_unmodified_and_records_only_request_creation(issuance):
    c = issuance
    result = await issue(c)
    state = result.request
    assert state.tenant_id == c.tenant and state.client_id == c.client
    assert state.initiated_by_user_id == c.operator and state.target_user_id == c.target
    assert state.expected_client_row_version == 1 and state.expected_linked_user_id is None
    assert state.expires_at == NOW+LIFETIME and state.issued_at == NOW
    assert state.row_version == 1 and state.consumed_at is None
    assert state.token_hash == hash_client_link_confirmation_token(result.token)
    assert result.token not in repr(result) and state.token_hash not in repr(result)
    assert c.base == ClientState(c.client,c.tenant,None,1)
    c.clients.change_linked_user.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
    event = c.audit_rows[0]
    assert event.id == result.audit_id and len(c.audit_rows) == 1
    assert (event.event_type,event.entity_type,event.entity_id) == (REQUEST_EVENT,REQUEST_ENTITY,state.request_id)
    assert event.user_id == c.operator and event.tenant_id == c.tenant and event.platform == 'api'
    assert event.payload['related_client_id'] == str(c.client)
    assert event.payload['operation'] == 'create' and event.payload['changes'] == []
    assert event.payload['expected_client_row_version'] == 1
    assert event.payload['identity_link_action'] == 'request_link'
    assert result.token not in json.dumps(event.payload) and state.token_hash not in json.dumps(event.payload)
    assert 'token' not in c.requests.create_request.await_args.kwargs
    assert all(k['user_id'] == c.operator for k in (a.kwargs for a in c.access.has_active_platform_membership.await_args_list))
    assert all(a.kwargs == {'user_id':c.target} for a in c.identity.require_existing_user.await_args_list)


@pytest.mark.asyncio
async def test_real_random_tokens_are_distinct_and_never_reused_for_another_request(issuance):
    c = issuance
    first,second = await issue(c),await issue(c)
    assert first.token != second.token and first.request.request_id != second.request.request_id
    assert first.request.token_hash != second.request.token_hash


@pytest.mark.asyncio
async def test_replacement_is_explicit_and_keeps_old_link_until_target_confirms(issuance):
    c = issuance
    c.base = replace(c.base,linked_user_id=c.previous,row_version=9)
    with pytest.raises(Invalid):await issue(c,expected_row_version=9)
    c.requests.create_request.assert_not_called()
    result = await c.service.request_replacement(actor=c.actor,access_context=c.context,client_id=c.client,
        target_user_id=c.target,previous_user_id=c.previous,expected_row_version=9)
    assert result.request.expected_linked_user_id == c.previous
    assert result.request.expected_client_row_version == 9
    assert c.base.linked_user_id == c.previous and c.base.row_version == 9
    assert c.audit_rows[0].payload['identity_link_action'] == 'request_replacement'
    c.clients.change_linked_user.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_previous_identity_and_stale_version_do_not_issue_requests(issuance):
    c = issuance
    c.base = replace(c.base,linked_user_id=c.previous,row_version=4)
    with pytest.raises(ConstructionRowVersionConflictError):await issue(c)
    with pytest.raises(ConstructionRowVersionConflictError):
        await c.service.request_replacement(actor=c.actor,access_context=c.context,client_id=c.client,
            target_user_id=c.target,previous_user_id=uuid4(),expected_row_version=4)
    c.requests.create_request.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['no_permission','project_only','wrong_tenant','wrong_user','inactive','plain_actor'])
async def test_initial_access_denial_precedes_client_or_target_lookup(issuance,denial):
    c = issuance
    actor,ctx = c.actor,c.context
    if denial == 'no_permission':ctx=replace(ctx,permissions=())
    elif denial == 'project_only':ctx=replace(ctx,grants=(ConstructionGrantContext('OWNER','project',uuid4()),))
    elif denial == 'wrong_tenant':ctx=replace(ctx,tenant_id=uuid4())
    elif denial == 'wrong_user':ctx=replace(ctx,user_id=c.target)
    elif denial == 'inactive':actor=replace(actor,status='blocked')
    else:actor=SimpleNamespace(**vars(actor))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):
        await issue(c,actor=actor,access_context=ctx)
    c.clients.get_state_for_update.assert_not_called()
    c.identity.require_existing_user.assert_not_called()
    c.requests.create_request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['inactive_identity','membership','revoked','expired','scope'])
async def test_stale_caller_context_cannot_bypass_fresh_authorization(issuance,denial):
    c = issuance
    if denial == 'inactive_identity':c.identity.require_active_actor.side_effect=ApiIdentityAccessError('not available')
    elif denial == 'membership':c.access.has_active_platform_membership.return_value=False
    elif denial == 'revoked':c.grant.status='revoked'
    elif denial == 'expired':c.grant.expires_at=NOW
    else:c.grant.scope_type,c.grant.scope_id='project',uuid4()
    with pytest.raises((ApiIdentityAccessError,ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await issue(c)
    c.clients.get_state_for_update.assert_not_called()
    c.requests.create_request.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['missing','foreign','untyped','version_bool','identity'])
async def test_bad_client_projection_never_becomes_confirmation_context(issuance,bad):
    c = issuance
    c.base = {'missing':None,'foreign':replace(c.base,tenant_id=uuid4()),'untyped':SimpleNamespace(**vars(c.base)),
        'version_bool':replace(c.base,row_version=True),'identity':replace(c.base,linked_user_id='bad')}[bad]
    with pytest.raises((ConstructionClientNotFoundError,ConstructionClientOperationError)):await issue(c)
    c.requests.create_request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'target_user_id':'user'}, {'client_id':'client'}, {'expected_row_version':True},
    {'expected_row_version':0}, {'trace_id':' '}, {'trace_id':'x'*129}])
async def test_invalid_inputs_are_rejected_before_storage(issuance,changes):
    c = issuance
    with pytest.raises(Invalid):await issue(c,**changes)
    c.clients.get_state_for_update.assert_not_called()
    c.requests.create_request.assert_not_called()


@pytest.mark.asyncio
async def test_self_target_uses_separate_existing_self_confirmation_flow(issuance):
    c = issuance
    with pytest.raises(Invalid):await issue(c,target_user_id=c.operator)
    c.requests.create_request.assert_not_called()


@pytest.mark.asyncio
async def test_missing_target_and_malformed_identity_receipt_prevent_issuance(issuance):
    c = issuance
    c.identity.require_existing_user.side_effect=Invalid('not available')
    with pytest.raises(Invalid):await issue(c)
    c.identity.require_existing_user.side_effect=None
    c.identity.require_existing_user.return_value=c.operator
    with pytest.raises(ConstructionClientOperationError):await issue(c)
    c.requests.create_request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['conditional_miss','context_receipt','audit_none','audit_wrong_entity',
    'audit_exception','access_after_lock','access_after_insert','access_after_audit','target_after_audit','expired_after_audit','cancelled'])
async def test_failed_issuance_rolls_back_and_never_returns_a_secret_or_commit(issuance,failure):
    c = issuance
    if failure == 'conditional_miss':c.requests.create_request.side_effect=None;c.requests.create_request.return_value=None
    elif failure == 'context_receipt':
        original=c.requests.create_request.side_effect
        async def changed(**kw):return replace(await original(**kw),target_user_id=c.operator)
        c.requests.create_request.side_effect=changed
    elif failure.startswith('access_'):
        number={'access_after_lock':2,'access_after_insert':3,'access_after_audit':4}[failure]
        c.access.has_active_platform_membership.side_effect=[True]*(number-1)+[False]
    elif failure == 'target_after_audit':c.identity.require_existing_user.side_effect=[c.target,c.target,Invalid('no target')]
    elif failure == 'audit_none':c.events.create_event.side_effect=None;c.events.create_event.return_value=None
    else:
        original=c.events.create_event.side_effect
        async def event(**kw):
            row=await original(**kw)
            if failure == 'audit_wrong_entity':row.entity_id=c.client
            elif failure == 'audit_exception':raise RuntimeError('private secret from downstream')
            elif failure == 'expired_after_audit':c.now=NOW+LIFETIME
            elif failure == 'cancelled':raise asyncio.CancelledError()
            return row
        c.events.create_event.side_effect=event
    expected=asyncio.CancelledError if failure=='cancelled' else (
        ConstructionRowVersionConflictError if failure=='conditional_miss' else
        ConstructionPermissionDeniedError if failure.startswith('access_') else
        Invalid if failure=='target_after_audit' else ConstructionClientOperationError)
    with pytest.raises(expected) as caught:await issue(c)
    assert 'private secret' not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    c.session.commit.assert_not_called()
    c.clients.change_linked_user.assert_not_called()


@pytest.fixture
def sql_issuance(request_repo):
    c=request_repo
    configure(c,clients=ConstructionClientUserLinkRepository(c.session),requests=c.repo)
    async def rollback():c.sync.rollback()
    c.session.rollback=AsyncMock(side_effect=rollback)
    original=c.events.create_event.side_effect
    async def audit(**kwargs):
        row=await original(**kwargs)
        c.sync.execute(text('INSERT INTO request_repo_test_audit VALUES(:id)'),{'id':str(row.id)})
        return row
    c.events.create_event.side_effect=audit
    return c


@pytest.mark.asyncio
async def test_real_request_and_audit_wait_for_caller_commit_without_changing_client_or_users(sql_issuance):
    c=sql_issuance
    result=await issue(c)
    assert c.sync.execute(select(Request.token_hash)).scalar_one()==hash_client_link_confirmation_token(result.token)
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==1
    c.session.commit.assert_not_called()
    c.sync.commit()
    with Session(c.engine) as reader:
        saved=reader.get(Request,result.request.request_id)
        assert saved.target_user_id==c.target and saved.consumed_at is None
        client=reader.get(ConstructionClient,c.client)
        assert client.linked_user_id is None and client.row_version==1
        assert reader.execute(text('SELECT count(*) FROM users')).scalar_one()==3
        assert reader.execute(text('SELECT count(*) FROM users WHERE id=:id AND tenant_id IS NULL'),{'id':c.target.hex}).scalar_one()==1
        assert reader.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['after_audit','lost_authority','later_caller_work'])
async def test_real_request_and_audit_are_both_rolled_back_on_mandatory_failure(sql_issuance,failure):
    c=sql_issuance
    original=c.events.create_event.side_effect
    async def audit(**kwargs):
        row=await original(**kwargs)
        assert c.sync.execute(select(Request.id)).scalar_one() is not None
        assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==1
        if failure=='after_audit':raise RuntimeError('failed after actual insert')
        if failure=='lost_authority':c.access.has_active_platform_membership.return_value=False
        return row
    c.events.create_event.side_effect=audit
    if failure=='later_caller_work':
        await issue(c)
        c.sync.rollback()
    else:
        with pytest.raises((ConstructionClientOperationError,ConstructionPermissionDeniedError)):await issue(c)
    assert c.sync.execute(select(Request.id)).all()==[]
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==0
    client=c.sync.get(ConstructionClient,c.client)
    assert client.linked_user_id is None and client.row_version==1


@pytest.mark.asyncio
async def test_real_replacement_request_keeps_existing_identity_and_version(sql_issuance):
    c=sql_issuance
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(linked_user_id=c.foreign_user))
    result=await c.service.request_replacement(actor=c.actor,access_context=c.context,client_id=c.client,
        target_user_id=c.target,previous_user_id=c.foreign_user,expected_row_version=1)
    assert result.request.expected_linked_user_id==c.foreign_user
    client=c.sync.get(ConstructionClient,c.client)
    assert client.linked_user_id==c.foreign_user and client.row_version==1


@pytest.mark.asyncio
async def test_platform_target_read_needs_only_existing_user_without_membership_account_or_status_filter():
    engine=create_engine('sqlite://')
    user_id=uuid4()
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE users(id CHAR(32) PRIMARY KEY)')
        conn.exec_driver_sql('INSERT INTO users VALUES(?)',(user_id.hex,))
    with Session(engine) as sync:
        statements=[]
        async def execute(statement):statements.append(statement);return sync.execute(statement)
        class Platform:
            @property
            def no_autoflush(self):return sync.no_autoflush
            async def execute(self,statement):return await execute(statement)
        platform=Platform()
        reader=PlatformClientLinkIdentityReader(platform)
        assert await reader.require_existing_user(user_id=user_id)==user_id
        sql=str(statements[0].compile(dialect=postgresql.dialect())).lower()
        assert 'tenant_id' not in sql and 'status' not in sql and 'user_accounts' not in sql and 'user_roles' not in sql
        assert statements[0].get_execution_options()['autoflush'] is False
        with pytest.raises(Invalid):await reader.require_existing_user(user_id=uuid4())
    engine.dispose()


@pytest.mark.parametrize('lifetime',[None,0,True,timedelta(0),timedelta(seconds=-1)])
def test_server_lifetime_must_match_the_approved_24_hours(issuance,lifetime):
    c=issuance
    with pytest.raises(ValueError):
        Service(session=c.session,request_repository=c.requests,client_repository=c.clients,
            access_repository=c.access,event_repository=c.events,identity_reader=c.identity,confirmation_lifetime=lifetime)


@pytest.mark.asyncio
async def test_missing_transaction_invalid_clock_and_invalid_token_never_create_request(issuance):
    c=issuance
    c.session.in_transaction=lambda:False
    with pytest.raises(ConstructionClientOperationError):await issue(c)
    c.session.in_transaction=lambda:True
    c.now=NOW.replace(tzinfo=None)
    with pytest.raises(ConstructionClientOperationError):await issue(c)
    c.now=NOW
    c.service.token_factory=lambda:'invalid raw secret'
    with pytest.raises(Invalid):await issue(c)
    c.requests.create_request.assert_not_called()


@pytest.mark.parametrize('token',[None,True,'raw','ccl1_'+'a'*42,'other_'+'A'*43,'ccl1_'+'!'*43,'ccl1_'+'B'*43])
def test_confirmation_hash_rejects_other_purposes_or_noncanonical_secret(token):
    with pytest.raises(Invalid):hash_client_link_confirmation_token(token)


def test_factory_shares_only_construction_transaction_and_uses_approved_lifetime(issuance):
    c=issuance
    platform=SimpleNamespace(execute=AsyncMock(),no_autoflush=nullcontext())
    default_service=build_construction_client_link_request_issuance_service(session=c.session,platform_session=platform)
    assert default_service.confirmation_lifetime==timedelta(hours=24)
    service=build_construction_client_link_request_issuance_service(session=c.session,platform_session=platform,confirmation_lifetime=LIFETIME)
    assert service.requests.session is service.clients.session is service.access.session is c.session
    assert service.audit.event_repository.session is c.session and service.identity.session is platform
    with pytest.raises(ValueError):
        build_construction_client_link_request_issuance_service(session=c.session,platform_session=c.session,confirmation_lifetime=LIFETIME)
