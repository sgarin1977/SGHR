"""Local ASGI + real SQLite unlink workflow; no deployed HTTP or Telegram."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import Depends
import httpx
import pytest
from sqlalchemy import select

import api.routes.platform_telegram_binding as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from api.settings import ApiConfigurationError
from database.models import ApiIdempotencyRecord, EventLog, UserAccount
from services.api_idempotency import hash_request_payload
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.platform_telegram_account_unlink import UNLINK_OPERATION, PlatformTelegramAccountUnlinkResult
from test_construction_1a_platform_telegram_account_unlink_workflow import workflow, environment, store, state, KEY


PREFIX='/api/v1/platform/integrations/telegram'


@pytest.fixture
def unlink_http(workflow):
    c=workflow
    c.app=create_app()
    c.rates=SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    c.begins=0
    async def begin():
        c.begins+=1;c.session.begin()
    c.bridge.begin=begin
    def provide(session=Depends(routes.get_binding_session)):
        assert session is c.bridge
        return c.workflow
    c.app.dependency_overrides[get_api_session]=lambda:c.bridge
    c.app.dependency_overrides[get_current_actor]=lambda:c.actor
    c.app.dependency_overrides[get_api_request_rate_limit_service]=lambda:c.rates
    c.app.dependency_overrides[routes.get_account_unlink_workflow]=provide
    return c


async def post(c,*,key='unlink-http-key',body=None,identifier=None,headers=None,method='POST'):
    values=[('X-Request-ID','unlink-http-attempt')]
    if key is not None:values.append(('Idempotency-Key',key))
    values.extend(headers or [])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app,raise_app_exceptions=False),base_url='http://testserver') as client:
        return await client.request(method,PREFIX+'/accounts/'+str(identifier or c.account.id)+'/unlink',
            json={'expected_row_version':1} if body is None else body,headers=values,
            params={'user_id':str(c.other),'tenant_id':str(uuid4()),'telegram_user_id':'666666666'})


@pytest.mark.asyncio
async def test_unlink_http_uses_platform_actor_returns_public_receipt_and_keeps_sessions(unlink_http):
    c=unlink_http
    response=await post(c)
    assert response.status_code==200,response.text
    assert response.json()==dict(data=dict(account_id=str(c.account.id),status='unlinked',row_version=2,changed=True),
        meta={},request_id='unlink-http-attempt')
    assert response.headers['X-Request-ID']=='unlink-http-attempt'
    assert response.headers['Cache-Control']=='no-store' and response.headers['Pragma']=='no-cache'
    assert response.headers['Referrer-Policy']=='no-referrer'
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant,user_id=c.owner)
    audit=c.session.execute(select(EventLog)).scalar_one()
    assert audit.user_id==c.owner and audit.tenant_id==c.tenant and audit.entity_id==c.account.id
    assert audit.trace_id.startswith('telegram-binding:') and audit.trace_id!='unlink-http-attempt'
    record=c.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    assert record.operation==UNLINK_OPERATION and record.principal_id==c.owner and record.tenant_id==c.tenant
    assert c.account.status=='unlinked' and c.second.status==c.email.status==c.foreign.status=='active'
    assert state(c)['api_auth_sessions']==c.workflow_before['api_auth_sessions']
    for private in ('123456789','retained-profile','raw_profile','response_ciphertext','user_id','tenant_id'):
        assert private not in response.text
    assert c.bridge.commits==1


@pytest.mark.asyncio
async def test_http_retry_uses_original_result_without_second_audit_and_reopens_transaction(unlink_http):
    c=unlink_http
    first=await post(c);assert first.status_code==200
    before=state(c);c.session.commit()
    c.service.unlink_account=AsyncMock(side_effect=AssertionError('Replay must not invoke unlink again.'))
    second=await post(c)
    assert second.status_code==200 and second.json()==first.json()
    assert state(c)==before and c.begins==1
    c.service.unlink_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_version_noop_and_stale_version_have_distinct_http_results(unlink_http):
    c=unlink_http
    assert (await post(c)).status_code==200
    noop=await post(c,key='new-noop-key',body={'expected_row_version':2})
    assert noop.status_code==200 and noop.json()['data']['changed'] is False
    assert noop.json()['data']['row_version']==2
    before=state(c)
    conflict=await post(c,key='new-stale-key')
    assert conflict.status_code==409 and conflict.json()['error']['code']=='ROW_VERSION_CONFLICT'
    assert state(c)==before and len(c.session.execute(select(EventLog.id)).all())==1


@pytest.mark.asyncio
@pytest.mark.parametrize('key',[None,'',' ','x'*256])
async def test_missing_or_invalid_key_is_rejected_before_business_writes(unlink_http,key):
    c=unlink_http
    response=await post(c,key=key)
    assert response.status_code==422
    assert state(c)==c.workflow_before and c.bridge.commits==0


@pytest.mark.asyncio
async def test_duplicate_key_headers_are_rejected(unlink_http):
    c=unlink_http
    response=await post(c,headers=[('Idempotency-Key','other-key')])
    assert response.status_code==422 and state(c)==c.workflow_before


@pytest.mark.asyncio
@pytest.mark.parametrize('body',[{}, {'expected_row_version':None},{'expected_row_version':True},
    {'expected_row_version':0},{'expected_row_version':'1'},{'expected_row_version':1.0},{'expected_row_version':2**63-1}])
async def test_version_body_is_required_and_strict(unlink_http,body):
    c=unlink_http
    response=await post(c,body=body)
    assert response.status_code==422 and state(c)==c.workflow_before and c.bridge.commits==0


@pytest.mark.asyncio
@pytest.mark.parametrize('field',['user_id','tenant_id','telegram_user_id','result'])
async def test_caller_cannot_supply_extra_identity_or_mutation_fields(unlink_http,field):
    c=unlink_http
    response=await post(c,body={'expected_row_version':1,field:'private-spoofed-context'})
    assert response.status_code==422 and 'private-spoofed-context' not in response.text
    assert state(c)==c.workflow_before


@pytest.mark.asyncio
@pytest.mark.parametrize('target',['foreign','email','missing'])
async def test_unavailable_account_does_not_expose_identity_or_create_reservation(unlink_http,target):
    c=unlink_http
    identifier=c.foreign.id if target=='foreign' else c.email.id if target=='email' else uuid4()
    response=await post(c,identifier=identifier)
    assert response.status_code==403 and response.json()['error']['code']=='account_unavailable'
    assert '666666666' not in response.text and state(c)==c.workflow_before


@pytest.mark.asyncio
async def test_invalid_path_identifier_is_validation_error(unlink_http):
    c=unlink_http
    response=await post(c,identifier='not-a-uuid')
    assert response.status_code==422 and state(c)==c.workflow_before


@pytest.mark.asyncio
@pytest.mark.parametrize('authorization',[None,'ApiKey private-api-key'])
async def test_unlink_requires_platform_bearer_authentication(unlink_http,authorization):
    c=unlink_http
    c.app.dependency_overrides.pop(get_current_actor)
    response=await post(c,headers=[] if authorization is None else [('Authorization',authorization)])
    assert response.status_code==401 and response.headers['WWW-Authenticate']=='Bearer'
    assert 'private-api-key' not in response.text and state(c)==c.workflow_before


@pytest.mark.asyncio
async def test_shared_platform_rate_limit_stops_unlink(unlink_http):
    c=unlink_http
    c.rates.ensure_authenticated_user_allowed.side_effect=ApiRequestRateLimitExceededError()
    response=await post(c,headers=[('X-Forwarded-For','8.8.8.8')])
    assert response.status_code==429 and response.json()['error']['code']=='RATE_LIMIT_EXCEEDED'
    assert state(c)==c.workflow_before and c.bridge.commits==0


@pytest.mark.asyncio
async def test_changed_command_under_existing_key_is_machine_readable_conflict(unlink_http):
    c=unlink_http
    assert (await post(c)).status_code==200
    before=state(c)
    response=await post(c,identifier=c.second.id)
    assert response.status_code==409 and response.json()['error']['code']=='IDEMPOTENCY_KEY_REUSED'
    assert state(c)==before


@pytest.mark.asyncio
async def test_processing_unlink_is_a_safe_409_without_second_mutation(unlink_http):
    import hashlib
    c=unlink_http
    c.session.add(ApiIdempotencyRecord(id=uuid4(),tenant_id=c.tenant,principal_type='user',principal_id=c.owner,
        operation=UNLINK_OPERATION,key_hash=hashlib.sha256(b'unlink-http-key').hexdigest(),
        request_hash=hash_request_payload(dict(account_id=str(c.account.id),expected_row_version=1)),
        status='processing',created_at=c.now,expires_at=c.now+timedelta(hours=24)))
    c.session.commit();before=state(c)
    response=await post(c)
    assert response.status_code==409 and response.json()['error']['code']=='idempotency_in_progress'
    assert state(c)==before and c.bridge.commits==0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['audit','completion'])
async def test_internal_failure_is_private_and_rolls_back_all_three_effects(unlink_http,failure):
    c=unlink_http
    if failure=='audit':
        original=c.service.events.create_event
        async def fail(**kwargs):
            await original(**kwargs)
            raise RuntimeError('private-audit-details')
        c.service.events.create_event=fail
    else:
        original=c.workflow.idempotency.complete
        async def fail(**kwargs):
            await original(**kwargs)
            raise RuntimeError('private-completion-details')
        c.workflow.idempotency.complete=fail
    response=await post(c)
    assert response.status_code==500 and response.json()['error']['code']=='internal_error'
    assert 'private-' not in response.text and state(c)==c.workflow_before and c.bridge.commits==0


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('account_id',uuid4()),('user_id',uuid4()),('tenant_id',uuid4()),
    ('row_version',True),('changed','true'),('status','active')])
async def test_invalid_workflow_response_is_not_coerced_or_leaked(unlink_http,field,value):
    c=unlink_http
    result=PlatformTelegramAccountUnlinkResult(c.account.id,c.owner,c.tenant,'unlinked',2,True)
    c.workflow.unlink_account=AsyncMock(return_value=replace(result,**{field:value}))
    response=await post(c)
    assert response.status_code==500 and response.json()['error']['code']=='internal_error'
    if isinstance(value,UUID):assert str(value) not in response.text
    assert state(c)==c.workflow_before


def test_configuration_uses_shared_key_without_bot_token_username_or_verifier(unlink_http,monkeypatch):
    c=unlink_http
    monkeypatch.setattr(routes.ApiIdempotencySettings,'from_env',lambda:SimpleNamespace(encryption_key=KEY))
    monkeypatch.setattr(routes,'get_binding_verifier',lambda:pytest.fail('Unlink must not need Telegram proof.'))
    monkeypatch.setattr(routes,'get_binding_bot_username',lambda:pytest.fail('Unlink must not need a bot username.'))
    built=routes.get_account_unlink_workflow(c.bridge)
    assert built.session is built.service.session is built.service.events.session is c.bridge
    def missing():raise ApiConfigurationError('private-idempotency-key-details')
    monkeypatch.setattr(routes.ApiIdempotencySettings,'from_env',missing)
    with pytest.raises(routes.ApiHttpError) as error:routes.get_account_unlink_workflow(c.bridge)
    assert error.value.status_code==503 and 'private-' not in str(error.value)


def test_unlink_openapi_is_platform_only_with_required_key_version_and_actor(unlink_http):
    c=unlink_http
    path=PREFIX+'/accounts/{id}/unlink'
    operation=c.app.openapi()['paths'][path]['post']
    assert operation['requestBody']['required'] is True
    key=next(p for p in operation['parameters'] if p['name']=='Idempotency-Key')
    assert key['required'] is True
    schemas=c.app.openapi()['components']['schemas']
    assert schemas['BindingVersionRequest']['required']==['expected_row_version']
    assert schemas['BindingVersionRequest']['additionalProperties'] is False
    assert set(schemas['AccountUnlinkedResponse']['properties'])=={'account_id','status','row_version','changed'}
    def calls(dependant):
        yield dependant.call
        for child in dependant.dependencies:yield from calls(child)
    route=next(r for r in routes.router.routes if r.path.endswith('/accounts/{id}/unlink'))
    dependencies=list(calls(route.dependant))
    assert get_current_actor in dependencies and routes.actor_limit in dependencies
    assert routes.get_binding_verifier not in dependencies and routes.get_binding_bot_username not in dependencies
    client_unlink_path = '/api/v1/construction/clients/{id}/user-link/unlink'
    client_unlink = c.app.openapi()['paths'][client_unlink_path]['post']
    assert client_unlink['operationId'] != operation['operationId']


@pytest.mark.asyncio
@pytest.mark.parametrize('method',['GET','PATCH','DELETE'])
async def test_unlink_is_only_the_registered_post_operation(unlink_http,method):
    c=unlink_http
    response=await post(c,method=method)
    assert response.status_code==405 and state(c)==c.workflow_before
