from construction_rate_limit_fixture import with_local_construction_rate_limit
"""ASGI + real local SQLite mutations; native RLS is a separate acceptance."""
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text, update

import api.routes.construction_client_unlink as routes
from api.app import create_app
from api.auth import get_access_token_codec, get_api_identity_service, require_bearer_token
from api.dependencies import (get_api_session, get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service)
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.security import AccessTokenClaims, ApiAuthenticationError
from database.construction_session import ConstructionDatabaseConfigurationError
from database.models import ConstructionClient
from services.api_identity import ApiIdentityAccessError
from services.api_platform_principal import ApiPlatformPrincipal
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction_client_unlink import ConstructionClientUnlinkResult
from services.construction_client_unlink_workflow import ConstructionClientUnlinkWorkflow
from services.construction_permissions import ConstructionPermissionDeniedError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_client_own_link_context import own_link
from test_construction_1a_client_unlink_workflow import unlink_pipeline, snapshot
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db


def supplied(value): return lambda: value


@pytest.fixture
async def unlink_http(own_link, monkeypatch):
    p, c = own_link, own_link.c
    p.timeline, p.read_kwargs, p.write_kwargs = [], [], []
    p.token_tenant = c.foreign  # A selected tenant is not ownership authority.
    p.workflow = ConstructionClientUnlinkWorkflow(session=c.session, unlink_service=p.service,
        idempotency_service=None, now_provider=lambda: c.now)
    p.ledger.calls = 0  # The earlier fixture's confirm already used its own receipt.
    p.identity = SimpleNamespace(require_actor=AsyncMock(side_effect=ApiIdentityAccessError('no membership')))
    p.rate = SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    p.suite, p.module = SimpleNamespace(require_enabled=AsyncMock()), SimpleNamespace(require_enabled=AsyncMock())
    p.after_lookup = None

    @asynccontextmanager
    async def lookup(**kw):
        p.read_kwargs.append(kw); p.timeline.append('read-enter')
        if not c.sync.in_transaction(): c.sync.begin()
        p.settings.update(transaction_read_only='on', **{'app.current_tenant_id': '',
            'app.current_invitation_id': '', 'app.current_invitation_recipient_id': '',
            'app.client_unlink_lookup_user_id': str(kw['user_id']), 'app.client_unlink_lookup_client_id': str(kw['client_id'])})
        try: yield c.session
        finally:
            c.sync.rollback(); p.settings.clear(); p.timeline.append('read-exit')
            if p.after_lookup: p.after_lookup()

    @asynccontextmanager
    async def writing(**kw):
        assert p.timeline[-1] == 'read-exit'
        p.write_kwargs.append(kw); p.timeline.append('write-enter')
        if not c.sync.in_transaction(): c.sync.begin()
        try: yield c.session
        except BaseException:
            c.sync.rollback(); raise
        finally:
            if c.sync.in_transaction(): c.sync.rollback()
            p.timeline.append('write-exit')

    def own_factory(**kw):
        assert kw == dict(session=c.session, platform_session=p.platform)
        return p.own_service

    def workflow_factory(**kw):
        assert kw == dict(session=c.session, platform_session=p.platform)
        return p.workflow

    monkeypatch.setattr(routes, 'construction_client_unlink_lookup_transaction', lookup)
    monkeypatch.setattr(routes, 'construction_transaction', writing)
    monkeypatch.setattr(routes, 'build_construction_client_own_link_context_service', own_factory)
    monkeypatch.setattr(routes, 'build_construction_client_unlink_workflow', workflow_factory)
    p.resolver = SimpleNamespace(resolve=AsyncMock(side_effect=lambda **kw: c.editor_context))
    monkeypatch.setattr(routes, 'build_construction_access_context_resolver', lambda session: p.resolver)
    p.claims = lambda: AccessTokenClaims(p.principal.user_id, p.token_tenant, uuid4(), c.now, c.now)
    p.codec = SimpleNamespace(decode_access_token=lambda token: p.claims())
    p.app = with_local_construction_rate_limit(create_app())
    for dependency, value in ((get_api_session, p.platform), (get_api_identity_service, p.identity),
            (get_api_request_rate_limit_service, p.rate), (get_tenant_suite_access_service, p.suite),
            (get_tenant_module_access_service, p.module), (get_access_token_codec, p.codec)):
        p.app.dependency_overrides[dependency] = supplied(value)
    p.app.dependency_overrides[get_authenticated_platform_principal] = lambda: p.principal
    p.app.dependency_overrides[require_bearer_token] = lambda: 'verified-token-fixture'
    return p


def editor(p):
    c = p.c
    p.principal, p.token_tenant = ApiPlatformPrincipal(c.operator), c.tenant
    p.identity.require_actor.side_effect = None
    p.identity.require_actor.return_value = c.editor
    c.access.has_active_platform_membership.side_effect = None
    c.access.has_active_platform_membership.return_value = True
    c.access.list_active_grants.side_effect = lambda **kw: [c.grant]


async def post(p, body=None, *, identifier=None, params=None, headers=None):
    path = '/api/v1/construction/clients/' + (str(p.c.client) if identifier is None else identifier) + '/user-link/unlink'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=p.app, raise_app_exceptions=False), base_url='http://testserver') as client:
        return await client.post(path, json={'expected_row_version': 2} if body is None else body,
            params=params, headers={'X-Request-ID': 'client-unlink-http', 'X-Tenant-ID': str(p.c.foreign), **(headers or {})})


def current(p):
    return p.c.sync.execute(select(ConstructionClient.linked_user_id, ConstructionClient.row_version)
        .where(ConstructionClient.id == p.c.client)).one()


@pytest.mark.asyncio
async def test_owner_without_membership_unlinks_with_exact_audit_using_separate_lookup_then_write(unlink_http):
    p, c = unlink_http, unlink_http.c
    users_before = c.sync.execute(text('SELECT * FROM users ORDER BY id')).all()
    response = await post(p)
    assert response.status_code == 200, response.text
    assert response.json() == dict(data=dict(client_id=str(c.client), linked_user_id=None, row_version=3, changed=True),
        meta={}, request_id='client-unlink-http')
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert current(p) == (None, 3)
    assert p.read_kwargs == [dict(user_id=c.target, client_id=c.client)]
    assert p.write_kwargs == [dict(tenant_id=c.tenant)]
    assert p.timeline == ['read-enter', 'read-exit', 'write-enter', 'write-exit']
    p.identity.require_actor.assert_not_called(); p.suite.require_enabled.assert_not_called()
    p.module.require_enabled.assert_not_called(); p.resolver.resolve.assert_not_called()
    c.access.has_active_platform_membership.assert_not_called(); c.access.list_active_grants.assert_not_called()
    p.rate.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.foreign, user_id=c.target)
    assert c.sync.execute(text('SELECT * FROM users ORDER BY id')).all() == users_before
    assert c.audit_rows[-1].payload['related_client_id'] == str(c.client)
    assert c.audit_rows[-1].payload['authorization_method'] == 'authenticated_link_owner'
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one() == 3
    assert 'tenant_id' not in response.text and 'previous_linked_user_id' not in response.text and 'audit_id' not in response.text
    assert p.ledger.calls == 0
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_editor_unlinks_with_normal_membership_scope_modules_and_correlated_audit(unlink_http):
    p, c = unlink_http, unlink_http.c; editor(p)
    response = await post(p)
    assert response.status_code == 200, response.text
    assert current(p) == (None, 3)
    p.identity.require_actor.assert_awaited_once_with(user_id=c.operator, tenant_id=c.tenant)
    p.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code='construction')
    p.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code='construction.core')
    assert p.resolver.resolve.await_count == 1
    assert c.audit_rows[-1].payload['identity_link_action'] == 'editor_unlink'
    assert c.access.has_active_platform_membership.await_count > 0 and c.access.list_active_grants.await_count > 0


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['owner', 'editor'])
async def test_stale_version_is_409_without_writes(unlink_http, mode):
    p = unlink_http
    if mode == 'editor': editor(p)
    before = snapshot(p)
    response = await post(p, {'expected_row_version': 1})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'ROW_VERSION_CONFLICT' and snapshot(p) == before


@pytest.mark.asyncio
async def test_editor_current_version_noop_does_not_write_audit_but_stale_version_still_conflicts(unlink_http):
    p, c = unlink_http, unlink_http.c; editor(p)
    assert (await post(p)).status_code == 200
    before = snapshot(p)
    response = await post(p, {'expected_row_version': 3})
    assert response.status_code == 200 and response.json()['data']['changed'] is False
    assert response.json()['data']['row_version'] == 3 and snapshot(p) == before
    assert (await post(p)).status_code == 409 and snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['missing', 'foreign_owner', 'archived', 'null', 'already_unlinked'])
async def test_owner_missing_foreign_archived_and_already_unlinked_are_identical_safe_404(unlink_http, target):
    p, c = unlink_http, unlink_http.c
    identifier = None
    if target == 'missing': identifier = str(uuid4())
    elif target == 'already_unlinked': assert (await post(p)).status_code == 200
    else:
        values = {'linked_user_id': c.foreign_user} if target == 'foreign_owner' else (
            {'deleted_at': c.now} if target == 'archived' else {'linked_user_id': None})
        c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id == c.client).values(**values)); c.sync.commit()
    before = snapshot(p); p.timeline.clear(); p.write_kwargs.clear()
    response = await post(p, identifier=identifier)
    assert response.status_code == 404, response.text
    assert response.json() == dict(error=dict(code='not_found', message='Client link is not available.', request_id='client-unlink-http'))
    assert p.write_kwargs == [] and snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['membership', 'revoked', 'project_scope', 'wrong_context', 'actor', 'suite', 'module'])
async def test_editor_fresh_access_denial_prevents_unlink(unlink_http, denial):
    p, c = unlink_http, unlink_http.c; editor(p); expected = 403
    if denial == 'membership': c.access.has_active_platform_membership.return_value = False
    elif denial == 'revoked': c.grant.status = 'revoked'
    elif denial == 'project_scope': c.grant.scope_type, c.grant.scope_id = 'project', uuid4()
    elif denial == 'wrong_context': p.resolver.resolve.side_effect = lambda **kw: replace(c.editor_context, user_id=c.target)
    elif denial == 'actor': p.identity.require_actor.return_value = replace(c.editor, tenant_id=c.foreign); expected = 404
    elif denial == 'suite': p.suite.require_enabled.side_effect = SuiteDisabledError()
    else: p.module.require_enabled.side_effect = ModuleDisabledError()
    before = snapshot(p)
    response = await post(p)
    assert response.status_code == expected, response.text
    assert snapshot(p) == before and c.session.commit.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['audit', 'commit', 'inactive_after_lookup', 'ownership_race', 'version_race'])
async def test_real_sqlite_failure_or_race_rolls_back_unlink_and_audit(unlink_http, failure):
    p, c = unlink_http, unlink_http.c; expected = 500
    if failure == 'audit': c.events.create_event.side_effect = RuntimeError('private audit detail')
    elif failure == 'commit': p.flags.fail_commit = True
    elif failure == 'inactive_after_lookup':
        p.after_lookup = lambda: setattr(c.identity.require_active_user, 'side_effect', ApiIdentityAccessError('private status')); expected = 403
    else:
        values = {'row_version': 3}
        if failure == 'ownership_race': values['linked_user_id'] = c.foreign_user; expected = 404
        else: expected = 409
        def race():
            c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id == c.client).values(**values)); c.sync.commit()
        p.after_lookup = race
    users_before = c.sync.execute(text('SELECT * FROM users ORDER BY id')).all()
    audit_before = c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()
    response = await post(p)
    assert response.status_code == expected, response.text
    assert 'private' not in response.text
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one() == audit_before
    assert c.sync.execute(text('SELECT * FROM users ORDER BY id')).all() == users_before
    assert current(p) == ((c.foreign_user, 3) if failure == 'ownership_race' else (c.target, 3 if failure == 'version_race' else 2))
    assert c.session.rollback.await_count > 0


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{}, {'expected_row_version': 0}, {'expected_row_version': True},
    {'expected_row_version': '2'}, {'expected_row_version': 2.0}, {'expected_row_version': None},
    {'expected_row_version': 2**63}, {'expected_row_version': -1},
    {'expected_row_version': 2, 'tenant_id': 'caller-tenant'}, {'expected_row_version': 2, 'user_id': 'caller-user'},
    {'expected_row_version': 2, 'linked_user_id': None}, {'expected_row_version': 2, 'mode': 'editor'},
    {'expected_row_version': 2, 'previous_user_id': None}, {'expected_row_version': 2, 'idempotency_key': 'caller-key'}])
async def test_request_is_exact_version_body_and_rejects_caller_authority(unlink_http, body):
    p = unlink_http; before = snapshot(p)
    response = await post(p, body)
    assert response.status_code == 422, response.text
    assert p.read_kwargs == [] and p.write_kwargs == [] and snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['query', 'bad_id', 'rate', 'lookup_runtime', 'anonymous', 'bad_claims', 'mismatched_principal'])
async def test_transport_authentication_and_runtime_fail_closed_before_writes(unlink_http, monkeypatch, case):
    p = unlink_http; kwargs = {}; expected = 422
    if case == 'query': kwargs['params'] = {'tenant_id': str(p.c.tenant)}
    elif case == 'bad_id': kwargs['identifier'] = 'not-a-uuid'
    elif case == 'rate': p.rate.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError(); expected = 429
    elif case == 'lookup_runtime':
        @asynccontextmanager
        async def unsafe(**kw):
            raise ConstructionDatabaseConfigurationError('private runtime settings')
            yield
        monkeypatch.setattr(routes, 'construction_client_unlink_lookup_transaction', unsafe); expected = 503
    elif case == 'anonymous':
        async def missing(): raise ApiHttpError(status_code=401, code='authentication_required', message='Authentication required.')
        p.app.dependency_overrides[get_authenticated_platform_principal] = missing; expected = 401
    elif case == 'bad_claims': p.codec.decode_access_token = lambda token: object(); expected = 401
    else: p.claims = lambda: AccessTokenClaims(uuid4(), p.token_tenant, uuid4(), p.c.now, p.c.now); expected = 401
    before = snapshot(p)
    response = await post(p, **kwargs)
    assert response.status_code == expected, response.text
    assert p.write_kwargs == [] and snapshot(p) == before and 'private' not in response.text


@pytest.mark.asyncio
async def test_unlink_http_needs_no_replay_encryption_configuration(unlink_http, monkeypatch):
    monkeypatch.delenv('API_IDEMPOTENCY_ENCRYPTION_KEY', raising=False)
    response = await post(unlink_http)
    assert response.status_code == 200 and unlink_http.ledger.calls == 0


def test_openapi_registers_only_approved_unlink_body_without_mandatory_key():
    document = with_local_construction_rate_limit(create_app()).openapi()
    operation = document['paths']['/api/v1/construction/clients/{id}/user-link/unlink']['post']
    assert not any(p.get('in') == 'header' and p['name'].lower() == 'idempotency-key' for p in operation.get('parameters', []))
    schema = document['components']['schemas']['ClientUnlinkRequest']
    assert set(schema['properties']) == {'expected_row_version'} and schema['required'] == ['expected_row_version']
    assert schema['additionalProperties'] is False
    assert set(document['components']['schemas']['ClientUnlinkResponse']['properties']) == {'client_id', 'linked_user_id', 'row_version', 'changed'}
