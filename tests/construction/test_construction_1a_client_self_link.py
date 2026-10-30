import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from database.models import ConstructionClient
from database.repositories.construction.construction_client_user_links import (
    ConstructionClientUserLinkMutation as Mutation, ConstructionClientUserLinkState as State,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_clients import ConstructionClientNotFoundError, ConstructionClientOperationError
from services.construction.construction_client_history_contract import construction_history_event_belongs_to_client
from services.construction.construction_client_self_link import (
    ConstructionClientSelfLinkService as Service, ConstructionClientSelfLinkValidationError,
    PlatformClientSelfIdentityReader, build_construction_client_self_link_service,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
)
from test_construction_1a_client_user_link_repository import link_repo
from test_construction_1a_client_link_model import client_store

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def setup(c):
    c.actor = ApiActorContext(c.target, c.tenant, None, (), 'ru', None, 'active')
    c.context = ConstructionAccessContext(c.target, c.tenant, ('OWNER',), ('construction.projects.edit',),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    c.grant = SimpleNamespace(role='OWNER', scope_type='tenant', scope_id=c.tenant, expires_at=None, status='active')
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(side_effect=lambda **_: [c.grant]))
    c.identity = SimpleNamespace(session=object(), require_active_actor=AsyncMock(side_effect=lambda **kw: kw['actor']))
    c.audit_rows = []
    async def create_event(**kwargs):
        row = SimpleNamespace(id=uuid4(), **kwargs)
        c.audit_rows.append(row)
        return row
    c.events = SimpleNamespace(session=c.session, create_event=AsyncMock(side_effect=create_event))
    c.now = NOW
    c.service = Service(session=c.session, repository=c.repo, access_repository=c.access,
        event_repository=c.events, identity_reader=c.identity, now_provider=lambda: c.now)
    return c


@pytest.fixture
def self_case():
    c = SimpleNamespace(tenant=uuid4(), target=uuid4(), other_user=uuid4(), client_id=uuid4())
    c.session = SimpleNamespace(in_transaction=lambda: True, no_autoflush=nullcontext(),
        rollback=AsyncMock(), commit=AsyncMock(), execute=AsyncMock())
    c.state = State(c.client_id, c.tenant, None, 1)
    c.repo = SimpleNamespace(session=c.session, get_state_for_update=AsyncMock(side_effect=lambda **_: c.state))
    async def mutate(**kw):
        c.state = State(kw['client_id'], kw['tenant_id'], kw['linked_user_id'], kw['expected_row_version']+1)
        return Mutation(c.state, True)
    c.repo.change_linked_user = AsyncMock(side_effect=mutate)
    return setup(c)


async def link(c, **overrides):
    kw = dict(actor=c.actor, access_context=c.context, client_id=c.client_id, expected_row_version=1, trace_id='self-link-test')
    kw.update(overrides)
    return await c.service.link_self(**kw)


def no_commit(c):
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_self_confirmation_links_only_authenticated_actor_and_records_one_correlated_update(self_case):
    c = self_case
    result = await link(c)
    assert result.changed and result.linked_user_id == c.actor.user_id and result.row_version == 2
    assert result.audit_id == c.audit_rows[0].id
    call = c.repo.change_linked_user.await_args.kwargs
    assert call == dict(tenant_id=c.tenant, client_id=c.client_id, expected_row_version=1,
        expected_linked_user_id=None, linked_user_id=c.actor.user_id, as_of=NOW)
    assert c.identity.require_active_actor.await_count == 4
    assert c.access.has_active_platform_membership.await_count == 4
    assert len(c.audit_rows) == 1
    event = c.audit_rows[0]
    assert event.event_type == 'construction_client_updated' and event.entity_type == 'construction_client'
    assert event.user_id == c.actor.user_id and event.tenant_id == c.tenant
    assert event.payload == {'operation':'update', 'changes':[
        {'field':'linked_user_id','old_value':None,'new_value':str(c.target)},
        {'field':'row_version','old_value':1,'new_value':2}], 'reason':None,
        'related_client_id':str(c.client_id), 'confirmation_method':'authenticated_self', 'identity_link_action':'self_link'}
    assert construction_history_event_belongs_to_client(event, tenant_id=c.tenant, client_id=c.client_id)
    c.session.rollback.assert_not_called()
    no_commit(c)


@pytest.mark.asyncio
async def test_no_caller_target_uuid_or_confirmation_boolean_is_accepted(self_case):
    c = self_case
    for supplied in ({'target_user_id':c.other_user}, {'confirmed':True}):
        with pytest.raises(TypeError):
            await link(c, **supplied)
    c.repo.change_linked_user.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_matching_self_link_is_noop_but_stale_version_is_still_conflict(self_case):
    c = self_case
    c.state = replace(c.state, linked_user_id=c.target, row_version=7)
    result = await link(c, expected_row_version=7)
    assert result.changed is False and result.row_version == 7 and result.audit_id is None
    c.repo.change_linked_user.assert_not_called()
    c.events.create_event.assert_not_called()
    with pytest.raises(ConstructionRowVersionConflictError):
        await link(c, expected_row_version=6)
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_normal_self_link_cannot_silently_replace_another_user(self_case):
    c = self_case
    c.state = replace(c.state, linked_user_id=c.other_user)
    with pytest.raises(ConstructionClientSelfLinkValidationError):
        await link(c)
    c.repo.change_linked_user.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_replacement_requires_matching_previous_identity_and_audits_old_and_new(self_case):
    c = self_case
    c.state = replace(c.state, linked_user_id=c.other_user)
    result = await c.service.replace_with_self(actor=c.actor, access_context=c.context,
        client_id=c.client_id, previous_user_id=c.other_user, expected_row_version=1)
    assert result.linked_user_id == c.target and result.row_version == 2
    assert c.repo.change_linked_user.await_args.kwargs['expected_linked_user_id'] == c.other_user
    assert c.audit_rows[0].payload['identity_link_action'] == 'replace_with_self'
    assert c.audit_rows[0].payload['changes'][0]['old_value'] == str(c.other_user)
    no_commit(c)


@pytest.mark.asyncio
async def test_wrong_replacement_base_cannot_change_link(self_case):
    c = self_case
    c.state = replace(c.state, linked_user_id=c.other_user)
    with pytest.raises(ConstructionRowVersionConflictError):
        await c.service.replace_with_self(actor=c.actor, access_context=c.context,
            client_id=c.client_id, previous_user_id=uuid4(), expected_row_version=1)
    c.repo.change_linked_user.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['plain_object','inactive_actor','wrong_context_user','wrong_context_tenant','no_permission','project_scope'])
async def test_initial_authorization_denial_happens_before_resource_lookup(self_case, denial):
    c = self_case
    actor, context = c.actor, c.context
    if denial == 'plain_object': actor = SimpleNamespace(user_id=c.target, tenant_id=c.tenant, status='active')
    elif denial == 'inactive_actor': actor = replace(actor, status='blocked')
    elif denial == 'wrong_context_user': context = replace(context, user_id=c.other_user)
    elif denial == 'wrong_context_tenant': context = replace(context, tenant_id=uuid4())
    elif denial == 'no_permission': context = replace(context, permissions=())
    else: context = replace(context, grants=(ConstructionGrantContext('OWNER','project',uuid4()),))
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await link(c, actor=actor, access_context=context)
    c.repo.get_state_for_update.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['platform_identity','membership','grant_revoked','grant_expired','scope_changed'])
async def test_fresh_authority_overrides_stale_caller_permission(self_case, denial):
    c = self_case
    if denial == 'platform_identity': c.identity.require_active_actor.side_effect = ApiIdentityAccessError('not available')
    elif denial == 'membership': c.access.has_active_platform_membership.return_value = False
    elif denial == 'grant_revoked': c.grant.status = 'revoked'
    elif denial == 'grant_expired': c.grant.expires_at = NOW
    else: c.grant.scope_type, c.grant.scope_id = 'project', uuid4()
    with pytest.raises((ApiIdentityAccessError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await link(c)
    c.repo.get_state_for_update.assert_not_called()
    c.repo.change_linked_user.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_client_does_not_write_or_audit(self_case):
    c = self_case
    c.repo.get_state_for_update.side_effect = None
    c.repo.get_state_for_update.return_value = None
    with pytest.raises(ConstructionClientNotFoundError):
        await link(c)
    c.repo.change_linked_user.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_conditional_repository_conflict_uses_existing_row_version_conflict(self_case):
    c = self_case
    c.repo.change_linked_user.side_effect = None
    c.repo.change_linked_user.return_value = None
    with pytest.raises(ConstructionRowVersionConflictError) as error:
        await link(c)
    assert error.value.status_code == 409 and error.value.code == 'ROW_VERSION_CONFLICT'
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_expiry_while_waiting_for_lock_rejects_before_write(self_case):
    c = self_case
    c.grant.expires_at = NOW + timedelta(seconds=1)
    async def locked(**_):
        c.now += timedelta(seconds=2)
        return c.state
    c.repo.get_state_for_update.side_effect = locked
    with pytest.raises(ConstructionPermissionDeniedError):
        await link(c)
    c.repo.change_linked_user.assert_not_called()


@pytest.mark.asyncio
async def test_permission_loss_after_audit_still_rolls_back(self_case):
    c = self_case
    async def event_then_revoke(**kw):
        c.access.has_active_platform_membership.return_value = False
        return SimpleNamespace(id=uuid4(), **kw)
    c.events.create_event.side_effect = event_then_revoke
    with pytest.raises(ConstructionPermissionDeniedError):
        await link(c)
    c.session.rollback.assert_awaited_once()
    no_commit(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['audit_write','bad_audit_receipt','bad_mutation_receipt','cancelled'])
async def test_mandatory_failure_and_cancellation_roll_back_without_commit(self_case, failure):
    c = self_case
    expected = ConstructionClientOperationError
    if failure == 'audit_write': c.events.create_event.side_effect = RuntimeError('private audit detail')
    elif failure == 'bad_audit_receipt': c.events.create_event.side_effect = lambda **kw: SimpleNamespace(id=uuid4(), **{**kw,'tenant_id':uuid4()})
    elif failure == 'bad_mutation_receipt':
        c.repo.change_linked_user.side_effect = None
        c.repo.change_linked_user.return_value = Mutation(replace(c.state, linked_user_id=c.other_user, row_version=2), True)
    else:
        c.events.create_event.side_effect = asyncio.CancelledError()
        expected = asyncio.CancelledError
    with pytest.raises(expected) as error:
        await link(c)
    assert 'private audit detail' not in str(error.value)
    c.session.rollback.assert_awaited_once()
    no_commit(c)


@pytest.fixture
def sql_self(link_repo):
    c = setup(link_repo)
    c.sync.execute(text('CREATE TABLE client_link_audit(id TEXT PRIMARY KEY, event_type TEXT)'))
    async def event(**kw):
        identifier = uuid4()
        c.sync.execute(text('INSERT INTO client_link_audit VALUES(:id,:kind)'),
            {'id':str(identifier),'kind':kw['event_type']})
        return SimpleNamespace(id=identifier, **kw)
    c.events.create_event.side_effect = event
    return c


@pytest.mark.asyncio
async def test_actual_client_link_and_audit_commit_together_without_platform_writes(sql_self):
    c = sql_self
    result = await link(c)
    assert result.changed and result.row_version == 2
    assert c.sync.execute(select(ConstructionClient.linked_user_id)).scalar_one() == c.target
    assert c.sync.execute(text('SELECT count(*) FROM client_link_audit')).scalar_one() == 1
    assert c.sync.execute(text('SELECT count(*) FROM users')).scalar_one() == 2
    no_commit(c)
    c.sync.commit()
    with Session(c.engine) as other:
        row = other.get(ConstructionClient, c.client_id)
        assert row.linked_user_id == c.target and row.row_version == 2
        assert other.execute(text('SELECT count(*) FROM client_link_audit')).scalar_one() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['audit_after_insert','later_work','final_permission'])
async def test_real_client_update_and_real_audit_insert_roll_back_together(sql_self, failure):
    c = sql_self
    async def rollback(): c.sync.rollback()
    c.session.rollback = AsyncMock(side_effect=rollback)
    original_event = c.events.create_event.side_effect
    async def fail_after_audit(**kw):
        result = await original_event(**kw)
        if failure == 'audit_after_insert': raise RuntimeError('private failure after real insert')
        if failure == 'final_permission': c.access.has_active_platform_membership.return_value = False
        return result
    c.events.create_event.side_effect = fail_after_audit
    if failure == 'later_work':
        await link(c)
        c.sync.rollback()
    else:
        with pytest.raises((ConstructionClientOperationError, ConstructionPermissionDeniedError)):
            await link(c)
    row = c.sync.execute(select(ConstructionClient)).scalar_one()
    assert row.linked_user_id is None and row.row_version == 1
    assert c.sync.execute(text('SELECT count(*) FROM client_link_audit')).scalar_one() == 0


@pytest.mark.asyncio
async def test_platform_reader_refreshes_canonical_status_without_membership_or_telegram_lookup(self_case):
    c = self_case
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE users(id CHAR(32) PRIMARY KEY,status TEXT)')
        conn.exec_driver_sql('INSERT INTO users VALUES(?,?)', (c.target.hex,'active'))
    with Session(engine) as sync:
        statements = []
        async def execute(statement):
            statements.append(statement)
            return sync.execute(statement)
        class Adapter:
            @property
            def no_autoflush(self):
                return sync.no_autoflush
            async def execute(self, statement):
                return await execute(statement)
        adapter = Adapter()
        reader = PlatformClientSelfIdentityReader(adapter)
        assert await reader.require_active_actor(actor=c.actor) == c.actor
        sql = str(statements[0]).lower()
        assert 'users.id' in sql and 'users.status' in sql
        assert 'tenant_id' not in sql and 'user_accounts' not in sql and 'user_roles' not in sql
        sync.execute(text("UPDATE users SET status='blocked'"))
        with pytest.raises(ApiIdentityAccessError):
            await reader.require_active_actor(actor=c.actor)
        sync.rollback()
    engine.dispose()


def test_factory_and_constructor_keep_platform_reads_separate_from_construction_writes(self_case):
    c = self_case
    platform = SimpleNamespace(execute=AsyncMock(), no_autoflush=nullcontext())
    service = build_construction_client_self_link_service(session=c.session, platform_session=platform)
    assert service.repository.session is c.session and service.events.session is c.session
    assert service.access.session is c.session and service.identity.session is platform
    c.identity.session = c.session
    with pytest.raises(ValueError):
        Service(session=c.session, repository=c.repo, access_repository=c.access,
            event_repository=c.events, identity_reader=c.identity)
