from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

import api.routes.construction_client_read as routes
from api.construction_client_card_cursors import decode_client_card_cursor, encode_client_card_cursor
from services.construction_client_history_contract import ConstructionHistoryClientOnlyActivityEvidence
from test_construction_01_client_read_http import client_http, get, no_write
from test_construction_1a_client_card_read_service import card_case


SECTIONS = ('projects', 'leads', 'activities', 'history')
METHODS = dict(projects='list_projects_for_client', leads='list_active_leads',
    activities='list_planned_activities_for_client', history='list_history_for_client')


@pytest.fixture
def card_http(client_http):
    c = client_http
    for name, item in dict(projects=c.card.project, leads=c.card.lead,
        activities=c.card.activity_record, history=c.card.event).items():
        getattr(c.card.repositories[name], METHODS[name]).return_value = [item]
    return c


@pytest.mark.asyncio
async def test_get_composes_all_real_read_services_in_approved_flat_format(card_http):
    c = card_http
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {'data', 'meta', 'request_id'}
    assert set(body['data']) == set(routes.ClientReadResponse.model_fields) | set(SECTIONS)
    assert 'client' not in body['data']
    for name in SECTIONS:
        assert len(body['data'][name]) == 1
    assert body['data']['activities'][0]['id'] == str(c.card.activity.id)
    assert body['data']['history'][0]['event_type'] == 'construction_client_archived'
    assert body['meta'] == {'pagination': {name+'_next_cursor': None for name in SECTIONS}}
    assert 'private-orm-field' not in response.text and 'private-token-hash' not in response.text
    c.card.access.has_active_platform_membership.assert_awaited()
    no_write(c)


@pytest.mark.asyncio
async def test_each_list_has_own_cursor_and_next_page_does_not_advance_other_lists(card_http):
    c = card_http
    originals = {}
    for name in SECTIONS:
        original = getattr(c.card.repositories[name], METHODS[name]).return_value[0]
        identifier = original.activity.id if name == 'activities' else original.id
        originals[name] = identifier
        if name == 'activities':
            second = replace(original, activity=SimpleNamespace(**{**vars(original.activity), 'id': UUID(int=identifier.int+1)}))
        elif name == 'history':
            second = replace(original, id=UUID(int=identifier.int-1))
        else:
            second = SimpleNamespace(**{**vars(original), 'id': UUID(int=identifier.int-1)})
        records = [original, second]
        async def paged(_records=records, **kwargs):
            return _records if kwargs.get('after') is None else _records[1:]
        getattr(c.card.repositories[name], METHODS[name]).side_effect = paged
    limits = {name+'_limit': 1 for name in SECTIONS}
    first = await get(c, params=limits)
    assert first.status_code == 200, first.text
    pagination = first.json()['meta']['pagination']
    assert set(pagination) == {name+'_next_cursor' for name in SECTIONS}
    for name in SECTIONS:
        token = pagination[name+'_next_cursor']
        assert isinstance(token, str)
        position = decode_client_card_cursor(token, section=name,
            tenant_id=c.tenant, user_id=c.user, client_id=c.client_id)
        id_field = 'activity_id' if name == 'activities' else 'event_id' if name == 'history' else name[:-1]+'_id'
        assert getattr(position, id_field) == originals[name]
        # Consume only this section's cursor; all other lists stay on page one.
        second = await get(c, params={**limits, name+'_cursor': token})
        assert second.status_code == 200, second.text
        page = second.json()
        assert page['meta']['pagination'][name+'_next_cursor'] is None
        assert page['data'][name][0]['id'] != str(originals[name])
        for other in set(SECTIONS)-{name}:
            assert page['data'][other] == first.json()['data'][other]
            assert page['meta']['pagination'][other+'_next_cursor'] == pagination[other+'_next_cursor']
    no_write(c)


@pytest.mark.asyncio
async def test_defaults_and_different_limits_are_passed_to_each_service(client_http):
    c = client_http
    original = c.service.get_client_card
    spy = AsyncMock(wraps=original)
    c.service = SimpleNamespace(get_client_card=spy)
    assert (await get(c)).status_code == 200
    kwargs = spy.await_args.kwargs
    assert all(kwargs[name+'_limit'] == 20 and kwargs[name+'_after'] is None for name in SECTIONS)
    response = await get(c, params={name+'_limit': value for name, value in zip(SECTIONS, (1, 3, 7, 100))})
    assert response.status_code == 200
    assert [spy.await_args.kwargs[name+'_limit'] for name in SECTIONS] == [1, 3, 7, 100]
    assert spy.await_args.kwargs['actor'] is c.actor
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
@pytest.mark.parametrize('value', ['0', '-1', '101', 'private-invalid-limit', ''])
async def test_invalid_independent_limits_are_422_before_business_read(client_http, section, value):
    c = client_http
    response = await get(c, params={section+'_limit': value})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'validation_error'
    assert 'private-invalid-limit' not in response.text
    c.repo.get_active_client.assert_not_called()
    c.card.access.has_active_platform_membership.assert_not_called()
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
@pytest.mark.parametrize('fault', ['bad', 'empty', 'oversized', 'section', 'tenant', 'user', 'client'])
async def test_invalid_cursor_cannot_cross_section_user_tenant_or_client(card_http, section, fault):
    c = card_http
    result = await c.service.get_client_card(actor=c.actor, access_context=c.context, client_id=c.client_id,
        **{name+'_limit': 1 for name in SECTIONS})
    # Build a genuine cursor from the corresponding item, independent of lookahead.
    from services.construction_client_projects_read import ConstructionClientProjectsReadCursor
    from services.construction_client_leads_read import ConstructionClientLeadsReadCursor
    from services.construction_client_activities_read import ConstructionClientActivitiesReadCursor
    from services.construction_client_history_read import ConstructionClientHistoryReadCursor
    types = dict(projects=ConstructionClientProjectsReadCursor, leads=ConstructionClientLeadsReadCursor,
        activities=ConstructionClientActivitiesReadCursor, history=ConstructionClientHistoryReadCursor)
    actual_section = next(name for name in SECTIONS if name != section) if fault == 'section' else section
    options = dict(tenant_id=uuid4() if fault == 'tenant' else c.tenant,
        user_id=uuid4() if fault == 'user' else c.user, client_id=uuid4() if fault == 'client' else c.client_id)
    cursor = types[actual_section](options['tenant_id'], options['user_id'], options['client_id'], c.card.now, UUID(int=100))
    token = encode_client_card_cursor(cursor, section=actual_section, **options)
    if fault == 'bad': token = 'private-invalid-cursor!'
    if fault == 'empty': token = ''
    if fault == 'oversized': token = 'p'*1025
    c.repo.get_active_client.reset_mock()
    c.card.access.has_active_platform_membership.reset_mock()
    response = await get(c, params={section+'_cursor': token})
    assert response.status_code == 422, response.text
    assert 'private-invalid-cursor' not in response.text
    assert 'Read client' not in response.text
    c.repo.get_active_client.assert_not_called()
    c.card.access.has_active_platform_membership.assert_not_called()
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
async def test_failed_section_never_returns_partial_card(card_http, section):
    c = card_http
    getattr(c.card.repositories[section], METHODS[section]).side_effect = RuntimeError('private-query-detail')
    response = await get(c)
    assert response.status_code == 500
    assert response.json()['error']['code'] == 'internal_error'
    assert 'data' not in response.json()
    assert 'private-query-detail' not in response.text and 'Read client' not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_revoked_membership_during_card_read_denies_whole_response(card_http):
    c = card_http
    original = c.card.repositories['history'].list_history_for_client
    async def revoke(**kwargs):
        result = original.return_value
        c.card.access.has_active_platform_membership.return_value = False
        return result
    original.side_effect = revoke
    response = await get(c)
    assert response.status_code == 403 and 'data' not in response.json()
    no_write(c)


@pytest.mark.asyncio
async def test_legacy_activity_history_is_visible_without_private_proof_or_payload_leak(card_http):
    c = card_http
    event = replace(c.card.event, event_type='construction_activity_created', entity_type='construction_activity',
        entity_id=c.card.activity.id, payload=dict(operation='create', changes=[], reason=None,
            token_hash='private-event-hash', response_ciphertext='private-ciphertext'),
        activity_evidence=ConstructionHistoryClientOnlyActivityEvidence(c.card.activity.id,
            c.tenant, c.client_id, c.card.now-timedelta(seconds=1)))
    c.card.repositories['history'].list_history_for_client.return_value = [event]
    response = await get(c)
    assert response.status_code == 200, response.text
    history = response.json()['data']['history']
    assert len(history) == 1 and history[0]['entity_id'] == str(c.card.activity.id)
    assert history[0]['payload'] == dict(operation='create', changes=[], reason=None)
    assert all(value not in response.text for value in ('activity_evidence', 'legacy_activity_evidence',
        'private-event-hash', 'private-ciphertext', 'token_hash', 'response_ciphertext'))
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
async def test_response_rejects_cross_tenant_section_even_after_service(card_http, section):
    c = card_http
    result = await c.service.get_client_card(actor=c.actor, access_context=c.context, client_id=c.client_id)
    page = getattr(result, section)
    forged = {**page.items[0], 'tenant_id': str(c.foreign)}
    c.service = SimpleNamespace(get_client_card=AsyncMock(return_value=replace(result,
        **{section: replace(page, items=(forged,))})))
    response = await get(c)
    assert response.status_code == 500 and 'data' not in response.json()
    no_write(c)


@pytest.mark.asyncio
async def test_invalid_server_cursor_is_500_instead_of_blame_on_request(card_http):
    c = card_http
    result = await c.service.get_client_card(actor=c.actor, access_context=c.context, client_id=c.client_id)
    c.service = SimpleNamespace(get_client_card=AsyncMock(return_value=replace(result,
        projects=replace(result.projects, next_cursor=object()))))
    response = await get(c, params={'projects_limit': 1})
    assert response.status_code == 500 and response.json()['error']['code'] == 'internal_error'
    no_write(c)


def test_openapi_documents_exact_independent_parameters_and_flat_pagination(card_http):
    schema = card_http.app.openapi()
    operation = schema['paths']['/api/v1/construction/clients/{id}']['get']
    query = {parameter['name']: parameter for parameter in operation['parameters'] if parameter['in'] == 'query'}
    assert set(query) == {name+suffix for name in SECTIONS for suffix in ('_limit', '_cursor')}
    for name in SECTIONS:
        limit = query[name+'_limit']['schema']
        assert limit['default'] == 20 and limit['minimum'] == 1 and limit['maximum'] == 100
        cursor = query[name+'_cursor']['schema']
        assert next(item for item in cursor['anyOf'] if item['type'] == 'string')['maxLength'] == 1024
    pagination = schema['components']['schemas']['ClientCardPagination']
    assert set(pagination['properties']) == set(pagination['required']) == {name+'_next_cursor' for name in SECTIONS}
    assert pagination['additionalProperties'] is False
    for name in SECTIONS:
        assert schema['components']['schemas']['ClientCardResponse']['properties'][name]['type'] == 'array'
