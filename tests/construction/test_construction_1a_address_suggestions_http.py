from construction_rate_limit_fixture import with_local_construction_rate_limit
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

import api.routes.construction_address_suggestions as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction.construction_address_matching import ConstructionAddressSuggestion
from services.construction.construction_address_suggestions import ConstructionAddressSuggestionValidationError
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_address_suggestions_service import suggestions, source, project_scope


PATH = '/api/v1/construction/address-suggestions'


@pytest.fixture
def address_http(suggestions):
    c = suggestions
    c.sources = [source(c, client_id=c.client, client_name='Existing client')]
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.rate = SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rate
    c.app.dependency_overrides[routes.get_address_suggestions_service] = lambda: c.service
    return c


async def get(c, params=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
            base_url='http://testserver') as client:
        return await client.get(PATH, params={'q': 'Rua das Flores 12'} if params is None else params,
            headers={'X-Request-ID': 'address-suggestions-http'})


def no_writes(c):
    for name in ('add', 'flush', 'commit', 'rollback'):
        getattr(c.session, name).assert_not_called()


@pytest.mark.asyncio
async def test_get_uses_real_service_current_actor_scope_and_safe_envelope(address_http):
    c = address_http
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['meta'] == {} and body['request_id'] == response.headers['X-Request-ID'] == 'address-suggestions-http'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert {item['entity_type'] for item in body['data']} == {'client', 'project'}
    expected = {'entity_type', 'entity_id', 'label', 'address_raw', 'match_kind', 'score'}
    assert all(set(item) == expected and item['match_kind'] == 'exact' and item['score'] == 1000 for item in body['data'])
    assert 'tenant_id' not in response.text and 'phone' not in response.text and 'token_hash' not in response.text
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code='construction')
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code='construction.core')
    c.rate.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    no_writes(c)


@pytest.mark.asyncio
async def test_project_only_actor_gets_only_its_permitted_project(address_http):
    c = address_http
    project_scope(c)
    c.sources = [source(c, 7, client_id=c.client, client_name='Private client name')]
    response = await get(c)
    assert response.status_code == 200, response.text
    assert [(item['entity_type'], item['entity_id']) for item in response.json()['data']] == [('project', str(UUID(int=7)))]
    assert 'Private client name' not in response.text
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('params', [{}, {'q': ''}, {'q': '...'}, {'q': 'unrelated address'}])
async def test_empty_or_unmatched_query_is_a_successful_empty_list(address_http, params):
    c = address_http
    response = await get(c, params)
    assert response.status_code == 200 and response.json()['data'] == []
    no_writes(c)


@pytest.mark.asyncio
async def test_maximum_ten_and_explicit_smaller_limit_are_enforced(address_http):
    c = address_http
    c.sources = [source(c, number) for number in range(1, 30)]
    assert len((await get(c)).json()['data']) == 10
    assert len((await get(c, {'q': 'Rua das Flores 12', 'limit': 3})).json()['data']) == 3
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['anonymous', 'inactive', 'permission', 'foreign_context', 'wrong_user', 'document_only', 'suite', 'module', 'rate'])
async def test_security_gates_deny_before_source_reads(address_http, case):
    c = address_http
    expected = 403
    if case == 'anonymous':
        c.app.dependency_overrides.pop(get_current_actor)
        expected = 401
    elif case == 'inactive': c.actor.status = 'blocked'
    elif case == 'permission': c.context = replace(c.context, permissions=())
    elif case == 'foreign_context': c.context = replace(c.context, tenant_id=uuid4())
    elif case == 'wrong_user': c.context = replace(c.context, user_id=uuid4())
    elif case == 'document_only':
        c.context = ConstructionAccessContext(c.user, c.tenant, ('OBSERVER',), ('construction.projects.read',),
            (ConstructionGrantContext('OBSERVER', 'document', uuid4()),))
    elif case == 'suite': c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif case == 'module': c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rate.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected = 429
    response = await get(c)
    assert response.status_code == expected, response.text
    assert not c.read_calls and 'Existing client' not in response.text
    assert response.json()['error']['request_id'] == response.headers['X-Request-ID']
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('params', [{'limit': 0}, {'limit': 11}, {'limit': 'true'}, {'limit': '1.5'},
    {'q': 'x'*(routes.MAX_ADDRESS_QUERY_LENGTH+1)}, {'tenant_id': 'untrusted'}, {'user_id': 'untrusted'},
    {'project_ids': 'untrusted'}, [('q', 'one'), ('q', 'two')], [('limit', '1'), ('limit', '2')]])
async def test_invalid_or_caller_identity_parameters_never_read_sources(address_http, params):
    c = address_http
    response = await get(c, params)
    assert response.status_code == 422, response.text
    assert response.json()['error']['code'] == 'validation_error' and not c.read_calls
    assert 'untrusted' not in response.text
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['storage', 'service_validation', 'bad_item', 'duplicate', 'too_many', 'bad_score', 'bad_scope'])
async def test_failure_responses_are_generic_and_never_return_partial_suggestions(address_http, case):
    c = address_http
    item = ConstructionAddressSuggestion('project', UUID(int=1), 'private-label', 'private-address', 'exact', 1000)
    if case == 'storage':
        method = AsyncMock(side_effect=RuntimeError('private-database-detail'))
    elif case == 'service_validation':
        method = AsyncMock(side_effect=ConstructionAddressSuggestionValidationError('private-query-detail'))
    else:
        values = {'bad_item': ({'private': 'private-orm-field'},), 'duplicate': (item, item),
            'too_many': tuple(replace(item, entity_id=UUID(int=i)) for i in range(1, 12)),
            'bad_score': (replace(item, score=True),), 'bad_scope': (item,)}[case]
        method = AsyncMock(return_value=values)
        if case == 'bad_scope': project_scope(c)
    c.service = SimpleNamespace(suggest_addresses=method)
    response = await get(c)
    assert response.status_code == (422 if case == 'service_validation' else 500), response.text
    assert 'private-' not in response.text and 'data' not in response.json()
    no_writes(c)


def test_factory_and_openapi_contract(address_http):
    c = address_http
    service = routes.get_address_suggestions_service(c.session)
    assert service.session is service.repository.session is service.access_repository.session is c.session
    schema = c.app.openapi()
    operation = schema['paths'][PATH]['get']
    parameters = {item['name']: item for item in operation['parameters']}
    assert parameters['q']['schema']['default'] == ''
    assert parameters['q']['schema']['maxLength'] == routes.MAX_ADDRESS_QUERY_LENGTH
    assert parameters['limit']['schema']['default'] == 10 and parameters['limit']['schema']['maximum'] == 10
    assert not {'tenant_id', 'user_id', 'project_ids', 'Idempotency-Key'} & parameters.keys()
    assert {'401', '403', '422', '429', '500'} <= operation['responses'].keys()
    fields = schema['components']['schemas']['AddressSuggestionResponse']['properties']
    assert set(fields) == {'entity_type', 'entity_id', 'label', 'address_raw', 'match_kind', 'score'}
    assert 'post' not in schema['paths'][PATH] and 'patch' not in schema['paths'][PATH]


def test_endpoint_is_registered_exactly_once(address_http):
    from test_construction_01_project_read_http import walk_routes
    matches = [route for path, route in walk_routes(address_http.app)
        if path == PATH and 'GET' in getattr(route, 'methods', ())]
    assert len(matches) == 1 and matches[0].endpoint is routes.suggest_addresses
