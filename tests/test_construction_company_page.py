from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from fastapi import FastAPI
from unittest.mock import AsyncMock
from api.dependencies import get_tenant_module_access_service,get_tenant_suite_access_service,get_api_request_rate_limit_service
from api.routes import construction_company_page as page
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext,CONSTRUCTION_ROLE_PERMISSIONS
from services.files import IMAGE_MAX_SIZE_BYTES,_FILE_TYPES_BY_EXTENSION

@pytest.mark.parametrize('role',['OWNER','ADMIN','OBSERVER'])
def test_capabilities_use_existing_scoped_policy(role):
    user,tenant=uuid4(),uuid4();actor=SimpleNamespace(user_id=user,tenant_id=tenant,status='active',permissions=())
    kind='tenant' if role in ('OWNER','ADMIN') else 'project';scope=tenant if kind=='tenant' else uuid4()
    context=ConstructionAccessContext(user,tenant,(role,),tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),(ConstructionGrantContext(role,kind,scope),))
    value=page.capabilities(actor,context)
    assert value['can_manage_profile']==(role in ('OWNER','ADMIN'))
    assert value['can_manage_settings']==(role in ('OWNER','ADMIN'))
    assert value['image_max_size_bytes']==IMAGE_MAX_SIZE_BYTES
    assert {x['mime_type'] for x in value['image_types']}=={mime for kind,mime in _FILE_TYPES_BY_EXTENSION.values() if kind=='image'}

@pytest.mark.parametrize('url',['https://storage.example.com/?key=x','https://user:secret@storage.example.com','http://storage.example.com','https://storage.example.com;unsafe invalid'])
def test_storage_origin_fails_closed(monkeypatch,url):
    monkeypatch.setenv('SUPABASE_URL',url);assert page.storage_origin() is None

@pytest.mark.asyncio
@pytest.mark.parametrize('suffix,media',[('','text/html'),('/style.css','text/css'),('/flow.mjs','text/javascript'),('/boot.mjs','text/javascript')])
async def test_shell_assets_no_database(monkeypatch,suffix,media):
    monkeypatch.setenv('SUPABASE_URL','https://storage.example.com')
    app=FastAPI();app.include_router(page.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://app.example.com') as client:
        response=await client.get(page.PREFIX+suffix)
    assert response.status_code==200 and response.headers['content-type'].startswith(media)
    assert response.headers['referrer-policy']=='no-referrer'
    assert 'https://storage.example.com' in response.headers['content-security-policy']
    assert 'no-store' in response.headers['cache-control']

@pytest.mark.asyncio
async def test_capability_http_uses_existing_auth_context_and_no_store():
    user,tenant=uuid4(),uuid4();actor=SimpleNamespace(user_id=user,tenant_id=tenant,status='active',permissions=())
    context=ConstructionAccessContext(user,tenant,('OWNER',),tuple(CONSTRUCTION_ROLE_PERMISSIONS['OWNER']),
        (ConstructionGrantContext('OWNER','tenant',tenant),))
    app=FastAPI();app.include_router(page.api_router,prefix='/api/v1')
    @app.middleware('http')
    async def identify(request,call_next):request.state.request_id='company-ui';return await call_next(request)
    app.dependency_overrides[page.get_current_actor]=lambda:actor
    app.dependency_overrides[page.get_construction_access_context]=lambda:context
    app.dependency_overrides[get_tenant_suite_access_service]=lambda:SimpleNamespace(require_enabled=AsyncMock())
    app.dependency_overrides[get_tenant_module_access_service]=lambda:SimpleNamespace(require_enabled=AsyncMock())
    rate=SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    app.dependency_overrides[get_api_request_rate_limit_service]=lambda:rate
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://app.example.com') as client:
        response=await client.get('/api/v1/construction/ui/company-capabilities')
    assert response.status_code==200,response.text
    assert response.json()['data']['can_manage_profile'] is True
    assert response.json()['data']['can_manage_settings'] is True
    assert response.headers['cache-control']=='private, no-store'
    rate.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=tenant,user_id=user)
