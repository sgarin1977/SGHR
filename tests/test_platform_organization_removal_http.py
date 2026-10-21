"""ASGI plus real local membership/grant/audit/receipt rollback; no native acceptance."""
import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime,timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID,uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

import api.routes.platform_organization_membership as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service
from database.repositories.platform_membership_serialization import PlatformMembershipSerializationReceipt as Receipt
from services.api_idempotency import ApiIdempotencyService,ApiIdempotencyResponseCodec
from services.platform_membership import PlatformMembershipRemovalDeniedError
from services.platform_organization_removal_command import PlatformOrganizationRemovalCommand,OPERATION
from test_construction_1a_organization_removal_workflow import removal_workflow
from test_construction_1a_organization_removal_service import organization_removal,snapshot,events
from test_construction_1a_platform_membership_service import removal
from test_construction_1a_platform_membership_repository import membership_store


class LocalReceipts:
    """Test-only live-window SQLite adapter for the real shared encrypted codec/service."""
    def __init__(self,c): self.c,self.session,self.fail=c,c.bridge,False
    def load(self,row):
        return NS(**{**dict(row),'id':UUID(row['id']),
            'expires_at':datetime.fromisoformat(row['expires_at'])})
    async def reserve_record(self,**v):
        assert v['principal_type']=='user'
        c=self.c
        scope=dict(tenant=str(v['tenant_id']),actor=str(v['principal_id']),operation=v['operation'],key=v['key_hash'])
        q='tenant=:tenant AND actor=:actor AND operation=:operation AND key_hash=:key'
        row=c.session.execute(text('SELECT * FROM removal_receipts WHERE '+q),scope).mappings().one_or_none()
        if row is not None:
            assert datetime.fromisoformat(row['expires_at'])>v['now']
            return self.load(row),False
        values=dict(scope,id=str(uuid4()),request=v['request_hash'],expires=v['expires_at'].isoformat())
        c.session.execute(text('INSERT INTO removal_receipts(id,tenant,actor,operation,key_hash,request_hash,status,expires_at) '
            "VALUES(:id,:tenant,:actor,:operation,:key,:request,'processing',:expires)"),values)
        row=c.session.execute(text('SELECT * FROM removal_receipts WHERE '+q),scope).mappings().one()
        return self.load(row),True
    async def complete_record(self,**v):
        self.c.session.execute(text("UPDATE removal_receipts SET status='completed',response_status=:status,"
            'response_ciphertext=:cipher WHERE id=:id'),dict(status=v['response_status'],cipher=v['response_ciphertext'],id=str(v['record'].id)))
        if self.fail: raise RuntimeError('private completion failure after real SQL update')
        return NS(status='completed',response_status=v['response_status'])


@pytest.fixture
def removal_http(removal_workflow):
    c=removal_workflow
    c.session.execute(text('CREATE TABLE removal_receipts(id TEXT PRIMARY KEY,tenant TEXT,actor TEXT,operation TEXT,'
        'key_hash TEXT,request_hash TEXT,status TEXT,expires_at TEXT,response_status INTEGER,response_ciphertext BLOB,'
        'UNIQUE(tenant,actor,operation,key_hash))'))
    c.session.commit(); c.session.begin()
    c.authorization.prepare_removal=AsyncMock()
    # SQLite cannot acquire PostgreSQL advisory locks. The actual production
    # repository/authorizer and writer protocol have separate SQL/guard tests.
    async def lock(**v): return Receipt(v['tenant_id'],v['user_id'],c.session.get_transaction())
    c.serialization.lock_member=AsyncMock(side_effect=lock)
    c.receipts=LocalReceipts(c)
    c.codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode())
    c.idempotency=ApiIdempotencyService(repository=c.receipts,response_codec=c.codec)
    c.command=PlatformOrganizationRemovalCommand(session=c.bridge,removal_workflow=c.workflow,
        idempotency_service=c.idempotency,now_provider=lambda:c.now)
    c.rate=NS(ensure_authenticated_user_allowed=AsyncMock())
    c.app=create_app()
    c.app.dependency_overrides[get_current_actor]=lambda:c.actor
    c.app.dependency_overrides[get_api_request_rate_limit_service]=lambda:c.rate
    c.app.dependency_overrides[routes.get_organization_removal_command]=lambda:c.command
    return c


async def post(c,*,key=None,body=None,query='',identifier=None,headers=None):
    if not c.session.in_transaction(): c.session.begin()
    h={'X-Request-ID':'platform-membership-http'}
    if key is not None: h['Idempotency-Key']=key
    if headers is not None: h=headers
    options={} if body is None else {'json':body}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app,raise_app_exceptions=False),base_url='http://testserver') as client:
        return await client.post('/api/v1/platform/organizations/current/members/'+str(identifier or c.user_id)+'/remove'+query,headers=h,**options)


def receipt_count(c): return c.session.scalar(text('SELECT count(*) FROM removal_receipts'))


@pytest.mark.asyncio
async def test_public_platform_operation_commits_membership_grants_audit_and_minimal_response(removal_http):
    c=removal_http; r=await post(c)
    assert r.status_code==200,r.text
    assert r.json()['data']=={'user_id':str(c.user_id),'changed':True}
    assert r.headers['cache-control']=='private, no-store'
    assert r.json()['meta']=={} and r.json()['request_id']=='platform-membership-http'
    c.bridge.commit.assert_awaited_once()
    assert c.user.tenant_id is None and c.roles['client'].status=='revoked'
    assert all(c.grants[n].status=='revoked' for n in ('tenant','project','document'))
    assert c.roles['foreign'].status==c.roles['global_role'].status=='active'
    assert len(events(c))==4 and receipt_count(c)==0


@pytest.mark.asyncio
async def test_authorized_unkeyed_repeat_is_domain_noop_without_new_audit(removal_http):
    c=removal_http
    assert (await post(c)).status_code==200
    before=snapshot(c)
    r=await post(c)
    assert r.status_code==200 and r.json()['data']['changed'] is False
    assert snapshot(c)==before and receipt_count(c)==0


@pytest.mark.asyncio
async def test_keyed_replay_returns_original_response_and_encrypted_receipt_without_second_audit(removal_http):
    c=removal_http
    first=await post(c,key='remove-one'); before=snapshot(c)
    second=await post(c,key='remove-one')
    assert first.status_code==second.status_code==200,(first.text,second.text)
    assert first.json()['data']==second.json()['data']=={'user_id':str(c.user_id),'changed':True}
    assert snapshot(c)==before and receipt_count(c)==1
    row=c.session.execute(text('SELECT * FROM removal_receipts')).mappings().one()
    assert row['operation']==OPERATION and row['key_hash']==hashlib.sha256(b'remove-one').hexdigest()
    assert b'changed' not in row['response_ciphertext']
    assert c.codec.decrypt(row['response_ciphertext'])==first.json()['data']


@pytest.mark.asyncio
async def test_replay_requires_fresh_server_authority_even_when_original_receipt_exists(removal_http):
    c=removal_http; assert (await post(c,key='remove')).status_code==200
    before=snapshot(c)
    c.authorization.authorize_removal.side_effect=PlatformMembershipRemovalDeniedError('private role revocation')
    r=await post(c,key='remove')
    assert r.status_code==403 and r.json()['error']['code']=='forbidden'
    assert 'private' not in r.text and snapshot(c)==before and receipt_count(c)==1


@pytest.mark.asyncio
async def test_same_key_for_another_target_returns_conflict_before_second_removal(removal_http):
    c=removal_http; assert (await post(c,key='remove')).status_code==200
    before=snapshot(c)
    c.authorization.authorize_removal.side_effect=lambda **v:replace(c.authority,target_user_id=v['target_user_id'])
    r=await post(c,key='remove',identifier=c.other_id)
    assert r.status_code==409 and r.json()['error']['code']=='IDEMPOTENCY_KEY_REUSED',r.text
    assert snapshot(c)==before and receipt_count(c)==1


@pytest.mark.asyncio
async def test_processing_receipt_does_not_repeat_business_operation(removal_http):
    c=removal_http; assert (await post(c,key='remove')).status_code==200
    c.session.execute(text("UPDATE removal_receipts SET status='processing',response_status=NULL,response_ciphertext=NULL"))
    c.session.commit(); before=snapshot(c)
    r=await post(c,key='remove')
    assert r.status_code==409 and r.json()['error']['code']=='idempotency_in_progress'
    assert snapshot(c)==before and receipt_count(c)==1


@pytest.mark.asyncio
async def test_replay_preserves_membership_and_grants_issued_after_original_removal(removal_http):
    from database.models import ConstructionAccessGrant
    c=removal_http; assert (await post(c,key='remove')).status_code==200
    c.user.tenant_id=c.a
    new=ConstructionAccessGrant(id=uuid4(),tenant_id=c.a,user_id=c.user_id,role='SURVEYOR',
        scope_type='project',scope_id=uuid4(),status='active',granted_by_user_id=c.actor_id,granted_at=c.now)
    c.session.add(new); c.session.commit(); before=snapshot(c)
    r=await post(c,key='remove')
    assert r.status_code==200 and r.json()['data']['changed'] is True,r.text
    assert snapshot(c)==before and c.user.tenant_id==c.a and new.status=='active'


@pytest.mark.asyncio
async def test_cancellation_after_real_receipt_completion_rolls_back_all_changes(removal_http):
    c=removal_http; before=snapshot(c); complete=c.idempotency.complete
    async def cancel_after_completion(**v):
        await complete(**v)
        raise asyncio.CancelledError()
    c.idempotency.complete=AsyncMock(side_effect=cancel_after_completion)
    with pytest.raises(asyncio.CancelledError):
        await c.command.remove_from_organization(actor=c.actor,user_id=c.user_id,idempotency_key='cancel')
    assert snapshot(c)==before and receipt_count(c)==0
    c.bridge.commit.assert_not_called()


@pytest.mark.asyncio
async def test_authority_expiry_during_receipt_completion_rolls_back_business_and_audit(removal_http):
    c=removal_http; before=snapshot(c); complete=c.idempotency.complete
    c.authority=replace(c.authority,valid_until=c.now+timedelta(seconds=1))
    async def time_passes(**v):
        value=await complete(**v)
        c.now+=timedelta(seconds=2)
        return value
    c.idempotency.complete=AsyncMock(side_effect=time_passes)
    r=await post(c,key='expiry')
    assert r.status_code==403,r.text
    assert snapshot(c)==before and receipt_count(c)==0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['audit','complete','commit'])
async def test_real_sql_changes_and_receipt_roll_back_together_on_mandatory_failure(removal_http,failure):
    c=removal_http; before=snapshot(c)
    if failure=='audit':
        create=c.events.create_event
        async def fail_after_flush(**v):
            await create(**v)
            raise RuntimeError('private audit failure after flush')
        c.events.create_event=AsyncMock(side_effect=fail_after_flush)
    elif failure=='complete': c.receipts.fail=True
    else: c.bridge.commit.side_effect=RuntimeError('private commit failure')
    r=await post(c,key='remove')
    assert r.status_code==503,r.text
    assert 'private' not in r.text and snapshot(c)==before and receipt_count(c)==0


@pytest.mark.asyncio
@pytest.mark.parametrize('body',[{'tenant_id':str(uuid4())},{'actor_user_id':str(uuid4())},{'user_id':str(uuid4())},
    {'force':True},{'permission':'platform.organization.memberships.manage'},{'expected_row_version':1}])
async def test_body_cannot_override_trusted_context_or_ownership_boundary(removal_http,body):
    c=removal_http; before=snapshot(c)
    r=await post(c,body=body)
    assert r.status_code==422 and snapshot(c)==before
    c.authorization.prepare_removal.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('query',['?tenant_id='+str(uuid4()),'?user_id='+str(uuid4()),'?force=true'])
async def test_query_does_not_supply_tenant_or_force(removal_http,query):
    c=removal_http; before=snapshot(c)
    r=await post(c,query=query)
    assert r.status_code==422 and snapshot(c)==before
    c.authorization.prepare_removal.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['','   ','x'*256])
async def test_invalid_key_never_reaches_business_preparation(removal_http,key):
    c=removal_http; r=await post(c,key=key)
    assert r.status_code==422,r.text
    c.authorization.prepare_removal.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_idempotency_headers_are_rejected(removal_http):
    c=removal_http
    r=await post(c,headers=[('Idempotency-Key','a'),('Idempotency-Key','b')])
    assert r.status_code==422
    c.authorization.prepare_removal.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_rate_limit_blocks_before_workflow(removal_http):
    from services.api_rate_limits import ApiRequestRateLimitExceededError
    c=removal_http
    c.rate.ensure_authenticated_user_allowed.side_effect=ApiRequestRateLimitExceededError()
    r=await post(c)
    assert r.status_code==429,r.text
    c.authorization.prepare_removal.assert_not_awaited()


@pytest.mark.asyncio
async def test_unauthenticated_request_never_opens_command(removal_http):
    c=removal_http; del c.app.dependency_overrides[get_current_actor]
    r=await post(c)
    assert r.status_code==401,r.text
    c.authorization.prepare_removal.assert_not_awaited()


def test_factory_uses_existing_platform_session_and_does_not_require_key_for_unkeyed_path(monkeypatch):
    called=[]; session=object()
    monkeypatch.setattr(routes,'build_platform_organization_removal_command',lambda **v:called.append(v) or 'command')
    monkeypatch.setattr(routes.ApiIdempotencySettings,'from_env',lambda:(_ for _ in ()).throw(AssertionError('Must not read replay key')))
    assert routes.get_organization_removal_command(session=session,key=None)=='command'
    assert called==[{'session':session,'idempotency_encryption_key':None}]


def test_openapi_declares_separate_platform_permission_and_no_caller_tenant(removal_http):
    schema=removal_http.app.openapi()
    route=schema['paths']['/api/v1/platform/organizations/current/members/{user_id}/remove']['post']
    assert route['x-required-platform-permission']=='platform.organization.memberships.manage'
    assert route['x-idempotency-key-required'] is False
    assert all(p['name'] not in ('tenant_id','actor_user_id','force') for p in route['parameters'])
    assert 'schema' in route['responses']['200']['content']['application/json']
