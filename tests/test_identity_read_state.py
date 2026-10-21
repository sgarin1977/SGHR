from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.dependencies import get_api_request_rate_limit_service
import api.routes.platform_telegram_account_state as routes
from database.repositories.platform_telegram_account_state import PlatformTelegramAccountStateRepository
from services.platform_telegram_account_state import build_platform_telegram_account_state_service, PlatformTelegramAccountStateError
from services.api_platform_principal import ApiPlatformPrincipal
from services.api_identity import ApiIdentityAccessError
from services.api_rate_limits import ApiRequestRateLimitService
from test_construction_1a_client_card_read_service import card_case
from test_construction_01_client_read_http import client_http, get, no_write

@pytest.fixture
def accounts():
    engine = create_engine('sqlite://')
    sync = Session(engine)
    sync.execute(text('CREATE TABLE users (id CHAR(32) PRIMARY KEY, status TEXT NOT NULL)'))
    sync.execute(text('CREATE TABLE user_accounts (id CHAR(32) PRIMARY KEY, user_id CHAR(32), platform TEXT, platform_user_id TEXT, status TEXT, row_version INTEGER)'))
    own, foreign = uuid4(), uuid4()
    for user in (own, foreign):
        sync.execute(text("INSERT INTO users VALUES (:id, 'active')"), {'id': user.hex})
    for number, user, platform, status, version in (
            (1, own, 'telegram', 'active', 7), (2, own, 'telegram', 'unlinked', 11),
            (3, foreign, 'telegram', 'active', 1), (4, own, 'email', 'active', 1)):
        sync.execute(text('INSERT INTO user_accounts VALUES (:id,:user,:platform,:tg,:status,:version)'),
            dict(id=UUID(int=number).hex, user=user.hex, platform=platform, tg=str(1000+number), status=status, version=version))
    sync.commit()
    statements = []
    async def execute(statement):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return sync.execute(statement)
    session = SimpleNamespace(no_autoflush=nullcontext(), execute=execute, in_transaction=lambda:True)
    service = build_platform_telegram_account_state_service(session=session)
    value = SimpleNamespace(sync=sync, session=session, own=own, foreign=foreign,
        principal=ApiPlatformPrincipal(own), service=service, statements=statements)
    yield value
    sync.close()
    engine.dispose()

@pytest.mark.asyncio
async def test_real_database_owner_only_pagination_independent_account_versions(accounts):
    c = accounts
    first = await c.service.list_accounts(principal=c.principal, limit=1)
    assert [(x.account_id, x.row_version, x.status) for x in first.items] == [(UUID(int=1),7,'active')]
    second = await c.service.list_accounts(principal=c.principal, limit=1, after=first.next_cursor)
    assert [(x.account_id, x.row_version, x.status) for x in second.items] == [(UUID(int=2),11,'unlinked')]
    assert second.next_cursor is None
    assert all(statement.startswith('SELECT ') for statement in c.statements)
    assert c.sync.execute(text('SELECT count(*) FROM user_accounts')).scalar_one() == 4

@pytest.mark.asyncio
async def test_foreign_cursor_never_selects_foreign_accounts(accounts):
    page = await accounts.service.list_accounts(principal=accounts.principal, after=UUID(int=3))
    assert page.items == () and page.next_cursor is None

@pytest.mark.asyncio
async def test_inactive_canonical_owner_denied(accounts):
    accounts.sync.execute(text("UPDATE users SET status='blocked' WHERE id=:id"), {'id':accounts.own.hex})
    with pytest.raises(ApiIdentityAccessError):
        await accounts.service.list_accounts(principal=accounts.principal)

@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['owner', 'version', 'status', 'telegram'])
async def test_invalid_projection_fails_closed(accounts, fault):
    row = (await accounts.service.repository.list_owned_states(user_id=accounts.own,limit=20))[0]
    updates = dict(owner={'user_id':accounts.foreign}, version={'row_version':True},
        status={'status':'unknown'}, telegram={'telegram_user_id':'001'})
    accounts.service.repository.list_owned_states = AsyncMock(return_value=(replace(row,**updates[fault]),))
    with pytest.raises(PlatformTelegramAccountStateError):
        await accounts.service.list_accounts(principal=accounts.principal)

@pytest.mark.asyncio
async def test_block_after_query_denied(accounts):
    original = accounts.service.repository.list_owned_states
    async def blocking(**options):
        rows = await original(**options)
        accounts.sync.execute(text("UPDATE users SET status='blocked' WHERE id=:id"), {'id':accounts.own.hex})
        return rows
    accounts.service.repository.list_owned_states = blocking
    with pytest.raises(ApiIdentityAccessError):
        await accounts.service.list_accounts(principal=accounts.principal)

def account_app(c):
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1')
    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'accounts-read'
        return await call_next(request)
    @app.exception_handler(ApiHttpError)
    async def failure(request: Request, exc):
        return JSONResponse({'error':{'code':exc.code}},status_code=exc.status_code,headers=exc.headers)
    c.rate = SimpleNamespace(ensure_platform_user_allowed=AsyncMock())
    app.dependency_overrides[get_authenticated_platform_principal] = lambda:c.principal
    app.dependency_overrides[routes.get_telegram_account_state_service] = lambda:c.service
    app.dependency_overrides[get_api_request_rate_limit_service] = lambda:c.rate
    return app

@pytest.mark.asyncio
async def test_http_real_service_without_membership_and_safe_projection(accounts):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=account_app(accounts)),base_url='http://test') as client:
        response = await client.get('/api/v1/platform/integrations/telegram/accounts?limit=1')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['data'] == [dict(account_id=str(UUID(int=1)),telegram_user_id='1001',status='active',row_version=7)]
    assert data['meta']['pagination']['next_cursor'] == str(UUID(int=1))
    assert 'no-store' in response.headers['cache-control']
    assert str(accounts.foreign) not in response.text and 'user_id"' not in response.text.replace('telegram_user_id"','')
    accounts.rate.ensure_platform_user_allowed.assert_awaited_once_with(user_id=accounts.own)

@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['tenant_id=x','user_id=x','limit=0','limit=101','cursor=bad','limit=1&limit=2'])
async def test_http_rejects_untrusted_selectors_and_invalid_pages(accounts,query):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=account_app(accounts)),base_url='http://test') as client:
        response = await client.get('/api/v1/platform/integrations/telegram/accounts?'+query)
    assert response.status_code == 422
    assert accounts.statements == []

@pytest.mark.asyncio
async def test_rate_limit_is_shared_and_platform_user_scoped():
    store = SimpleNamespace(increment=AsyncMock(return_value=1))
    service = ApiRequestRateLimitService(store=store)
    owner = uuid4()
    await service.ensure_platform_user_allowed(user_id=owner)
    assert store.increment.await_args.kwargs['key'] == f'api-rate-limit:platform-user:{owner}'

@pytest.mark.asyncio
async def test_client_current_state_comes_from_real_card_projection(client_http):
    c = client_http
    target = uuid4()
    c.client.row_version = 17
    c.client.linked_user_id = target
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()['data']['row_version'] == 17
    assert response.json()['data']['linked_user_id'] == str(target)
    no_write(c)

@pytest.mark.asyncio
@pytest.mark.parametrize('version', [None, True, 0, -1, '2', 2**63])
async def test_invalid_client_version_never_fabricated(client_http, version):
    client_http.client.row_version = version
    response = await get(client_http)
    assert response.status_code == 500
    no_write(client_http)


from database.construction_client_unlink_context import OWN_LINK_CLIENT_SETTING, OWN_LINK_USER_SETTING
from services.construction_client_own_link_state import build_construction_client_own_link_state_service
from services.construction_client_unlink import ConstructionClientUnlinkDeniedError
import api.routes.construction_client_own_link_state as own_routes

@pytest.fixture
def own_state(accounts):
    c = accounts
    c.client_id, c.tenant = uuid4(), uuid4()
    c.sync.execute(text('CREATE TABLE construction_clients (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), linked_user_id CHAR(32), row_version INTEGER, deleted_at TIMESTAMP)'))
    c.sync.execute(text('INSERT INTO construction_clients VALUES (:id,:tenant,:user,23,NULL)'),
        dict(id=c.client_id.hex, tenant=c.tenant.hex,user=c.own.hex))
    c.sync.commit()
    c.settings = {'transaction_read_only':'on','app.current_tenant_id':'', 'app.current_invitation_id':'',
        'app.current_invitation_recipient_id':'', OWN_LINK_CLIENT_SETTING:str(c.client_id),OWN_LINK_USER_SETTING:str(c.own)}
    raw = c.sync.connection().connection.driver_connection
    raw.create_function('current_setting', -1, lambda *args:c.settings.get(args[0],''))
    platform = SimpleNamespace(no_autoflush=nullcontext(), execute=c.session.execute)
    c.own_service = build_construction_client_own_link_state_service(session=c.session, platform_session=platform)
    return c

@pytest.mark.asyncio
async def test_native_sql_own_metadata_without_membership(own_state):
    c = own_state
    state = await c.own_service.get_state(principal=c.principal, client_id=c.client_id)
    assert vars(state) == dict(client_id=c.client_id,linked_user_id=c.own,row_version=23)
    assert all(s.startswith('SELECT ') for s in c.statements)
    assert not any('display_name' in s or 'construction_projects' in s for s in c.statements)

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['foreign','archived','unlinked','missing','read_write','tenant','other_client_hint'])
async def test_own_read_fails_closed_for_hidden_or_wrong_context(own_state,fault):
    c=own_state
    if fault=='foreign': c.sync.execute(text('UPDATE construction_clients SET linked_user_id=:id'),{'id':c.foreign.hex})
    if fault=='archived': c.sync.execute(text("UPDATE construction_clients SET deleted_at='2026-10-20'"))
    if fault=='unlinked': c.sync.execute(text('UPDATE construction_clients SET linked_user_id=NULL'))
    if fault=='missing': c.sync.execute(text('DELETE FROM construction_clients'))
    if fault=='read_write': c.settings['transaction_read_only']='off'
    if fault=='tenant': c.settings['app.current_tenant_id']=str(c.tenant)
    if fault=='other_client_hint': c.settings[OWN_LINK_CLIENT_SETTING]=str(uuid4())
    with pytest.raises(ConstructionClientUnlinkDeniedError):
        await c.own_service.get_state(principal=c.principal,client_id=c.client_id)

@pytest.mark.asyncio
async def test_ownership_rechecked_between_context_and_version(own_state):
    c=own_state
    original=c.own_service.repository.get_owned_version
    async def changed(**kwargs):
        c.sync.execute(text('UPDATE construction_clients SET linked_user_id=:id'),{'id':c.foreign.hex})
        return await original(**kwargs)
    c.own_service.repository.get_owned_version=changed
    with pytest.raises(ConstructionClientUnlinkDeniedError):
        await c.own_service.get_state(principal=c.principal,client_id=c.client_id)

@pytest.mark.asyncio
async def test_own_state_http_only_metadata_without_tenant_selector(own_state):
    c=own_state
    app=account_app(c)
    app.include_router(own_routes.router,prefix='/api/v1')
    app.dependency_overrides[own_routes.get_own_link_state_service]=lambda:c.own_service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get(f'/api/v1/construction/clients/{c.client_id}/user-link/self')
        assert response.status_code==200,response.text
        assert response.json()['data']==dict(client_id=str(c.client_id),linked_user_id=str(c.own),row_version=23)
        assert str(c.tenant) not in response.text
        assert 'no-store' in response.headers['cache-control']
        bad=await client.get(f'/api/v1/construction/clients/{c.client_id}/user-link/self?tenant_id={c.tenant}')
        assert bad.status_code==422
        c.sync.execute(text('UPDATE construction_clients SET linked_user_id=NULL'))
        hidden=await client.get(f'/api/v1/construction/clients/{c.client_id}/user-link/self')
        assert hidden.status_code==404

@pytest.mark.asyncio
async def test_http_rate_limit_and_read_failure_have_safe_errors(accounts):
    from services.api_rate_limits import ApiRequestRateLimitExceededError
    app=account_app(accounts)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        accounts.rate.ensure_platform_user_allowed.side_effect=ApiRequestRateLimitExceededError()
        response=await client.get('/api/v1/platform/integrations/telegram/accounts')
        assert response.status_code==429
        accounts.rate.ensure_platform_user_allowed.side_effect=None
        accounts.service.repository.list_owned_states=AsyncMock(side_effect=RuntimeError('private SQL detail'))
        response=await client.get('/api/v1/platform/integrations/telegram/accounts')
        assert response.status_code==503 and 'private SQL detail' not in response.text
