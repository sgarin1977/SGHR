from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from pydantic import SecretStr
import pytest

from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)


@pytest.fixture
def session():
    return PlatformApiClientSession(uuid4(), uuid4(), SecretStr('test-access-token'))


def envelope(data, meta=None):
    return dict(data=data, meta=meta or {}, request_id='transport-test')


@pytest.mark.asyncio
async def test_reads_only_approved_http_paths_and_supplies_bearer_without_tenant_headers(session):
    seen = []
    entity_id = uuid4()
    def transport(request):
        seen.append(request)
        assert request.headers['Authorization'] == 'Bearer test-access-token'
        assert 'X-Tenant-ID' not in request.headers and 'X-User-ID' not in request.headers
        return httpx.Response(200, json=envelope(dict(id=str(entity_id), tenant_id=str(session.tenant_id))))
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(transport)) as http:
        client = ConstructionApiClient(http=http)
        await client.lead(session=session, lead_id=entity_id)
        await client.client(session=session, client_id=entity_id)
        await client.project(session=session, project_id=entity_id)
    assert [r.url.path for r in seen] == ['/api/v1/construction/' + prefix + '/' + str(entity_id)
        for prefix in ('crm/leads', 'clients', 'projects')]
    assert all(r.method == 'GET' and not r.content for r in seen)


@pytest.mark.asyncio
async def test_pagination_and_filters_are_forwarded_without_reconstructing_cursors(session):
    cursor = 'opaque-existing-api-cursor'
    def transport(request):
        assert dict(request.url.params) == dict(limit='10', cursor=cursor, status='new', source='phone')
        return httpx.Response(200, json=envelope([], {'pagination': {'next_cursor': 'next-api-cursor'}}))
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(transport)) as http:
        result = await ConstructionApiClient(http=http).leads(session=session, limit=10, cursor=cursor,
            filters={'status': 'new', 'source': 'phone'})
    assert result.meta['pagination']['next_cursor'] == 'next-api-cursor'


@pytest.mark.asyncio
@pytest.mark.parametrize('status,code', [(401,'invalid_token'),(403,'forbidden'),(404,'not_found'),(429,'RATE_LIMIT_EXCEEDED'),(500,'internal_error')])
async def test_api_errors_are_returned_without_raw_body_or_secret(session, status, code):
    def transport(request):
        return httpx.Response(status, json={'error': dict(code=code, message='private SQL diagnostic', request_id='r-1')})
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(ConstructionApiClientError) as captured:
            await ConstructionApiClient(http=http).lead(session=session, lead_id=uuid4())
    assert (captured.value.status_code, captured.value.code) == (status, code)
    assert 'private SQL diagnostic' not in str(captured.value)
    assert 'test-access-token' not in str(captured.value)


@pytest.mark.asyncio
async def test_redirect_does_not_forward_credentials(session):
    seen = []
    def transport(request):
        seen.append(request)
        return httpx.Response(307, headers={'Location': 'https://foreign.example/steal'})
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(transport), follow_redirects=True) as http:
        with pytest.raises(ConstructionApiClientError, match='api_redirect_denied'):
            await ConstructionApiClient(http=http).lead(session=session, lead_id=uuid4())
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['wrong_tenant', 'wrong_id', 'html', 'duplicate', 'too_large'])
async def test_invalid_server_response_is_not_presented_as_data(session, kind):
    entity_id = uuid4()
    def transport(request):
        if kind == 'html': return httpx.Response(200, content=b'<html>proxy error</html>')
        if kind == 'duplicate': return httpx.Response(200, content=b'{"data":{},"data":{},"meta":{},"request_id":"r"}')
        if kind == 'too_large': return httpx.Response(200, content=b'x' * (1024 * 1024 + 1))
        return httpx.Response(200, json=envelope(dict(id=str(uuid4()) if kind=='wrong_id' else str(entity_id),
            tenant_id=str(uuid4()) if kind=='wrong_tenant' else str(session.tenant_id))))
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).lead(session=session, lead_id=entity_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,expected', [(httpx.ReadTimeout,504),(httpx.ConnectError,503)])
async def test_network_errors_are_safe_and_not_retried(session, failure, expected):
    calls = []
    def transport(request):
        calls.append(request)
        raise failure('private connection details', request=request)
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(transport)) as http:
        with pytest.raises(ConstructionApiClientError) as captured:
            await ConstructionApiClient(http=http).lead(session=session, lead_id=uuid4())
    assert captured.value.status_code == expected and len(calls) == 1
    assert 'private connection details' not in str(captured.value)


def test_session_does_not_expose_or_issue_credentials(session):
    assert 'test-access-token' not in repr(session)
    with pytest.raises(ValueError):
        PlatformApiClientSession(session.user_id, session.tenant_id, 'raw-string-token')


@pytest.mark.asyncio
async def test_caller_identity_filters_cannot_replace_signed_api_context(session):
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(lambda r: pytest.fail('unexpected request'))) as http:
        with pytest.raises(ValueError):
            await ConstructionApiClient(http=http).leads(session=session, filters={'tenant_id': str(uuid4())})


# Actual registered Construction routes, actual Bearer decoder and fresh actor
# dependency. Only storage/module/rate dependencies are local test fixtures.
from test_construction_1a_lead_read_http import read_http, list_case, detail_case, read_case, terminal_case


@pytest.fixture
def api_case(read_http):
    from api.auth import get_access_token_codec, get_api_identity_service, get_current_actor
    from api.security import AccessTokenCodec
    c = read_http
    c.app.dependency_overrides.pop(get_current_actor)
    c.codec = AccessTokenCodec(secret='local-construction-api-client-test-key', issuer='local-test', audience='local-test', access_ttl_seconds=600)
    c.identity = SimpleNamespace(require_actor=AsyncMock(return_value=c.actor))
    c.app.dependency_overrides[get_access_token_codec] = lambda: c.codec
    c.app.dependency_overrides[get_api_identity_service] = lambda: c.identity
    token = c.codec.issue_access_token(user_id=c.user, tenant_id=c.tenant,
        now=datetime.now(timezone.utc)-timedelta(seconds=5))
    c.client_session = PlatformApiClientSession(c.user, c.tenant, SecretStr(token))
    return c


@pytest.mark.asyncio
async def test_real_api_auth_routing_services_and_next_action_are_used(api_case):
    c = api_case
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.ASGITransport(app=c.app)) as http:
        client = ConstructionApiClient(http=http)
        listed = await client.leads(session=c.client_session)
        detailed = await client.lead(session=c.client_session, lead_id=c.lead.id)
    assert listed.data[0]['id'] == detailed.data['id'] == str(c.lead.id)
    assert detailed.data['next_action']['id'] == str(c.row.id)
    assert c.identity.require_actor.await_count == 2
    assert c.suite.require_enabled.await_count == c.module.require_enabled.await_count == 2
    assert c.rate.ensure_authenticated_user_allowed.await_count == 2


@pytest.mark.asyncio
async def test_real_api_rejects_invalid_bearer_before_business_read(api_case):
    c = api_case
    bad = PlatformApiClientSession(c.user, c.tenant, SecretStr('invalid-bearer'))
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.ASGITransport(app=c.app)) as http:
        with pytest.raises(ConstructionApiClientError) as captured:
            await ConstructionApiClient(http=http).leads(session=bad)
    assert captured.value.status_code == 401
    c.identity.require_actor.assert_not_called()
    c.list_repo.list_active_leads.assert_not_called()


@pytest.mark.asyncio
async def test_real_api_revoked_membership_stays_denied_without_direct_service_fallback(api_case):
    from services.api_identity import ApiIdentityAccessError
    c = api_case
    c.identity.require_actor.side_effect = ApiIdentityAccessError()
    async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.ASGITransport(app=c.app)) as http:
        with pytest.raises(ConstructionApiClientError) as captured:
            await ConstructionApiClient(http=http).leads(session=c.client_session)
    assert captured.value.status_code == 401
    c.list_repo.list_active_leads.assert_not_called()
