from construction_rate_limit_fixture import with_local_construction_rate_limit
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import jwt.api_jwt as pyjwt_impl
import api.security as token_security
import pytest
from fastapi import Depends
from sqlalchemy import text, update

import api.routes.construction_client_confirmation as routes
from api.app import create_app
from api.auth import get_access_token_codec, get_current_actor
from api.dependencies import get_api_session, get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.security import AccessTokenCodec
from api.settings import ApiConfigurationError
from database.models import ConstructionClient
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction_client_link_confirmation import ConstructionClientLinkConfirmationResult, PlatformClientLinkConfirmationIdentityReader
from services.construction_client_link_request_issuance import ConstructionClientLinkIssuanceResult
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_client_link_confirmation_workflow import pipeline, snapshot
from test_construction_1a_client_link_confirmation import sql_confirmation, TOKEN
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db
from test_construction_01_project_read_http import walk_routes

PATH = '/api/v1/construction/client-user-link-requests/confirm'


@pytest.fixture
async def confirmation_http(pipeline, monkeypatch):
    p, c = pipeline, pipeline.c
    c.sync.execute(text("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")); c.sync.commit()
    p.platform_queries, p.opened_tenants = [], []
    class PlatformReader:
        no_autoflush = nullcontext()
        async def execute(self, statement):
            p.platform_queries.append(statement)
            return c.sync.execute(statement)
    p.platform = PlatformReader()
    c.identity = PlatformClientLinkConfirmationIdentityReader(p.platform)
    c.service.identity = c.identity
    # Keep JWT time independent of the host clock and of the domain clock
    # advanced by replay/expiry tests. Signature, issuer and expiry verification
    # still run through the real AccessTokenCodec and PyJWT implementation.
    auth_instant = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    class ConfirmationJwtDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(auth_instant.timestamp(), tz)
    monkeypatch.setattr(token_security, 'datetime', ConfirmationJwtDatetime)
    monkeypatch.setattr(pyjwt_impl, 'datetime', ConfirmationJwtDatetime)
    p.auth_now = ConfirmationJwtDatetime.now(timezone.utc)
    p.auth_codec = AccessTokenCodec(secret='test-confirmation-platform-secret-at-least-32-characters',
        issuer='confirmation-http', audience='platform', access_ttl_seconds=600)
    p.access_token = p.auth_codec.issue_access_token(user_id=c.target, tenant_id=c.foreign)
    p.rate = SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    p.suites, p.modules = SimpleNamespace(require_enabled=AsyncMock()), SimpleNamespace(require_enabled=AsyncMock())
    @asynccontextmanager
    async def scoped(*, tenant_id):
        p.opened_tenants.append(tenant_id)
        if not c.sync.in_transaction(): c.sync.begin()
        yield c.session
    monkeypatch.setattr(routes, 'construction_transaction', scoped)
    p.app = with_local_construction_rate_limit(create_app())
    p.app.dependency_overrides[get_access_token_codec] = lambda: p.auth_codec
    p.app.dependency_overrides[get_api_session] = lambda: p.platform
    p.app.dependency_overrides[routes.get_confirmation_crypto_settings] = lambda: SimpleNamespace(encryption_key=p.key)
    p.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: p.rate
    p.app.dependency_overrides[get_tenant_suite_access_service] = lambda: p.suites
    p.app.dependency_overrides[get_tenant_module_access_service] = lambda: p.modules
    def workflow(session=Depends(routes.get_confirmation_scoped_session)):
        assert session is c.session
        return p.workflow
    p.app.dependency_overrides[routes.get_confirmation_workflow] = workflow
    c.session.commit.reset_mock(); c.session.rollback.reset_mock()
    return p


async def post(p, body=None, *, key='confirm-http-key', authorization=None, params=None, headers=None):
    data = dict(recipient_context=p.request.recipient_context, token=p.request.token) if body is None else body
    request_headers = [('Authorization', 'Bearer '+p.access_token), ('X-Tenant-ID', str(uuid4())), ('X-Request-ID', 'confirm-http')]
    if authorization is not None:
        request_headers = [(name, value) for name, value in request_headers if name != 'Authorization']
        if authorization != 'absent': request_headers.append(('Authorization', authorization))
    if key is not None: request_headers.append(('Idempotency-Key', key))
    if headers: request_headers.extend(headers)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=p.app, raise_app_exceptions=False), base_url='http://testserver') as client:
        return await client.post(PATH, json=data, headers=request_headers, params=params)


@pytest.mark.asyncio
async def test_real_jwt_target_without_membership_confirms_in_one_existing_workflow_transaction(confirmation_http):
    p, c = confirmation_http, confirmation_http.c
    async def membership(**kw):
        assert kw == dict(user_id=c.operator, tenant_id=c.tenant)
        return True
    c.access.has_active_platform_membership.side_effect = membership
    before = snapshot(p)
    response = await post(p)
    assert response.status_code == 200, response.text
    body = response.json(); data = body['data']
    assert data == dict(request_id=str(c.request_id), tenant_id=str(c.tenant), client_id=str(c.client),
        linked_user_id=str(c.target), client_row_version=2, request_row_version=2,
        consumed_at=c.now.isoformat().replace('+00:00', 'Z'), client_changed=True)
    assert body['meta'] == {} and body['request_id'] == response.headers['X-Request-ID'] == 'confirm-http'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert p.opened_tenants == [c.tenant] and c.foreign not in p.opened_tenants
    call = c.service.confirm_request.await_args.kwargs
    assert call['actor'].user_id == c.target and call['actor'].tenant_id is None and call['actor'].roles == ()
    assert call['trace_id'] == 'confirm-http' and c.session.commit.await_count == 1
    after = snapshot(p)
    assert after[0][0].replace(tzinfo=timezone.utc) == c.now and after[0][1] == 2
    assert after[1] == (c.target, 2) and after[2] == before[2]+2
    assert p.request.token not in response.text and p.request.recipient_context not in response.text
    assert 'audit_id' not in response.text and 'token_hash' not in response.text and 'ciphertext' not in response.text
    p.rate.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.target)
    p.suites.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code='construction')
    p.modules.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code='construction.core')


@pytest.mark.asyncio
async def test_same_key_replays_after_expiry_and_later_unlink_without_second_mutation(confirmation_http):
    p, c = confirmation_http, confirmation_http.c
    first = await post(p)
    assert first.status_code == 200, first.text
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id == c.client).values(linked_user_id=None, row_version=3))
    c.sync.commit(); c.now = c.request.expires_at + timedelta(seconds=1)
    before = snapshot(p)
    replay = await post(p)
    assert replay.status_code == 200 and replay.json() == first.json() and snapshot(p) == before
    assert c.service.confirm_request.await_count == 1
    again = await post(p, key='another-key')
    assert again.status_code == 403 and snapshot(p) == before


@pytest.mark.asyncio
async def test_same_key_other_valid_context_has_idempotency_conflict(confirmation_http):
    p, c = confirmation_http, confirmation_http.c
    assert (await post(p)).status_code == 200
    other = replace(c.request, request_id=uuid4(), client_id=c.other_client)
    material = ConstructionClientLinkIssuanceResult(other, TOKEN, uuid4())
    envelope = p.context_codec.issue_for_request(material=material)
    before = snapshot(p)
    response = await post(p, dict(recipient_context=envelope.recipient_context, token=TOKEN))
    assert response.status_code == 409 and response.json()['error']['code'] == 'IDEMPOTENCY_KEY_REUSED'
    assert snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing_auth', 'invalid_token', 'inactive_target', 'wrong_target', 'bad_context', 'wrong_token',
    'suite', 'module', 'rate', 'missing_key', 'blank_key', 'long_key', 'duplicate_key', 'query_tenant'])
async def test_boundary_gates_deny_before_construction_transaction(confirmation_http, failure):
    p, c = confirmation_http, confirmation_http.c; options = {}; expected = 403
    if failure == 'missing_auth': options['authorization'] = 'absent'; expected = 401
    elif failure == 'invalid_token': options['authorization'] = 'Bearer private-token'; expected = 401
    elif failure == 'inactive_target':
        c.sync.execute(text("UPDATE users SET status='blocked' WHERE id=:id"), {'id': c.target.hex}); c.sync.commit(); expected = 401
    elif failure == 'wrong_target': p.access_token = p.auth_codec.issue_access_token(user_id=c.operator, tenant_id=c.tenant)
    elif failure == 'bad_context': options['body'] = dict(recipient_context='private-context', token=p.request.token)
    elif failure == 'wrong_token':
        token = p.request.token[:-1] + ('A' if p.request.token[-1] != 'A' else 'E')
        options['body'] = dict(recipient_context=p.request.recipient_context, token=token)
    elif failure == 'suite': p.suites.require_enabled.side_effect = SuiteDisabledError()
    elif failure == 'module': p.modules.require_enabled.side_effect = ModuleDisabledError()
    elif failure == 'rate': p.rate.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError(); expected = 429
    elif failure == 'missing_key': options['key'] = None; expected = 422
    elif failure == 'blank_key': options['key'] = ' '; expected = 422
    elif failure == 'long_key': options['key'] = 'x'*256; expected = 422
    elif failure == 'duplicate_key': options['headers'] = [('Idempotency-Key', 'second')]; expected = 422
    else: options['params'] = {'tenant_id': str(c.foreign)}; expected = 422
    before = snapshot(p)
    response = await post(p, **options)
    assert response.status_code == expected, response.text
    assert snapshot(p) == before and p.opened_tenants == []
    assert c.service.confirm_request.await_count == 0 and 'private-' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{}, {'token': 'bad'}, {'recipient_context': 'bad'},
    {'recipient_context': 'x'*4097, 'token': TOKEN}, {'recipient_context': 'bad', 'token': 'x'*49},
    {'recipient_context': 'bad', 'token': TOKEN, 'target_user_id': 'private-target'},
    {'recipient_context': 'bad', 'token': TOKEN, 'tenant_id': 'private-tenant'},
    {'recipient_context': 'bad', 'token': TOKEN, 'expected_row_version': 1},
    {'recipient_context': 'bad', 'token': TOKEN, 'confirmed': True}])
async def test_strict_approved_body_cannot_supply_target_tenant_or_client_state(confirmation_http, body):
    p = confirmation_http
    response = await post(p, body)
    assert response.status_code == 422 and p.opened_tenants == []
    assert 'private-target' not in response.text and 'private-tenant' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['expired', 'client_version', 'revoked_initiator', 'inactive_initiator', 'audit', 'complete', 'commit'])
async def test_domain_failures_and_real_sqlite_transaction_rollback(confirmation_http, failure):
    p, c = confirmation_http, confirmation_http.c; expected = 500
    if failure == 'expired': c.now = c.request.expires_at; expected = 403
    elif failure == 'client_version':
        c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id == c.client).values(row_version=2)); c.sync.commit(); expected = 409
    elif failure == 'revoked_initiator': c.grant.status = 'revoked'; expected = 403
    elif failure == 'inactive_initiator':
        c.sync.execute(text("UPDATE users SET status='blocked' WHERE id=:id"), {'id': c.operator.hex}); c.sync.commit(); expected = 403
    elif failure == 'audit': c.events.create_event.side_effect = RuntimeError('private audit failure')
    elif failure == 'complete': p.flags.fail_complete = True
    else: p.flags.fail_commit = True
    before = snapshot(p)
    response = await post(p)
    assert response.status_code == expected, response.text
    assert snapshot(p) == before and c.session.rollback.await_count >= 1
    assert 'private' not in response.text and 'data' not in response.json()
    if failure == 'client_version': assert response.json()['error']['code'] == 'ROW_VERSION_CONFLICT'


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['type', 'tenant', 'client', 'target', 'request', 'bool_version', 'request_version', 'audit'])
async def test_invalid_workflow_receipt_is_not_exposed(confirmation_http, bad):
    p, c = confirmation_http, confirmation_http.c
    value = ConstructionClientLinkConfirmationResult(c.request_id, c.tenant, c.client, c.target, 2, 2, c.now, True, uuid4(), uuid4())
    changes = {'tenant': {'tenant_id': uuid4()}, 'client': {'client_id': uuid4()}, 'target': {'linked_user_id': uuid4()},
        'request': {'request_id': uuid4()}, 'bool_version': {'client_row_version': True}, 'request_version': {'request_row_version': 1},
        'audit': {'client_audit_id': None}}
    value = {'private': 'private-result'} if bad == 'type' else replace(value, **changes[bad])
    p.workflow = SimpleNamespace(confirm_request=AsyncMock(return_value=value))
    response = await post(p)
    assert response.status_code == 500 and 'data' not in response.json() and 'private-result' not in response.text


def test_secret_request_repr_factory_and_exact_approved_http_contract(confirmation_http):
    p, c = confirmation_http, confirmation_http.c
    body = routes.ClientConfirmationRequest(recipient_context=p.request.recipient_context, token=p.request.token)
    assert p.request.token not in repr(body) and p.request.recipient_context not in repr(body)
    workflow = routes.get_confirmation_workflow(c.session, p.platform, SimpleNamespace(encryption_key=p.key))
    assert workflow.service.session is workflow.idempotency.repository.session is c.session
    assert workflow.service.identity.session is p.platform
    schema = p.app.openapi(); operation = schema['paths'][PATH]['post']
    matches = [r for path, r in walk_routes(p.app) if path == PATH and 'POST' in getattr(r, 'methods', ())]
    assert len(matches) == 1 and matches[0].endpoint is routes.confirm_client_link
    model = schema['components']['schemas']['ClientConfirmationRequest']
    assert set(model['properties']) == set(model['required']) == {'recipient_context', 'token'}
    assert model['additionalProperties'] is False
    key = next(parameter for parameter in operation['parameters'] if parameter['name'] == 'Idempotency-Key')
    assert key['required'] is True
    assert not {'tenant_id', 'target_user_id', 'client_id', 'expected_row_version'} & {item['name'] for item in operation['parameters']}
    assert {'401', '403', '409', '422', '429', '500', '503'} <= operation['responses'].keys()


def test_missing_crypto_config_fails_closed_without_default_lifetime(confirmation_http, monkeypatch):
    def missing(): raise ApiConfigurationError('private configuration')
    monkeypatch.setattr(routes.ApiIdempotencySettings, 'from_env', missing)
    with pytest.raises(ApiHttpError) as caught: routes.get_confirmation_crypto_settings()
    assert caught.value.status_code == 503 and 'private' not in caught.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['expired', 'future', 'signature', 'issuer', 'audience'])
async def test_fixed_jwt_clock_keeps_real_authentication_checks(confirmation_http, invalid):
    p, c = confirmation_http, confirmation_http.c
    payload = jwt.decode(p.access_token, p.auth_codec.secret, algorithms=['HS256'],
        audience=p.auth_codec.audience, issuer=p.auth_codec.issuer)
    now = int(p.auth_now.timestamp())
    key = p.auth_codec.secret
    if invalid == 'expired':
        payload.update(iat=now-600, nbf=now-600, exp=now)
    elif invalid == 'future':
        payload.update(iat=now+1, nbf=now+1, exp=now+601)
    elif invalid == 'signature':
        key = 'other-test-signature-key-at-least-32-characters'
    elif invalid == 'issuer':
        payload['iss'] = 'different-test-issuer'
    else:
        payload['aud'] = 'different-test-audience'
    p.access_token = jwt.encode(payload, key, algorithm='HS256')
    before = snapshot(p)
    response = await post(p)
    assert response.status_code == 401, response.text
    assert response.json()['error']['code'] == 'invalid_token'
    assert snapshot(p) == before and p.opened_tenants == []
    assert c.service.confirm_request.await_count == 0
