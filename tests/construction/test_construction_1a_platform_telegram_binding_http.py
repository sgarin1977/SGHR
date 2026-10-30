from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import Depends
import httpx
import pytest
from sqlalchemy import select

import api.routes.platform_telegram_binding as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from api.settings import ApiConfigurationError
from database.models import ApiIdempotencyRecord, EventLog, PlatformTelegramBindingToken as Token
from services.api_rate_limits import ApiRequestRateLimitExceededError
from test_construction_1a_platform_telegram_binding_consume import environment, proof
from test_construction_1a_platform_telegram_binding_workflow import workflow, counts, KEY
from test_construction_1a_platform_telegram_binding_lifecycle_workflow import lifecycle_workflow


PREFIX = '/api/v1/platform/integrations/telegram'


@pytest.fixture
def binding_http(lifecycle_workflow):
    c = lifecycle_workflow
    c.app = create_app()
    c.rates = SimpleNamespace(ensure_public_api_allowed=AsyncMock(), ensure_authenticated_user_allowed=AsyncMock())
    c.begins = 0
    async def begin():
        c.begins += 1
        c.env.session.begin()
    c.env.bridge.begin = begin
    def provide(session=Depends(routes.get_binding_session)):
        assert session is c.env.bridge
        return c.lifecycle
    c.app.dependency_overrides[get_api_session] = lambda: c.env.bridge
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[routes.get_binding_workflow] = provide
    c.app.dependency_overrides[routes.get_binding_bot_username] = lambda: 'SGHR_test_bot'
    return c


async def post(c, operation='initiate', *, key='http-key', body=None, headers=None, identifier=None):
    if operation == 'initiate': suffix, default = '/bindings', {}
    elif operation == 'consume': suffix, default = '/bindings/consume', {'token': c.env.material.token, 'init_data': proof()}
    else:
        suffix = '/bindings/'+str(identifier or c.env.binding_id)+'/'+operation
        default = {'expected_row_version': 1}
    pairs = [('X-Request-ID', 'binding-http-test')]
    if key is not None: pairs.append(('Idempotency-Key', key))
    pairs += list((headers or {}).items()) if isinstance(headers, dict) else list(headers or [])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url='http://testserver') as client:
        return await client.post(PREFIX+suffix, json=default if body is None else body, headers=pairs,
            params={'tenant_id': str(uuid4()), 'user_id': str(uuid4())})


@pytest.mark.asyncio
async def test_initiation_uses_current_platform_actor_and_returns_only_code_link_receipt(binding_http):
    c = binding_http
    response = await post(c)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['meta'] == {} and body['request_id'] == response.headers['X-Request-ID'] == 'binding-http-test'
    assert set(body['data']) == {'binding_id', 'code', 'deep_link', 'issued_at', 'expires_at', 'row_version'}
    data = body['data']
    assert data['row_version'] == 1 and data['deep_link'] == 'https://t.me/SGHR_test_bot?start=bind_'+data['code']
    row = c.env.session.get(Token, __import__('uuid').UUID(data['binding_id']))
    assert row.user_id == c.actor.user_id and row.tenant_id == c.actor.tenant_id
    assert counts(c) == (2, 0, 1, 1)
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.actor.tenant_id, user_id=c.actor.user_id)
    audit = c.env.session.execute(select(EventLog)).scalar_one()
    assert audit.trace_id.startswith('telegram-binding:') and audit.trace_id != 'binding-http-test'
    assert data['code'] not in audit.trace_id and data['code'] not in str(audit.payload)
    assert row.token_hash not in response.text
    assert response.headers['Cache-Control'] == 'no-store' and response.headers['Referrer-Policy'] == 'no-referrer'


@pytest.mark.asyncio
async def test_consume_has_no_platform_session_dependency_and_opens_its_transaction(binding_http):
    c = binding_http
    c.env.session.rollback()
    def forbidden(): raise AssertionError('Consume must not resolve a Platform actor.')
    c.app.dependency_overrides[get_current_actor] = forbidden
    response = await post(c, 'consume', headers={'Authorization': 'Bearer invalid-and-irrelevant'})
    assert response.status_code == 200, response.text
    assert c.begins == 1
    data = response.json()['data']
    assert set(data) == {'binding_id', 'account_id', 'consumed_at', 'row_version', 'account_created'}
    assert data['row_version'] == 2 and data['account_created'] is True
    assert 'user_id' not in data and 'tenant_id' not in data
    assert c.env.material.token not in response.text and 'init_data' not in response.text
    c.rates.ensure_public_api_allowed.assert_awaited_once_with(client_ip='127.0.0.1')
    c.rates.ensure_authenticated_user_allowed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['initiate', 'consume', 'resend', 'revoke'])
async def test_http_keyed_replay_returns_same_response_without_second_business_write(binding_http, operation):
    c = binding_http
    first = await post(c, operation)
    assert first.status_code == (201 if operation == 'initiate' else 200), first.text
    before = counts(c)
    repeated = await post(c, operation)
    assert repeated.status_code == first.status_code and repeated.json() == first.json()
    assert counts(c) == before and c.commits == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['initiate', 'resend', 'revoke'])
async def test_anonymous_caller_cannot_run_session_authorized_operations(binding_http, operation):
    c = binding_http
    c.app.dependency_overrides.pop(get_current_actor)
    response = await post(c, operation)
    assert response.status_code == 401, response.text
    assert response.headers['WWW-Authenticate'] == 'Bearer'
    assert counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['initiate', 'consume', 'resend'])
@pytest.mark.parametrize('key', [None, '', ' ', 'k'*256])
async def test_required_idempotency_key_is_checked_before_business_writes(binding_http, operation, key):
    c = binding_http
    response = await post(c, operation, key=key)
    assert response.status_code == 422, response.text
    assert counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['initiate', 'consume', 'resend', 'revoke'])
async def test_duplicate_idempotency_headers_are_rejected(binding_http, operation):
    c = binding_http
    response = await post(c, operation, headers=[('Idempotency-Key', 'second')])
    assert response.status_code == 422 and counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
async def test_revoke_without_key_remains_available_with_expected_version(binding_http):
    c = binding_http
    response = await post(c, 'revoke', key=None)
    assert response.status_code == 200, response.text
    assert set(response.json()['data']) == {'binding_id', 'revoked_at', 'row_version'}
    assert counts(c) == (1, 0, 1, 0)
    repeated = await post(c, 'revoke', key=None)
    assert repeated.status_code == 409 and repeated.json()['error']['code'] == 'ROW_VERSION_CONFLICT'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['initiate', 'consume', 'resend', 'revoke'])
@pytest.mark.parametrize('field', ['user_id', 'tenant_id', 'telegram_user_id'])
async def test_identity_context_is_not_accepted_in_request_body(binding_http, operation, field):
    c = binding_http
    body = {} if operation == 'initiate' else {'token': c.env.material.token, 'init_data': proof()} if operation == 'consume' else {'expected_row_version': 1}
    body[field] = 'private-spoofed-context'
    response = await post(c, operation, body=body)
    assert response.status_code == 422
    assert 'private-spoofed-context' not in response.text and c.env.material.token not in response.text
    assert counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
@pytest.mark.parametrize('version', [None, 0, True, '1', 1.0, 2**63-1])
async def test_mutations_require_strict_expected_version(binding_http, operation, version):
    c = binding_http
    response = await post(c, operation, body={'expected_row_version': version})
    assert response.status_code == 422 and counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
async def test_bad_signature_and_unknown_token_are_safe_errors_without_side_effects(binding_http):
    c = binding_http
    bad = await post(c, 'consume', body={'token': c.env.material.token, 'init_data': 'private-forged-proof'})
    assert bad.status_code == 401 and bad.json()['error']['code'] == 'invalid_telegram_auth'
    assert 'private-forged-proof' not in bad.text and c.env.material.token not in bad.text
    missing = await post(c, 'consume', body={'token': 'A'*43, 'init_data': proof()})
    assert missing.status_code == 400 and missing.json()['error']['code'] == 'invalid_binding_token'
    assert counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
async def test_identity_conflict_is_machine_readable_and_does_not_consume_challenge(binding_http):
    c = binding_http
    await c.env.service.accounts.create_or_get_account(user_id=c.env.other, telegram_user_id=123456789)
    c.env.session.commit(); c.env.session.begin()
    response = await post(c, 'consume')
    assert response.status_code == 409 and response.json()['error']['code'] == 'TELEGRAM_ACCOUNT_ALREADY_LINKED'
    assert counts(c) == (1, 1, 0, 0)


@pytest.mark.asyncio
async def test_changed_verified_consumer_with_same_key_is_idempotency_conflict(binding_http):
    c = binding_http
    assert (await post(c, 'consume')).status_code == 200
    response = await post(c, 'consume', body={'token': c.env.material.token, 'init_data': proof(telegram_id=987654)})
    assert response.status_code == 409 and response.json()['error']['code'] == 'IDEMPOTENCY_KEY_REUSED'
    assert counts(c) == (1, 1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['initiate', 'consume'])
async def test_existing_platform_rate_limits_stop_writes_and_ignore_forwarded_identity(binding_http, operation):
    c = binding_http
    target = c.rates.ensure_public_api_allowed if operation == 'consume' else c.rates.ensure_authenticated_user_allowed
    target.side_effect = ApiRequestRateLimitExceededError()
    response = await post(c, operation, headers={'X-Forwarded-For': '8.8.8.8'})
    assert response.status_code == 429 and response.json()['error']['code'] == 'RATE_LIMIT_EXCEEDED'
    assert counts(c) == (1, 0, 0, 0)
    if operation == 'consume': target.assert_awaited_once_with(client_ip='127.0.0.1')


@pytest.mark.asyncio
async def test_invalid_response_context_is_500_and_never_exposes_secret(binding_http):
    c = binding_http
    issued = await c.workflow.initiate_binding(actor=c.actor, idempotency_key='seed')
    c.workflow.initiate_binding = AsyncMock(return_value=replace(issued, tenant_id=uuid4()))
    response = await post(c)
    assert response.status_code == 500 and response.json()['error']['code'] == 'internal_error'
    assert issued.token not in response.text and 'deep_link' not in response.text


def test_configuration_dependency_uses_shared_platform_key_and_server_bot_target(binding_http, monkeypatch):
    c = binding_http
    monkeypatch.setattr(routes, 'load_dotenv', lambda: None)
    monkeypatch.setenv('BOT_USERNAME', 'Configured_test_bot')
    assert routes.get_binding_bot_username() == 'Configured_test_bot'
    monkeypatch.setenv('BOT_USERNAME', 'https://private.invalid/bot')
    with pytest.raises(routes.ApiHttpError) as error: routes.get_binding_bot_username()
    assert error.value.status_code == 503 and 'private.invalid' not in str(error.value)
    monkeypatch.setattr(routes.ApiIdempotencySettings, 'from_env', lambda: SimpleNamespace(encryption_key=KEY))
    built = routes.get_binding_workflow(c.env.bridge, c.env.service.verifier)
    assert built.session is built.base.session is built.service.session is c.env.bridge
    def missing(): raise ApiConfigurationError('private-configuration-value')
    monkeypatch.setattr(routes.ApiIdempotencySettings, 'from_env', missing)
    with pytest.raises(routes.ApiHttpError) as error: routes.get_binding_workflow(c.env.bridge, c.env.service.verifier)
    assert error.value.status_code == 503 and 'private-configuration-value' not in str(error.value)


def test_openapi_has_approved_platform_operations_and_no_actor_dependency_for_consume(binding_http):
    from api.platform_principal import get_authenticated_platform_principal
    from api.routes import platform_telegram_account_state as account_state

    c = binding_http
    paths = c.app.openapi()['paths']
    posts = {
        PREFIX+'/bindings', PREFIX+'/bindings/consume',
        PREFIX+'/bindings/{id}/resend', PREFIX+'/bindings/{id}/revoke',
        PREFIX+'/accounts/{id}/unlink',
    }
    account_read = PREFIX+'/accounts'
    selected = {path for path in paths if path.startswith(PREFIX)}
    # Keep an exact allowlist; the own-account GET is independent of the five
    # existing mutations and must never be treated as an idempotent POST.
    assert selected == posts | {account_read}
    assert all(set(paths[path]) == {'post'} for path in posts)
    assert set(paths[account_read]) == {'get'}
    assert paths[PREFIX+'/bindings']['post']['responses']['201']
    for path in posts:
        parameter = next(item for item in paths[path]['post']['parameters'] if item['name'] == 'Idempotency-Key')
        assert parameter['required'] is (not path.endswith('/revoke'))
    assert not any(parameter['name'] == 'Idempotency-Key'
        for parameter in paths[account_read]['get'].get('parameters', []))

    def calls(dependant):
        yield dependant.call
        for child in dependant.dependencies:
            yield from calls(child)
    for route in routes.router.routes:
        dependencies = list(calls(route.dependant))
        if route.path.endswith('/consume'):
            assert get_current_actor not in dependencies
            assert routes.get_binding_verifier in dependencies
        else:
            assert get_current_actor in dependencies
    own_read = next(route for route in account_state.router.routes if route.path.endswith('/accounts'))
    dependencies = list(calls(own_read.dependant))
    assert get_authenticated_platform_principal in dependencies
    assert get_current_actor not in dependencies

    models = c.app.openapi()['components']['schemas']
    assert models['BindingConsumeRequest']['additionalProperties'] is False
    assert set(models['BindingConsumeRequest']['properties']) == {'token', 'init_data'}
