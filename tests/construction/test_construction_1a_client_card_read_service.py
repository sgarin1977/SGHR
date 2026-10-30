import asyncio
from contextlib import nullcontext
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from database.repositories.construction.construction_client_history import ConstructionClientHistoryRecord
from database.repositories.construction.construction_client_activities import ConstructionClientActivityRecord
from services.construction.construction_client_card_read import (
    ConstructionClientCardReadService, ConstructionClientCardReadResult, ConstructionClientCardReadOperationError,
    ClientCardClientResponse, build_construction_client_card_read_service,
)
from services.construction.construction_client_read import ConstructionClientReadService
from services.construction.construction_client_projects_read import ConstructionClientProjectsReadService, ConstructionClientProjectsReadCursor
from services.construction.construction_client_leads_read import ConstructionClientLeadsReadService, ConstructionClientLeadsReadCursor
from services.construction.construction_client_activities_read import ConstructionClientActivitiesReadService, ConstructionClientActivitiesReadCursor
from services.construction.construction_client_history_read import ConstructionClientHistoryReadService, ConstructionClientHistoryReadCursor
from services.construction.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


SECTIONS = ('projects', 'leads', 'activities', 'history')
METHODS = dict(projects='list_projects_for_client', leads='list_leads_for_client',
    activities='list_planned_activities_for_client', history='list_history_for_client')


@pytest.fixture
def card_case():
    c = SimpleNamespace(tenant=uuid4(), user=uuid4(), client=uuid4(), now=datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
    c.actor = SimpleNamespace(tenant_id=c.tenant, user_id=c.user, status='active')
    c.context = ConstructionAccessContext(c.user, c.tenant, ('OWNER',), ('construction.projects.read',),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    c.session = SimpleNamespace(no_autoflush=nullcontext(), in_transaction=lambda: True,
        execute=AsyncMock(), add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    c.grant = SimpleNamespace(role='OWNER', scope_type='tenant', scope_id=c.tenant, status='active', expires_at=None)
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    c.client_row = SimpleNamespace(id=c.client, tenant_id=c.tenant, display_name='Client', client_type='person',
        phone='123', email=None, notes='Notes', linked_user_id=None, row_version=1,
        created_at=c.now, updated_at=c.now, deleted_at=None, private='secret')
    c.clients = SimpleNamespace(session=c.session, get_active_client=AsyncMock(return_value=c.client_row))
    c.project = SimpleNamespace(id=UUID(int=50), tenant_id=c.tenant, client_id=c.client, name='Project', status='completed',
        responsible_user_id=c.user, comment=None, address_raw='Address', address_formatted=None, country_code=None,
        region=None, city=None, postal_code=None, latitude=None, longitude=None, address_provider=None,
        provider_place_id=None, address_verification_status='pending', created_by=c.user, created_at=c.now,
        updated_at=c.now, row_version=1, deleted_at=None)
    c.lead = SimpleNamespace(id=UUID(int=60), tenant_id=c.tenant, source='phone', name='Lead', work_type_description='Work',
        phone='123', email=None, address_raw=None, comment=None, responsible_user_id=c.user, status='won',
        converted_client_id=c.client, converted_project_id=c.project.id, created_at=c.now, updated_at=c.now,
        row_version=2, deleted_at=None)
    c.activity = SimpleNamespace(id=UUID(int=70), tenant_id=c.tenant, type='call', status='planned', lead_id=c.lead.id,
        client_id=c.client, project_id=c.project.id, due_at=c.now, timezone='UTC', responsible_user_id=c.user,
        comment=None, result=None, completed_at=None, cancelled_at=None, created_by_user_id=c.user,
        created_at=c.now, updated_at=c.now, row_version=1, deleted_at=None)
    c.activity_record = ConstructionClientActivityRecord(c.activity, c.lead.id, c.tenant, c.client,
        c.project.id, c.tenant, c.client)
    c.event = ConstructionClientHistoryRecord(UUID(int=80), c.tenant, c.user, 'construction_client_archived',
        'construction_client', c.client, dict(operation='archive', changes=[], reason=None, related_client_id=str(c.client)), c.now)
    c.repositories = {
        'projects': SimpleNamespace(session=c.session, list_projects_for_client=AsyncMock(return_value=[c.project])),
        'leads': SimpleNamespace(session=c.session, list_active_leads=AsyncMock(return_value=[c.lead])),
        'activities': SimpleNamespace(session=c.session, list_planned_activities_for_client=AsyncMock(return_value=[c.activity_record])),
        'history': SimpleNamespace(session=c.session, list_history_for_client=AsyncMock(return_value=[c.event]),
            get_client_identity_for_history=AsyncMock(return_value=dict(id=c.client, tenant_id=c.tenant))),
    }
    c.services = {}
    for name, cls in [('projects', ConstructionClientProjectsReadService), ('leads', ConstructionClientLeadsReadService),
        ('activities', ConstructionClientActivitiesReadService), ('history', ConstructionClientHistoryReadService)]:
        options = dict(session=c.session, repository=c.repositories[name], access_repository=c.access, now_provider=lambda: c.now)
        if name != 'history': options['client_repository'] = c.clients
        c.services[name] = cls(**options)
    c.options = dict(session=c.session, client_service=ConstructionClientReadService(repository=c.clients),
        access_repository=c.access, now_provider=lambda: c.now, **{name+'_service': value for name, value in c.services.items()})
    c.service = ConstructionClientCardReadService(**c.options)
    return c


async def read(c, **changes):
    return await c.service.get_client_card(actor=c.actor, access_context=c.context, client_id=c.client, **changes)


def no_writes(c):
    for method in ('add', 'flush', 'commit', 'rollback'):
        getattr(c.session, method).assert_not_called()


@pytest.mark.asyncio
async def test_composes_real_authorized_services_and_returns_only_existing_sections(card_case):
    c = card_case
    result = await read(c)
    assert isinstance(result, ConstructionClientCardReadResult)
    assert {field.name for field in fields(result)} == {'client', *SECTIONS}
    assert set(result.client) == set(ClientCardClientResponse.model_fields)
    assert result.client['id'] == str(c.client) and 'private' not in result.client
    assert result.projects.items[0]['status'] == 'completed'
    assert result.leads.items[0]['converted_client_id'] == str(c.client)
    assert len(result.activities.items) == 1  # All three links to the client, once.
    assert result.history.items[0]['event_type'] == 'construction_client_archived'
    for name in SECTIONS:
        assert getattr(result, name).next_cursor is None
    c.client_row.display_name = 'Changed later'; c.project.name = 'Changed later'
    c.event.payload['reason'] = 'Changed later'
    assert result.client['display_name'] == 'Client' and result.projects.items[0]['name'] == 'Project'
    assert result.history.items[0]['payload']['reason'] is None
    no_writes(c)


@pytest.mark.asyncio
async def test_empty_related_sections_keep_available_client(card_case):
    c = card_case
    for name, method in [('projects', 'list_projects_for_client'), ('leads', 'list_active_leads'),
        ('activities', 'list_planned_activities_for_client'), ('history', 'list_history_for_client')]:
        getattr(c.repositories[name], method).return_value = []
    result = await read(c)
    assert result.client['id'] == str(c.client)
    assert all(getattr(result, name).items == () for name in SECTIONS)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
@pytest.mark.parametrize('limit', [0, 101, True, '20'])
async def test_invalid_independent_page_limits_fail_before_reads(card_case, section, limit):
    c = card_case
    with pytest.raises(ConstructionClientValidationError): await read(c, **{section+'_limit': limit})
    c.access.has_active_platform_membership.assert_not_called(); c.clients.get_active_client.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
@pytest.mark.parametrize('fault', ['tenant', 'user', 'client', 'type'])
async def test_cursor_cannot_cross_section_actor_client_or_tenant(card_case, section, fault):
    c = card_case
    classes = dict(projects=ConstructionClientProjectsReadCursor, leads=ConstructionClientLeadsReadCursor,
        activities=ConstructionClientActivitiesReadCursor, history=ConstructionClientHistoryReadCursor)
    cursor = classes[section](uuid4() if fault=='tenant' else c.tenant, uuid4() if fault=='user' else c.user,
        uuid4() if fault=='client' else c.client, c.now, UUID(int=100))
    if fault == 'type': cursor = object()
    with pytest.raises(ConstructionClientValidationError): await read(c, **{section+'_after': cursor})
    c.access.has_active_platform_membership.assert_not_called(); c.clients.get_active_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['inactive', 'context_tenant', 'context_user', 'project_scope', 'membership', 'revoked', 'expired', 'transaction'])
async def test_initial_access_and_transaction_guards(card_case, fault):
    c = card_case
    if fault == 'inactive': c.actor.status = 'blocked'
    if fault == 'context_tenant': c.context = replace(c.context, tenant_id=uuid4())
    if fault == 'context_user': c.context = replace(c.context, user_id=uuid4())
    if fault == 'project_scope':
        c.context = ConstructionAccessContext(c.user, c.tenant, ('OBSERVER',), ('construction.projects.read',),
            (ConstructionGrantContext('OBSERVER', 'project', c.project.id),))
    if fault == 'membership': c.access.has_active_platform_membership.return_value = False
    if fault == 'revoked': c.grant.status = 'revoked'
    if fault == 'expired': c.grant.expires_at = c.now
    if fault == 'transaction': c.session.in_transaction = lambda: False
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ConstructionClientCardReadOperationError)):
        await read(c)
    c.clients.get_active_client.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
async def test_revocation_after_any_completed_section_prevents_whole_card_publication(card_case, section):
    c = card_case
    original = getattr(c.services[section], METHODS[section])
    async def revoke(**kwargs):
        result = await original(**kwargs)
        c.grant.status = 'revoked'
        return result
    setattr(c.services[section], METHODS[section], revoke)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)): await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['membership', 'grants', 'deleted', 'foreign'])
async def test_last_client_await_cannot_bypass_final_access_check(card_case, fault):
    c = card_case
    async def client(**kwargs):
        if c.clients.get_active_client.await_count == 8:
            if fault == 'membership': c.access.has_active_platform_membership.return_value = False
            if fault == 'grants': c.grant.status = 'revoked'
            if fault == 'deleted': c.client_row.deleted_at = c.now
            if fault == 'foreign': c.client_row.tenant_id = uuid4()
        return c.client_row
    c.clients.get_active_client.side_effect = client
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ConstructionClientNotFoundError)):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_sections_are_read_sequentially_and_page_options_are_independent(card_case):
    c = card_case; calls = []; active = False
    for name in SECTIONS:
        method = getattr(c.services[name], METHODS[name])
        async def wrapped(_name=name, _method=method, **kwargs):
            nonlocal active
            assert not active; active = True
            await asyncio.sleep(0)
            calls.append((_name, kwargs['limit'], kwargs['after']))
            result = await _method(**kwargs)
            active = False
            return result
        setattr(c.services[name], METHODS[name], wrapped)
    await read(c, projects_limit=3, leads_limit=4, activities_limit=5, history_limit=6)
    assert calls == [('projects', 3, None), ('leads', 4, None), ('activities', 5, None), ('history', 6, None)]
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
@pytest.mark.parametrize('fault', ['error', 'cancel'])
async def test_child_failure_never_returns_partial_card_or_owns_transaction(card_case, section, fault):
    c = card_case
    error = RuntimeError('private database details') if fault == 'error' else asyncio.CancelledError()
    setattr(c.services[section], METHODS[section], AsyncMock(side_effect=error))
    with pytest.raises(ConstructionClientCardReadOperationError if fault == 'error' else asyncio.CancelledError) as caught:
        await read(c)
    assert 'private database details' not in str(caught.value)
    no_writes(c)


@pytest.mark.asyncio
async def test_factory_uses_same_session_for_every_section_and_repository(card_case):
    c = card_case
    service = build_construction_client_card_read_service(session=c.session, now_provider=lambda: c.now)
    assert service.client_service.repository.session is c.session and service.access_repository.session is c.session
    assert all(value.session is c.session and value.repository.session is c.session for value in service.sections.values())
    no_writes(c)


@pytest.mark.asyncio
async def test_each_section_has_its_own_cursor_and_continuation(card_case):
    c = card_case
    project = SimpleNamespace(**{**vars(c.project), 'id': UUID(int=49)})
    lead = SimpleNamespace(**{**vars(c.lead), 'id': UUID(int=59)})
    activity = SimpleNamespace(**{**vars(c.activity), 'id': UUID(int=71)})
    record = replace(c.activity_record, activity=activity)
    event = replace(c.event, id=UUID(int=79))
    lists = {'projects': ('list_projects_for_client', c.project, project),
        'leads': ('list_active_leads', c.lead, lead),
        'activities': ('list_planned_activities_for_client', c.activity_record, record),
        'history': ('list_history_for_client', c.event, event)}
    for name, (method, first, second) in lists.items():
        getattr(c.repositories[name], method).return_value = [first, second]
    limits = {name+'_limit': 1 for name in SECTIONS}
    first = await read(c, **limits)
    assert all(getattr(first, name).next_cursor is not None for name in SECTIONS)
    for name, (method, _, second) in lists.items():
        getattr(c.repositories[name], method).return_value = [second]
    second = await read(c, **limits, **{name+'_after': getattr(first, name).next_cursor for name in SECTIONS})
    for name in SECTIONS:
        assert getattr(first, name).items[0]['id'] != getattr(second, name).items[0]['id']
        assert getattr(second, name).next_cursor is None
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('section', SECTIONS)
@pytest.mark.parametrize('fault', ['foreign', 'extra'])
async def test_assembled_public_projection_is_revalidated(card_case, section, fault):
    c = card_case
    original = getattr(c.services[section], METHODS[section])
    async def corrupt(**kwargs):
        page = await original(**kwargs)
        page.items[0]['tenant_id' if fault == 'foreign' else 'private_internal'] = str(uuid4())
        return page
    setattr(c.services[section], METHODS[section], corrupt)
    with pytest.raises(ConstructionClientCardReadOperationError): await read(c)
    no_writes(c)


@pytest.mark.parametrize('section', SECTIONS)
def test_mixed_sessions_are_rejected(card_case, section):
    c = card_case
    c.services[section].session = object()
    with pytest.raises(ValueError): ConstructionClientCardReadService(**c.options)
