from construction_rate_limit_fixture import with_local_construction_rate_limit
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import insert, select, text

import api.routes.construction_client_self_link as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_session, get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from api.settings import ApiConfigurationError
from database.models import ConstructionClient
from services.api_identity import ApiIdentityAccessError
from services.construction_client_self_link import ConstructionClientSelfLinkResult
from services.construction_client_self_link_workflow import ConstructionClientSelfLinkWorkflow, build_construction_client_self_link_workflow
from services.construction_clients import ConstructionClientOperationError
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_client_self_link_workflow import self_pipeline, change_client, snapshot
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db
from test_construction_01_project_read_http import walk_routes


def supplied(value):
    return lambda: value


@pytest.fixture
async def self_http(self_pipeline):
    p, c = self_pipeline, self_pipeline.c
    c.foreign_client = uuid4()
    c.sync.execute(insert(ConstructionClient.__table__).values(id=c.foreign_client, tenant_id=c.foreign,
        display_name='Foreign client', client_type='person'))
    c.sync.commit()
    p.keyed_workflow = p.workflow
    p.workflow = ConstructionClientSelfLinkWorkflow(session=c.session, self_link_service=p.service,
        idempotency_service=None, now_provider=lambda: c.now)
    p.suite, p.module = SimpleNamespace(require_enabled=AsyncMock()), SimpleNamespace(require_enabled=AsyncMock())
    p.rate = SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    p.app = with_local_construction_rate_limit(create_app())
    for dependency, value in ((get_current_actor, c.self_actor), (get_construction_access_context, c.self_context),
        (get_construction_scoped_session, c.session), (get_api_session, p.identity.session),
        (get_tenant_suite_access_service, p.suite), (get_tenant_module_access_service, p.module),
        (get_api_request_rate_limit_service, p.rate)):
        p.app.dependency_overrides[dependency] = supplied(value)
    p.app.dependency_overrides[routes.get_client_self_link_workflow] = lambda: p.workflow
    return p


async def post(p, body=None, *, identifier=None, key=None, params=None, headers=None):
    c = p.c
    if not c.sync.in_transaction(): c.sync.begin()
    request_headers = [('X-Request-ID', 'self-link-http'), ('X-Tenant-ID', str(c.foreign))]
    if key is not None: request_headers.append(('Idempotency-Key', key))
    if headers: request_headers.extend(headers)
    path = '/api/v1/construction/clients/' + (identifier or str(c.client)) + '/user-link/self'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=p.app, raise_app_exceptions=False), base_url='http://testserver') as client:
        return await client.post(path, json={'expected_row_version': 1} if body is None else body,
            headers=request_headers, params=params)


def current(p):
    c = p.c
    return c.sync.execute(select(ConstructionClient.linked_user_id, ConstructionClient.row_version)
        .where(ConstructionClient.id == c.client)).one()


@pytest.mark.asyncio
async def test_no_key_http_uses_real_sqlite_workflow_one_audit_and_trusted_target(self_http):
    p, c = self_http, self_http.c
    before_users = c.sync.execute(text('SELECT * FROM users ORDER BY id')).all()
    response = await post(p)
    assert response.status_code == 200, response.text
    assert response.json() == dict(data=dict(client_id=str(c.client), tenant_id=str(c.tenant), linked_user_id=str(c.operator),
        row_version=2, changed=True), meta={}, request_id='self-link-http')
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert current(p) == (c.operator, 2)
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one() == 1
    assert c.sync.execute(text('SELECT * FROM users ORDER BY id')).all() == before_users
    assert p.ledger.calls == 0 and p.ledger.all() == []
    assert c.audit_rows[-1].trace_id == 'self-link-http' and c.audit_rows[-1].payload['related_client_id'] == str(c.client)
    assert 'audit_id' not in response.text and 'token' not in response.text
    c.session.commit.assert_awaited_once()
    p.rate.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.operator)
    p.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code='construction')
    p.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code='construction.core')


@pytest.mark.asyncio
async def test_no_key_repeat_is_noop_at_current_version_but_stale_version_conflicts(self_http):
    p = self_http
    assert (await post(p)).status_code == 200
    before = snapshot(p)
    stale = await post(p)
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'ROW_VERSION_CONFLICT'
    noop = await post(p, {'expected_row_version': 2})
    assert noop.status_code == 200 and noop.json()['data']['changed'] is False and noop.json()['data']['row_version'] == 2
    assert snapshot(p) == before and p.ledger.calls == 0


@pytest.mark.asyncio
async def test_explicit_replacement_checks_previous_identity_and_records_it(self_http):
    p, c = self_http, self_http.c
    change_client(p, linked_user_id=c.target)
    before = snapshot(p)
    missing = await post(p)
    assert missing.status_code == 422 and snapshot(p) == before
    wrong = await post(p, {'expected_row_version': 1, 'previous_user_id': str(c.foreign_user)})
    assert wrong.status_code == 409 and wrong.json()['error']['code'] == 'ROW_VERSION_CONFLICT'
    assert snapshot(p) == before
    response = await post(p, {'expected_row_version': 1, 'previous_user_id': str(c.target)})
    assert response.status_code == 200 and current(p) == (c.operator, 2)
    assert p.service.replace_with_self.await_count == 2
    assert c.audit_rows[-1].payload['identity_link_action'] == 'replace_with_self'
    assert c.audit_rows[-1].payload['changes'][0]['old_value'] == str(c.target)


@pytest.mark.asyncio
async def test_optional_key_replays_original_result_and_conflicting_payload_returns_409(self_http):
    p, c = self_http, self_http.c; p.workflow = p.keyed_workflow
    first = await post(p, key='optional-self-key')
    assert first.status_code == 200, first.text
    change_client(p, linked_user_id=c.target, row_version=3)
    before = snapshot(p)
    replay = await post(p, key='optional-self-key')
    assert replay.status_code == 200 and replay.json() == first.json() and snapshot(p) == before
    assert p.service.link_self.await_count == 1
    reused = await post(p, {'expected_row_version': 2}, key='optional-self-key')
    assert reused.status_code == 409 and reused.json()['error']['code'] == 'IDEMPOTENCY_KEY_REUSED'
    assert snapshot(p) == before and 'optional-self-key' not in replay.text


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['anonymous', 'inactive', 'permission', 'wrong_tenant', 'wrong_user', 'project_only',
    'suite', 'module', 'rate', 'identity', 'membership', 'revoked'])
async def test_authentication_entitlements_scope_and_fresh_identity_deny_before_link(self_http, case):
    p, c = self_http, self_http.c; before = snapshot(p); expected = 403
    if case == 'anonymous': p.app.dependency_overrides.pop(get_current_actor); expected = 401
    elif case == 'inactive': p.app.dependency_overrides[get_current_actor] = supplied(replace(c.self_actor, status='blocked'))
    elif case in ('permission', 'wrong_tenant', 'wrong_user'):
        changes = {'permission': {'permissions': ()}, 'wrong_tenant': {'tenant_id': uuid4()}, 'wrong_user': {'user_id': uuid4()}}[case]
        p.app.dependency_overrides[get_construction_access_context] = supplied(replace(c.self_context, **changes))
    elif case == 'project_only':
        context = ConstructionAccessContext(c.operator, c.tenant, ('PROJECT_MANAGER',), ('construction.projects.edit',),
            (ConstructionGrantContext('PROJECT_MANAGER', 'project', uuid4()),))
        p.app.dependency_overrides[get_construction_access_context] = supplied(context)
    elif case == 'suite': p.suite.require_enabled.side_effect = SuiteDisabledError()
    elif case == 'module': p.module.require_enabled.side_effect = ModuleDisabledError()
    elif case == 'rate': p.rate.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError(); expected = 429
    elif case == 'identity': p.identity.require_active_actor.side_effect = ApiIdentityAccessError('private identity')
    elif case == 'membership': c.access.has_active_platform_membership.return_value = False
    else: c.grant.status = 'revoked'
    response = await post(p)
    assert response.status_code == expected, response.text
    assert snapshot(p) == before and p.service.link_self.await_count == 0
    assert 'private identity' not in response.text and 'data' not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{}, {'expected_row_version': True}, {'expected_row_version': 0}, {'expected_row_version': 2**63},
    {'expected_row_version': '1'}, {'expected_row_version': 1.0}, {'expected_row_version': 1, 'previous_user_id': 'bad'},
    {'expected_row_version': 1, 'target_user_id': 'private-user'}, {'expected_row_version': 1, 'tenant_id': 'private-tenant'},
    {'expected_row_version': 1, 'confirmed': True}, {'expected_row_version': 1, 'linked_user_id': 'private-user'}])
async def test_invalid_or_caller_identity_body_cannot_reach_workflow(self_http, body):
    p = self_http; before = snapshot(p)
    response = await post(p, body)
    assert response.status_code == 422 and snapshot(p) == before
    assert p.service.link_self.await_count == 0 and p.service.replace_with_self.await_count == 0
    assert 'private-user' not in response.text and 'private-tenant' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['bad_path', 'query', 'blank_key', 'long_key', 'repeated_key', 'absent', 'foreign', 'archived'])
async def test_invalid_boundary_and_unavailable_client_do_not_mutate(self_http, case):
    p, c = self_http, self_http.c
    if case == 'archived': change_client(p, deleted_at=c.now)
    before = snapshot(p); options = {}; expected = 422
    if case == 'bad_path': options['identifier'] = 'bad'
    elif case == 'query': options['params'] = {'tenant_id': str(c.foreign)}
    elif case == 'blank_key': options['key'] = ' '
    elif case == 'long_key': options['key'] = 'x'*256
    elif case == 'repeated_key': options['headers'] = [('Idempotency-Key', 'one'), ('Idempotency-Key', 'two')]
    else:
        expected = 404
        if case == 'absent': options['identifier'] = str(uuid4())
        elif case == 'foreign': options['identifier'] = str(c.foreign_client)
    response = await post(p, **options)
    assert response.status_code == expected, response.text
    assert snapshot(p) == before and 'data' not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['audit', 'commit', 'completion'])
async def test_real_client_and_audit_write_roll_back_on_mandatory_failure(self_http, failure):
    p, c = self_http, self_http.c; before = snapshot(p)
    if failure == 'audit': c.events.create_event.side_effect = RuntimeError('private audit failure')
    elif failure == 'commit': p.flags.fail_commit = True
    else: p.flags.fail_complete = True; p.workflow = p.keyed_workflow
    response = await post(p, key='key' if failure == 'completion' else None)
    assert response.status_code == 500 and snapshot(p) == before
    assert 'private' not in response.text and 'data' not in response.json()
    assert c.session.rollback.await_count >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['type', 'tenant', 'client', 'target', 'version', 'bool_version', 'changed', 'missing_audit', 'noop_audit'])
async def test_invalid_workflow_receipt_is_sanitized(self_http, bad):
    p, c = self_http, self_http.c
    result = ConstructionClientSelfLinkResult(c.client, c.tenant, c.operator, 2, True, uuid4())
    changes = {'tenant': {'tenant_id': uuid4()}, 'client': {'client_id': uuid4()}, 'target': {'linked_user_id': uuid4()},
        'version': {'row_version': 3}, 'bool_version': {'row_version': True}, 'changed': {'changed': 1},
        'missing_audit': {'audit_id': None}, 'noop_audit': {'changed': False, 'row_version': 1}}
    result = {'private': 'private value'} if bad == 'type' else replace(result, **changes[bad])
    p.workflow = SimpleNamespace(link_self=AsyncMock(return_value=result))
    response = await post(p)
    assert response.status_code == 500 and 'private value' not in response.text and 'data' not in response.json()


def test_factory_without_key_does_not_load_encryption_settings_and_uses_separate_platform_session(self_http, monkeypatch):
    p, c = self_http, self_http.c
    def unavailable(): raise AssertionError('No replay configuration should be read without a key.')
    monkeypatch.setattr(routes.ApiIdempotencySettings, 'from_env', unavailable)
    platform = SimpleNamespace(execute=AsyncMock(), no_autoflush=nullcontext())
    workflow = routes.get_client_self_link_workflow(c.session, platform, None)
    assert workflow.idempotency is None and workflow.service.identity.session is platform
    assert workflow.service.session is workflow.service.repository.session is workflow.service.access.session is c.session


def test_factory_with_key_requires_valid_server_configuration(self_http, monkeypatch):
    p, c = self_http, self_http.c
    platform = SimpleNamespace(execute=AsyncMock(), no_autoflush=nullcontext())
    monkeypatch.setattr(routes.ApiIdempotencySettings, 'from_env', lambda: SimpleNamespace(encryption_key=p.key))
    assert routes.get_client_self_link_workflow(c.session, platform, 'key').idempotency.repository.session is c.session
    def unavailable(): raise ApiConfigurationError('private key setting')
    monkeypatch.setattr(routes.ApiIdempotencySettings, 'from_env', unavailable)
    with pytest.raises(ApiHttpError) as caught: routes.get_client_self_link_workflow(c.session, platform, 'key')
    assert caught.value.status_code == 503 and 'private' not in caught.value.message


@pytest.mark.asyncio
async def test_programmatic_key_without_configured_replay_fails_before_link(self_http):
    p = self_http; before = snapshot(p)
    with pytest.raises(ConstructionClientOperationError):
        await p.workflow.link_self(actor=p.c.self_actor, access_context=p.c.self_context,
            client_id=p.c.client, expected_row_version=1, idempotency_key='key')
    assert snapshot(p) == before and p.service.link_self.await_count == 0


def test_exact_approved_route_and_openapi_contract(self_http):
    p = self_http
    path = '/api/v1/construction/clients/{id}/user-link/self'
    matches = [r for item, r in walk_routes(p.app) if item == path and 'POST' in getattr(r, 'methods', ())]
    assert len(matches) == 1 and matches[0].endpoint is routes.link_client_to_self
    schema = p.app.openapi(); operation = schema['paths'][path]['post']
    body = schema['components']['schemas']['ClientSelfLinkRequest']
    assert set(body['properties']) == {'expected_row_version', 'previous_user_id'} and body['required'] == ['expected_row_version']
    assert body['additionalProperties'] is False
    key = next(p for p in operation['parameters'] if p['name'] == 'Idempotency-Key')
    assert key['required'] is False and not {'tenant_id', 'target_user_id', 'user_id'} & {p['name'] for p in operation['parameters']}
    assert {'401', '403', '404', '409', '422', '429', '500', '503'} <= operation['responses'].keys()
    assert routes.edit_permission.construction_permission_code == 'construction.projects.edit'
    assert 'get' in schema['paths']['/api/v1/construction/clients/{id}']
