from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from database.repositories.construction_address_sources import ConstructionAddressSource
from services.construction_address_matching import ConstructionAddressCandidate, rank_construction_address_suggestions
from services.construction_address_suggestions import (
    ConstructionAddressSuggestionsService as Service, ConstructionAddressSuggestionOperationError,
    build_construction_address_suggestions_service,
)
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_address_sources_repository import source_case, real_sources


@pytest.fixture
def suggestions():
    c = SimpleNamespace(tenant=uuid4(), user=uuid4(), client=uuid4(), active=True,
        now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc), sources=[], read_calls=[], on_read=None)
    c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status='active')
    c.context = ConstructionAccessContext(c.user, c.tenant, ('OWNER',), ('construction.projects.read',),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    c.session = SimpleNamespace(in_transaction=lambda: c.active, no_autoflush=nullcontext(), execute=AsyncMock(),
        add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    c.grant = SimpleNamespace(role='OWNER', scope_type='tenant', scope_id=c.tenant, status='active', expires_at=None)
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    async def iterate(**options):
        c.read_calls.append(options)
        for row in c.sources:
            if c.on_read is not None:
                await c.on_read(row)
            yield row
    c.repo = SimpleNamespace(session=c.session, iter_address_sources=iterate)
    c.service = Service(session=c.session, repository=c.repo, access_repository=c.access, now_provider=lambda: c.now)
    return c


def source(c, number=1, address='Rua das Flores 12', **changes):
    return ConstructionAddressSource(**dict(dict(project_id=UUID(int=number), tenant_id=c.tenant,
        project_name='Project '+str(number), project_status='draft', address_raw=address,
        address_formatted=None, client_id=None, client_name=None), **changes))


async def read(c, **changes):
    return await c.service.suggest_addresses(**dict(dict(actor=c.actor, access_context=c.context,
        query='Rua das Flores 12'), **changes))


def no_writes(c):
    for name in ('add', 'flush', 'commit', 'rollback'):
        getattr(c.session, name).assert_not_called()


@pytest.mark.asyncio
async def test_fresh_authorization_and_client_deduplication_preserve_the_best_matching_address(suggestions):
    c = suggestions
    c.sources = [source(c, 1, 'Rua das Florez 12', client_id=c.client, client_name='Existing client'),
        source(c, 2, '  Rua das Flores, 12  ', client_id=c.client, client_name='Existing client')]
    result = await read(c)
    assert len(result) == 3
    client = next(item for item in result if item.entity_type == 'client')
    assert client.entity_id == c.client and client.address_raw == '  Rua das Flores, 12  ' and client.match_kind == 'exact'
    assert [item.match_kind for item in result] == ['exact', 'exact', 'fuzzy']
    assert c.read_calls == [dict(tenant_id=c.tenant, allowed_project_ids=None)]
    assert c.access.has_active_platform_membership.await_count == 2
    no_writes(c)


@pytest.mark.asyncio
async def test_exact_match_late_in_the_stream_beats_earlier_fuzzy_matches_and_memory_is_bounded(suggestions, monkeypatch):
    c = suggestions
    c.sources = [source(c, number, 'Rua das Florez 12') for number in range(1, 101)]
    c.sources.append(source(c, 101))
    import services.construction_address_suggestions as module
    original = module.select_construction_address_matches
    observed = []
    def select(**options):
        observed.append(len(options['candidates']))
        return original(**options)
    monkeypatch.setattr(module, 'select_construction_address_matches', select)
    result = await read(c)
    assert len(result) == 10 and result[0].entity_id == UUID(int=101) and result[0].match_kind == 'exact'
    assert max(observed) <= 12
    no_writes(c)


@pytest.mark.asyncio
async def test_incremental_results_equal_full_ranking_including_duplicate_normalized_address_ties(suggestions):
    c = suggestions
    c.sources = [source(c, n, 'Rua das Flores 12', client_id=c.client, client_name='Client',
        address_formatted='Rua das Flores 12 '+('Lisboa' if n % 2 else 'Lisbon')) for n in range(1, 35)]
    candidates = []
    for row in c.sources:
        candidates.extend([ConstructionAddressCandidate('project', row.project_id, c.tenant, row.project_name,
            row.address_raw, row.address_formatted), ConstructionAddressCandidate('client', c.client, c.tenant,
                row.client_name, row.address_raw, row.address_formatted)])
    assert await read(c) == rank_construction_address_suggestions(tenant_id=c.tenant, query='Rua das Flores 12', candidates=candidates)
    no_writes(c)


def project_scope(c):
    c.context = ConstructionAccessContext(c.user, c.tenant, ('PROJECT_MANAGER',), ('construction.projects.read',),
        (ConstructionGrantContext('PROJECT_MANAGER', 'project', UUID(int=7)),))
    c.grant.role, c.grant.scope_type, c.grant.scope_id = 'PROJECT_MANAGER', 'project', UUID(int=7)


@pytest.mark.asyncio
async def test_project_scope_is_sent_to_sql_and_does_not_expose_tenant_client_card_names(suggestions):
    c = suggestions
    project_scope(c)
    c.sources = [source(c, 7, client_id=c.client, client_name='Tenant client name')]
    result = await read(c)
    assert len(result) == 1 and result[0].entity_type == 'project' and result[0].label == 'Project 7'
    assert c.read_calls == [dict(tenant_id=c.tenant, allowed_project_ids=(UUID(int=7),))]
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['actor_inactive', 'wrong_actor', 'wrong_tenant', 'no_permissions', 'document_only', 'no_grants'])
async def test_bad_initial_context_is_denied_before_repository_or_membership_queries(suggestions, case):
    c = suggestions
    if case == 'actor_inactive':
        c.actor.status = 'blocked'
    else:
        grants = () if case == 'no_grants' else (ConstructionGrantContext('OBSERVER', 'document', uuid4()),) if case == 'document_only' else c.context.grants
        c.context = replace(c.context, user_id=uuid4() if case == 'wrong_actor' else c.user,
            tenant_id=uuid4() if case == 'wrong_tenant' else c.tenant,
            roles=('OBSERVER',) if case == 'document_only' else c.context.roles,
            permissions=() if case == 'no_permissions' else c.context.permissions, grants=grants)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    assert not c.read_calls
    c.access.has_active_platform_membership.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['membership', 'revoked', 'expired', 'wrong_tenant'])
async def test_fresh_membership_and_scope_are_required_even_with_a_valid_initial_context(suggestions, case):
    c = suggestions
    if case == 'membership': c.access.has_active_platform_membership.return_value = False
    elif case == 'revoked': c.grant.status = 'revoked'
    elif case == 'expired': c.grant.expires_at = c.now
    else: c.grant.scope_id = uuid4()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    assert not c.read_calls
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['membership_lost', 'grant_revoked', 'grant_expired', 'scope_changed'])
async def test_permission_loss_during_read_never_returns_partial_or_stale_suggestions(suggestions, case):
    c = suggestions
    c.sources = [source(c)]
    c.grant.expires_at = c.now+timedelta(minutes=1)
    async def during(row):
        if case == 'membership_lost': c.access.has_active_platform_membership.return_value = False
        elif case == 'grant_revoked': c.grant.status = 'revoked'
        elif case == 'grant_expired': c.now += timedelta(minutes=2)
        else:
            c.grant.role, c.grant.scope_type, c.grant.scope_id = 'PROJECT_MANAGER', 'project', row.project_id
    c.on_read = during
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['foreign_source', 'out_of_scope', 'malformed'])
async def test_invalid_source_cannot_escape_the_service_boundary(suggestions, case):
    c = suggestions
    if case == 'out_of_scope': project_scope(c)
    c.sources = [source(c, tenant_id=uuid4()) if case == 'foreign_source' else source(c, 9) if case == 'out_of_scope' else object()]
    with pytest.raises((ConstructionAddressSuggestionOperationError, ConstructionScopeDeniedError)):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('query', [None, '', '   ', '...'])
async def test_empty_input_has_no_source_scan_but_still_checks_current_authorization(suggestions, query):
    c = suggestions
    assert await read(c, query=query) == ()
    assert not c.read_calls and c.access.has_active_platform_membership.await_count == 2
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [dict(limit=0), dict(limit=11), dict(limit=True), dict(query=42)])
async def test_invalid_input_cannot_read_sources(suggestions, options):
    c = suggestions
    with pytest.raises(ValueError): await read(c, **options)
    assert not c.read_calls
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['storage', 'storage_value_error', 'transaction_ended', 'clock'])
async def test_failures_do_not_write_or_expose_private_storage_details(suggestions, case):
    c = suggestions
    if case == 'clock': c.service.now_provider = lambda: datetime(2026, 10, 16)
    else:
        async def broken(**options):
            if case == 'storage': raise RuntimeError('private-database-connection-string')
            if case == 'storage_value_error': raise ValueError('private-database-connection-string')
            c.active = False
            if False: yield
        c.repo.iter_address_sources = broken
    with pytest.raises(ConstructionAddressSuggestionOperationError) as caught:
        await read(c)
    assert 'private' not in str(caught.value)
    no_writes(c)


@pytest.mark.parametrize('dependency', ['repository', 'access_repository'])
def test_shared_session_is_required(suggestions, dependency):
    c = suggestions
    values = dict(session=c.session, repository=c.repo, access_repository=c.access)
    values[dependency] = object()
    with pytest.raises(ValueError): Service(**values)


def test_factory_reuses_the_supplied_session(suggestions):
    c = suggestions
    service = build_construction_address_suggestions_service(session=c.session)
    assert service.session is service.repository.session is service.access_repository.session is c.session


@pytest.mark.asyncio
async def test_real_local_source_query_and_service_preserve_terminal_projects_and_client_deduplication(real_sources):
    c = real_sources
    actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status='active')
    context = ConstructionAccessContext(c.user, c.tenant, ('OWNER',), ('construction.projects.read',),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    grant = SimpleNamespace(role='OWNER', scope_type='tenant', scope_id=c.tenant, status='active', expires_at=None)
    access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[grant]))
    service = Service(session=c.session, repository=c.repo, access_repository=access)
    result = await service.suggest_addresses(actor=actor, access_context=context, query='Rua das Flores 12')
    assert len(result) == 7
    assert {item.entity_id.int for item in result if item.entity_type=='project'} == {10, 20, 30, 40, 50, 70}
    assert [item.entity_id for item in result if item.entity_type=='client'] == [c.client]
    assert {item.label for item in result} >= {'completed', 'cancelled'}
    assert not c.sync.dirty and not c.sync.new
    no_writes(c)
