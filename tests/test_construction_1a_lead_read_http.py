from construction_rate_limit_fixture import with_local_construction_rate_limit
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

import api.routes.construction_lead_read as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction_lead_list_read import ConstructionLeadListFilters, ConstructionLeadListReadCursor, ConstructionLeadListReadPage
from services.construction_lead_read import PUBLIC_LEAD_DETAIL_FIELDS, ConstructionLeadReadNotFoundError
from services.construction_lead_rules import ConstructionLeadValidationError
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_lead_list_read import list_case, listing, another
from test_construction_1a_lead_detail_read import detail_case, no_writes
from test_construction_1a_activity_read_service import read_case
from test_construction_1a_activity_terminal_service import terminal_case
from test_construction_01_project_read_http import walk_routes


PATH = '/api/v1/construction/crm/leads'


def supplied(value):
    return lambda: value


@pytest.fixture
def read_http(list_case):
    c = list_case
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.rate = SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    for dependency, value in (
        (get_current_actor, c.actor), (get_construction_access_context, c.context),
        (get_construction_scoped_session, c.session), (get_tenant_suite_access_service, c.suite),
        (get_tenant_module_access_service, c.module), (get_api_request_rate_limit_service, c.rate),
        (routes.get_lead_read_service, c.detail), (routes.get_lead_list_read_service, c.list_service),
    ):
        c.app.dependency_overrides[dependency] = supplied(value)
    return c


async def get(c, kind='list', *, params=None, identifier=None):
    path = PATH if kind == 'list' else PATH + '/' + (identifier or str(c.lead.id))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url='http://testserver') as client:
        return await client.get(path, params=params, headers={'X-Request-ID': 'lead-read-http', 'X-Tenant-ID': str(uuid4())})


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['list', 'detail'])
async def test_registered_get_uses_real_service_computed_next_action_and_no_writes(read_http, kind):
    c = read_http; c.row.due_at = c.now - timedelta(days=5)
    response = await get(c, kind)
    assert response.status_code == 200, response.text
    body = response.json()
    data = body['data'][0] if kind == 'list' else body['data']
    assert set(data) == PUBLIC_LEAD_DETAIL_FIELDS and data['id'] == str(c.lead.id)
    assert data['tenant_id'] == str(c.tenant) and data['converted_client_id'] is None
    assert data['next_action']['id'] == str(c.row.id) and data['next_action']['status'] == 'planned'
    assert body['meta'] == ({'pagination': {'next_cursor': None}} if kind == 'list' else {})
    assert body['request_id'] == response.headers['X-Request-ID'] == 'lead-read-http'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'private_internal' not in response.text and 'deleted_at' not in response.text
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code='construction')
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code='construction.core')
    c.rate.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['list', 'detail'])
async def test_no_planned_action_returns_null_without_storing_anything(read_http, kind):
    c = read_http
    c.repo.get_next_action_for_lead.return_value = None
    c.list_repo.list_next_actions_for_leads.return_value = []
    response = await get(c, kind)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert (data[0] if kind == 'list' else data)['next_action'] is None
    no_writes(c)


@pytest.mark.asyncio
async def test_list_empty_and_keyset_pagination_through_http(read_http):
    c = read_http
    c.list_repo.list_active_leads.return_value = [c.lead, another(c, 20)]
    first = await get(c, params={'limit': 1, 'source': ' PHONE '})
    assert first.status_code == 200, first.text
    token = first.json()['meta']['pagination']['next_cursor']
    assert isinstance(token, str) and len(first.json()['data']) == 1
    c.list_repo.list_active_leads.return_value = [another(c, 20)]
    c.list_repo.list_next_actions_for_leads.return_value = []
    final = await get(c, params={'limit': 1, 'source': 'phone', 'cursor': token})
    assert final.status_code == 200, final.text
    assert final.json()['data'][0]['id'] == str(UUID(int=20))
    assert final.json()['meta']['pagination']['next_cursor'] is None
    assert c.list_repo.list_active_leads.await_args.kwargs['after'].lead_id == c.lead.id
    c.list_repo.list_active_leads.return_value = []
    empty = await get(c)
    assert empty.status_code == 200 and empty.json()['data'] == []
    no_writes(c)


@pytest.mark.asyncio
async def test_five_existing_filters_are_passed_as_normalized_typed_values(read_http):
    c = read_http
    c.lead.converted_client_id, c.lead.converted_project_id = c.client.id, c.project.id
    params = dict(status='new', source=' PHONE ', responsible_user_id=str(c.user),
        converted_client_id=str(c.client.id), converted_project_id=str(c.project.id), limit=100)
    response = await get(c, params=params)
    assert response.status_code == 200, response.text
    kwargs = c.list_repo.list_active_leads.await_args.kwargs
    assert kwargs['limit'] == 100 and kwargs['source'] == 'phone' and kwargs['status'] == 'new'
    assert kwargs['responsible_user_id'] == c.user and kwargs['converted_client_id'] == c.client.id
    assert kwargs['converted_project_id'] == c.project.id
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['absent', 'foreign', 'deleted'])
async def test_detail_unavailable_cases_share_generic_404(read_http, missing):
    c = read_http
    if missing == 'absent': c.lead_repo.get_active_lead.return_value = None
    elif missing == 'foreign': c.lead.tenant_id = uuid4()
    else: c.lead.deleted_at = c.now
    response = await get(c, 'detail')
    assert response.status_code == 404, response.text
    assert response.json() == {'error': {'code': 'not_found', 'message': 'Construction lead is not available.', 'request_id': 'lead-read-http'}}
    assert c.lead.name not in response.text
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['list', 'detail'])
@pytest.mark.parametrize('case', ['anonymous', 'inactive', 'permission', 'wrong_tenant', 'wrong_user', 'project_only', 'document_only', 'suite', 'module', 'rate', 'membership', 'revoked'])
async def test_auth_scope_and_fresh_membership_gates_precede_business_reads(read_http, kind, case):
    c = read_http; expected = 403
    if case == 'anonymous':
        c.app.dependency_overrides.pop(get_current_actor); expected = 401
    elif case == 'inactive': c.actor.status = 'blocked'
    elif case in ('permission', 'wrong_tenant', 'wrong_user'):
        c.context = replace(c.context, **{'permission': {'permissions': ()}, 'wrong_tenant': {'tenant_id': uuid4()}, 'wrong_user': {'user_id': uuid4()}}[case])
    elif case in ('project_only', 'document_only'):
        c.context = ConstructionAccessContext(c.user, c.tenant, ('OBSERVER',), ('construction.projects.read',),
            (ConstructionGrantContext('OBSERVER', 'project' if case == 'project_only' else 'document', c.project.id),))
    elif case == 'suite': c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif case == 'module': c.module.require_enabled.side_effect = ModuleDisabledError()
    elif case == 'rate': c.rate.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError(); expected = 429
    elif case == 'membership': c.access.has_active_platform_membership.return_value = False
    else: c.grant.status = 'revoked'
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    response = await get(c, kind)
    assert response.status_code == expected, response.text
    assert 'data' not in response.json() and c.lead.name not in response.text
    c.list_repo.list_active_leads.assert_not_called(); c.lead_repo.get_active_lead.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('params', [dict(limit=0), dict(limit=101), dict(limit='true'), dict(limit='1.5'),
    dict(status='qualified'), dict(source=' '), dict(responsible_user_id='bad'), dict(converted_client_id='bad'),
    dict(converted_project_id='bad'), dict(cursor='bad'), dict(cursor='x'*1025), dict(tenant_id='private-untrusted'),
    dict(user_id='private-untrusted'), dict(next_action='private-untrusted'), [('limit', '1'), ('limit', '2')],
    [('source', 'phone'), ('source', 'email')]])
async def test_invalid_list_query_does_not_read_or_echo_caller_values(read_http, params):
    c = read_http
    response = await get(c, params=params)
    assert response.status_code == 422, response.text
    assert response.json()['error']['code'] == 'validation_error' and 'private-untrusted' not in response.text
    c.list_repo.list_active_leads.assert_not_called(); no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,params,identifier', [('detail', None, 'bad-id'), ('detail', {'tenant_id': 'bad'}, None),
    ('detail', {'next_action': 'bad'}, None)])
async def test_invalid_detail_boundary_has_no_reads(read_http, kind, params, identifier):
    c = read_http
    response = await get(c, kind, params=params, identifier=identifier)
    assert response.status_code == 422
    c.lead_repo.get_active_lead.assert_not_called(); no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['actor', 'tenant', 'source', 'revoked'])
async def test_http_cursor_cannot_cross_context_or_restore_lost_authority(read_http, change):
    c = read_http
    c.list_repo.list_active_leads.return_value = [c.lead, another(c, 20)]
    first = await get(c, params={'limit': 1})
    assert first.status_code == 200
    token = first.json()['meta']['pagination']['next_cursor']
    c.list_repo.list_active_leads.reset_mock()
    params = dict(cursor=token)
    if change == 'source': params['source'] = 'email'
    elif change == 'revoked': c.grant.status = 'revoked'
    else:
        c.actor = SimpleNamespace(**{**vars(c.actor), 'user_id' if change == 'actor' else 'tenant_id': uuid4()})
        c.context = replace(c.context, user_id=c.actor.user_id, tenant_id=c.actor.tenant_id)
        c.app.dependency_overrides[get_current_actor] = lambda: c.actor
        c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    response = await get(c, params=params)
    assert response.status_code == (403 if change == 'revoked' else 422), response.text
    c.list_repo.list_active_leads.assert_not_called(); no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['list', 'detail'])
@pytest.mark.parametrize('failure,status', [('validation', 422), ('storage', 500), ('not_found', 404)])
async def test_service_errors_are_sanitized(read_http, kind, failure, status):
    c = read_http
    error = {'validation': ConstructionLeadValidationError, 'storage': RuntimeError, 'not_found': ConstructionLeadReadNotFoundError}[failure]
    method = AsyncMock(side_effect=error('private-error-detail'))
    dependency = routes.get_lead_list_read_service if kind == 'list' else routes.get_lead_read_service
    c.app.dependency_overrides[dependency] = lambda: SimpleNamespace(**{'list_leads' if kind == 'list' else 'get_lead': method})
    response = await get(c, kind)
    assert response.status_code == status and 'private-error-detail' not in response.text
    assert 'data' not in response.json(); no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['list', 'detail'])
@pytest.mark.parametrize('bad', ['tenant', 'extra', 'action_tenant', 'action_lead', 'terminal_action', 'version'])
async def test_invalid_service_projection_never_leaks_a_partial_response(read_http, kind, bad):
    c = read_http
    data = await c.detail.get_lead(actor=c.actor, access_context=c.context, lead_id=c.lead.id)
    if bad == 'tenant': data['tenant_id'] = str(uuid4())
    elif bad == 'extra': data['private_extra'] = 'private-field-value'
    elif bad == 'version': data['row_version'] = True
    elif bad == 'terminal_action': data['next_action']['status'] = 'completed'
    else: data['next_action']['tenant_id' if bad == 'action_tenant' else 'lead_id'] = str(uuid4())
    result = ConstructionLeadListReadPage((data,), None) if kind == 'list' else data
    dependency = routes.get_lead_list_read_service if kind == 'list' else routes.get_lead_read_service
    c.app.dependency_overrides[dependency] = lambda: SimpleNamespace(**{'list_leads' if kind == 'list' else 'get_lead': AsyncMock(return_value=result)})
    response = await get(c, kind)
    assert response.status_code == 500, response.text
    assert 'data' not in response.json() and c.lead.name not in response.text and 'private-field-value' not in response.text
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['tuple', 'too_many', 'duplicate', 'filter', 'position', 'cursor_actor'])
async def test_list_rejects_bad_page_or_cursor_from_service(read_http, bad):
    c = read_http
    data = await c.detail.get_lead(actor=c.actor, access_context=c.context, lead_id=c.lead.id)
    position = None; items = (data,); params = {'limit': 1}
    if bad == 'tuple': items = [data]
    elif bad in ('too_many', 'duplicate'):
        items = (data, data)
        if bad == 'duplicate': params['limit'] = 2
    elif bad == 'filter': params['source'] = 'email'
    else: position = ConstructionLeadListReadCursor(c.tenant, uuid4() if bad == 'cursor_actor' else c.user,
        ConstructionLeadListFilters(), c.now, UUID(int=20) if bad == 'position' else c.lead.id)
    page = ConstructionLeadListReadPage(items, position)
    c.app.dependency_overrides[routes.get_lead_list_read_service] = lambda: SimpleNamespace(list_leads=AsyncMock(return_value=page))
    response = await get(c, params=params)
    assert response.status_code == 500, response.text
    assert 'data' not in response.json(); no_writes(c)


def test_factories_and_openapi_register_exactly_two_approved_gets(read_http):
    c = read_http
    detail, listing_service = routes.get_lead_read_service(c.session), routes.get_lead_list_read_service(c.session)
    assert detail.session is detail.repository.session is detail.access.session is detail.activities.session is c.session
    assert listing_service.session is listing_service.repository.session is listing_service.access.session is c.session
    schema = c.app.openapi()
    for path in (PATH, PATH + '/{id}'):
        matches = [r for item, r in walk_routes(c.app) if item == path and 'GET' in getattr(r, 'methods', ())]
        assert len(matches) == 1
        operation = schema['paths'][path]['get']
        assert {'401', '403', '422', '429', '500'} <= operation['responses'].keys()
        assert not {'Idempotency-Key', 'tenant_id', 'user_id'} & {p['name'] for p in operation['parameters']}
    parameters = {p['name']: p['schema'] for p in schema['paths'][PATH]['get']['parameters']}
    assert set(parameters) == {'limit', 'cursor', 'status', 'source', 'responsible_user_id', 'converted_client_id', 'converted_project_id'}
    assert parameters['limit']['default'] == 20 and parameters['limit']['maximum'] == 100
    assert schema['components']['schemas']['LeadListEnvelope']['properties']['data']['type'] == 'array'
    assert '404' in schema['paths'][PATH + '/{id}']['get']['responses']
    assert set(schema['components']['schemas']['ConstructionLeadReadResponse']['properties']) == PUBLIC_LEAD_DETAIL_FIELDS
    assert 'post' in schema['paths'][PATH] and 'patch' in schema['paths'][PATH + '/{id}/status']
