"""Shared rate limiter in actual ASGI dependency graphs, using only local stores/SQLite."""
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, Request

from api.app import create_app
from api.auth import get_current_actor, require_authenticated_user_rate_limit
from api.dependencies import get_api_request_rate_limit_service
from api.routes.construction import ConstructionAPIRouter
from api.construction_recipient_rate_limit import require_invitation_recipient_rate_limit, get_invitation_recipient_actor
from services.api_rate_limits import ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore
from test_construction_01_project_read_http import walk_routes
from test_construction_1a_invitation_acceptance_http import http_case


class CountingStore(InMemoryApiRequestRateLimitStore):
    def __init__(self,clock):super().__init__(clock=clock);self.keys=[]
    async def increment(self,**values):
        self.keys.append(values['key'])
        return await super().increment(**values)


def walk_dependencies(node):
    yield node
    for child in getattr(node,'dependencies',()):yield from walk_dependencies(child)


def test_every_registered_construction_api_operation_has_the_shared_store_dependency():
    app=create_app()
    routes=[(path,route) for path,route in walk_routes(app) if path.startswith('/api/v1/construction/')]
    assert len(routes)>=38
    missing=[]
    for path,route in routes:
        if not any(dep.call is get_api_request_rate_limit_service for dep in walk_dependencies(route.dependant)):
            missing.append((path,route.methods))
    assert not missing,missing


@pytest.fixture
def limited_app():
    c=NS(actor=NS(user_id=uuid4(),tenant_id=uuid4(),status='active'),time=[0.0],calls=[],commits=0)
    c.store=CountingStore(clock=lambda:c.time[0]);c.rate=ApiRequestRateLimitService(store=c.store)
    c.db=sqlite3.connect(':memory:');c.db.execute('CREATE TABLE operations(value INTEGER)')
    c.db.execute('INSERT INTO operations VALUES(0)');c.db.commit()
    c.app=create_app()
    c.app.dependency_overrides[get_current_actor]=lambda:c.actor
    c.app.dependency_overrides[get_api_request_rate_limit_service]=lambda:c.rate
    async def permission():c.calls.append('permission')
    permission.construction_permission_code='construction.projects.read'
    async def domain_session():c.calls.append('domain_session')
    router=ConstructionAPIRouter(prefix='/construction/rate-probe',dependencies=[Depends(domain_session)])
    async def operation(request:Request):
        c.calls.append('handler')
        if request.method!='GET':
            c.db.execute('UPDATE operations SET value=value+1');c.db.commit();c.commits+=1
        return {'value':c.db.execute('SELECT value FROM operations').fetchone()[0]}
    for method in ('GET','POST','PATCH'):
        router.add_api_route('',operation,methods=[method],dependencies=[Depends(permission)])
    router.add_api_route('/existing',operation,methods=['GET'],dependencies=[
        Depends(permission),Depends(require_authenticated_user_rate_limit())])
    c.app.include_router(router,prefix='/api/v1')
    try:yield c
    finally:c.db.close()


async def request(c,method='GET',suffix='',**kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app,raise_app_exceptions=False),
        base_url='http://testserver') as client:
        return await client.request(method,'/api/v1/construction/rate-probe'+suffix,
            headers={'X-Request-ID':'construction-limit'},**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('method',['GET','POST','PATCH'])
async def test_shared_boundary_allows_120_then_blocks_before_any_domain_read_write_or_commit(limited_app,method):
    c=limited_app
    for _ in range(119):await c.rate.ensure_authenticated_user_allowed(tenant_id=c.actor.tenant_id,user_id=c.actor.user_id)
    allowed=await request(c,method)
    assert allowed.status_code==200,allowed.text
    before=c.db.execute('SELECT value FROM operations').fetchone(),c.commits
    c.calls.clear()
    denied=await request(c,method,params={'tenant_id':str(uuid4()),'user_id':str(uuid4())})
    assert denied.status_code==429,denied.text
    assert denied.json()=={'error':{'code':'RATE_LIMIT_EXCEEDED','message':'Rate limit exceeded.','request_id':'construction-limit'}}
    assert not c.calls
    assert (c.db.execute('SELECT value FROM operations').fetchone(),c.commits)==before
    assert len(set(c.store.keys))==1


@pytest.mark.asyncio
async def test_existing_explicit_default_guard_does_not_count_twice(limited_app):
    c=limited_app
    response=await request(c,suffix='/existing')
    assert response.status_code==200,response.text
    assert c.store.keys==[f'api-rate-limit:tenant:{c.actor.tenant_id}:authenticated-user:{c.actor.user_id}']
    for _ in range(118):await c.rate.ensure_authenticated_user_allowed(tenant_id=c.actor.tenant_id,user_id=c.actor.user_id)
    assert (await request(c,suffix='/existing')).status_code==200
    assert (await request(c,suffix='/existing')).status_code==429


@pytest.mark.asyncio
async def test_routes_methods_and_retries_share_bucket_while_trusted_users_tenants_and_expiry_are_separate(limited_app):
    c=limited_app
    for _ in range(120):await c.rate.ensure_authenticated_user_allowed(tenant_id=c.actor.tenant_id,user_id=c.actor.user_id)
    for method in ('GET','POST','PATCH'):
        assert (await request(c,method)).status_code==429
    c.time[0]=59.999
    assert (await request(c)).status_code==429
    c.time[0]=60
    assert (await request(c)).status_code==200
    original=c.actor
    c.actor=NS(user_id=uuid4(),tenant_id=original.tenant_id,status='active')
    assert (await request(c,'POST')).status_code==200
    c.actor=NS(user_id=original.user_id,tenant_id=uuid4(),status='active')
    assert (await request(c,'PATCH')).status_code==200
    assert len(set(c.store.keys))==3


@pytest.mark.asyncio
async def test_store_failure_fails_closed_and_is_sanitized_before_business(limited_app):
    c=limited_app
    c.rate=NS(ensure_authenticated_user_allowed=AsyncMock(side_effect=RuntimeError('private redis URL/key diagnostic')))
    response=await request(c,'POST')
    assert response.status_code==500 and response.json()['error']['code']=='internal_error'
    assert 'private' not in response.text and not c.calls and c.commits==0


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['accept','preview','decline'])
async def test_recipient_limit_precedes_token_crypto_and_domain_even_without_target_membership(http_case,operation,monkeypatch):
    c=http_case
    rate=ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.app.dependency_overrides[get_invitation_recipient_actor]=lambda:c.actor
    c.app.dependency_overrides[get_api_request_rate_limit_service]=lambda:rate
    assert c.actor.tenant_id!=c.material.tenant_id
    for _ in range(120):await rate.ensure_platform_user_allowed(user_id=c.actor.user_id)
    resolve=Mock(wraps=c.codec.resolve);monkeypatch.setattr(c.codec,'resolve',resolve)
    body=dict(c.body)
    if operation=='preview':body.pop('expected_row_version')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app,raise_app_exceptions=False),base_url='http://testserver') as client:
        response=await client.post('/api/v1/construction/invitations/'+operation,
            params={'tenant_id':str(uuid4())},json=body,headers={'Idempotency-Key':'retry-key','X-Request-ID':'recipient-limit'})
    assert response.status_code==429,response.text
    assert response.json()['error']['code']=='RATE_LIMIT_EXCEEDED'
    assert c.envelope.token not in response.text and c.envelope.invitation_context not in response.text
    resolve.assert_not_called();c.suite.require_enabled.assert_not_called();c.module.require_enabled.assert_not_called()
    c.workflow.accept_invitation.assert_not_called()


@pytest.mark.asyncio
async def test_recipient_bucket_uses_only_active_authenticated_platform_identity_without_membership(http_case):
    from test_construction_1a_invitation_acceptance_http import post
    c=http_case;store=CountingStore(clock=lambda:0.0);rate=ApiRequestRateLimitService(store=store)
    c.actor.tenant_id=None
    c.app.dependency_overrides[get_api_request_rate_limit_service]=lambda:rate
    response=await post(c)
    assert response.status_code==200,response.text
    assert store.keys==[f'api-rate-limit:platform-user:{c.actor.user_id}']
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.material.tenant_id,suite_code='construction')


def test_new_rate_responses_are_documented_without_new_business_paths():
    app=create_app();paths=app.openapi()['paths']
    for path,method in (
        ('/api/v1/construction/projects/{id}','get'),('/api/v1/construction/projects','post'),
        ('/api/v1/construction/settings/company','patch'),('/api/v1/construction/invitations/accept','post'),
        ('/api/v1/construction/invitations/preview','post'),('/api/v1/construction/invitations/decline','post')):
        assert '429' in paths[path][method]['responses']
    assert '/api/v1/construction/rate-probe' not in paths

# invitation_identity_only_v1
