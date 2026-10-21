"""Shared-session authorization, real local audit and rollback; no deployed acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, String, Table, Uuid, func, select
from sqlalchemy.dialects.postgresql import JSONB

from database.models import EventLog, User
from database.repositories.event import EventRepository
from services.api_identity import ApiActorContext
from services.platform_membership import (
    PlatformMembershipService, PlatformMembershipRemovalAuthority as Authority,
    PlatformMembershipRemovalError, PlatformMembershipRemovalDeniedError,
    build_platform_membership_service,
)
from test_construction_1a_platform_membership_repository import membership_store


@pytest.fixture
def removal(membership_store):
    c = membership_store
    metadata = MetaData()
    for name in ('tenants', 'users', 'countries'):
        Table(name, metadata, Column('id', Uuid, primary_key=True))
    Table('languages', metadata, Column('code', String(10), primary_key=True))
    table = EventLog.__table__.to_metadata(metadata)
    for column in table.c:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    table.create(c.session.bind)
    c.bridge.add = c.session.add
    c.bridge.flush = AsyncMock(side_effect=c.session.flush)
    c.bridge.rollback = AsyncMock(side_effect=c.session.rollback)
    c.now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    c.actor = ApiActorContext(c.actor_id, c.a, None, (), 'uk', 'UTC', 'active')
    c.authority = Authority(c.actor_id, c.a, c.user_id, c.now+timedelta(hours=1))
    async def authorize(**kwargs):
        assert kwargs == {'actor': c.actor, 'target_user_id': c.user_id}
        assert c.session.in_transaction()
        return c.authority
    c.authorization = SimpleNamespace(session=c.bridge, authorize_removal=AsyncMock(side_effect=authorize))
    c.events = EventRepository(c.bridge)
    c.service = build_platform_membership_service(session=c.bridge, authorization_service=c.authorization,
        now_provider=lambda: c.now)
    return c


async def remove(c, **changes):
    return await c.service.remove_from_organization(**dict(dict(actor=c.actor, user_id=c.user_id,
        trace_id='membership-local-test'), **changes))


def event_count(c):
    return c.session.scalar(select(func.count()).select_from(EventLog))


@pytest.mark.asyncio
async def test_removal_uses_canonical_membership_and_exact_mandatory_audit_without_commit(removal):
    c = removal
    result = await remove(c)
    assert result.changed and result.user_id == c.user_id and result.tenant_id == c.a
    assert set(result.revoked_role_ids) == {c.roles['client'].id, c.roles['specialist'].id}
    assert set(result.revoked_scope_ids) == {c.scopes[key].id for key in ('client', 'specialist', 'historical')}
    assert result.primary_tenant_cleared and result.active_role_cleared
    assert c.user.tenant_id is None and c.user.active_role is None and c.user.status == 'active'
    assert c.roles['foreign'].status == c.roles['global_role'].status == c.roles['other'].status == 'active'
    assert c.roles['suspended'].status == 'suspended'
    assert c.user.language_code == 'uk' and c.user.extra_metadata == {'preserve': True}
    audit = c.session.get(EventLog, result.audit_id)
    assert audit.event_type == 'platform_tenant_membership_removed' and audit.entity_type == 'platform_membership'
    assert audit.user_id == c.actor_id and audit.entity_id == c.user_id and audit.tenant_id == c.a
    assert audit.payload == {'operation': 'platform.membership.remove', 'target_user_id': str(c.user_id),
        'revoked_role_ids': [str(value) for value in result.revoked_role_ids],
        'revoked_scope_ids': [str(value) for value in result.revoked_scope_ids],
        'changes': [*({'field': f'user_roles.{value}.status', 'old_value': 'active', 'new_value': 'revoked'}
                for value in result.revoked_role_ids),
            *({'field': f'role_scopes.{value}.status', 'old_value': 'active', 'new_value': 'revoked'}
                for value in result.revoked_scope_ids),
            {'field': 'users.tenant_id', 'old_value': str(c.a), 'new_value': None},
            {'field': 'users.active_role', 'old_value': 'specialist', 'new_value': None}]}
    assert audit.platform == 'api' and audit.trace_id == 'membership-local-test'
    c.bridge.commit.assert_not_called()
    c.bridge.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_ordered_actor_target_locks_precede_server_authorization(removal):
    c = removal
    original = c.authorization.authorize_removal.side_effect
    async def authorize(**kwargs):
        statements = c.bridge.statements
        assert len(statements) == 2 and all(statement.is_select for statement in statements)
        ids = [next(iter(statement.compile().params.values())) for statement in statements]
        assert ids == sorted({c.actor_id, c.user_id}, key=lambda value: value.int)
        assert all(statement._for_update_arg is not None for statement in statements)
        return await original(**kwargs)
    c.authorization.authorize_removal.side_effect = authorize
    await remove(c)


@pytest.mark.asyncio
async def test_repeat_is_authorized_noop_with_no_new_audit(removal):
    c = removal
    await remove(c)
    assert event_count(c) == 1
    result = await remove(c)
    assert not result.changed and result.audit_id is None
    assert result.revoked_role_ids == result.revoked_scope_ids == ()
    assert not result.primary_tenant_cleared and not result.active_role_cleared
    assert event_count(c) == 1 and c.authorization.authorize_removal.await_count == 2
    c.bridge.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('selection', ['client', 'admin'])
async def test_other_tenant_primary_and_remaining_effective_selection_are_preserved(removal, selection):
    c = removal
    c.user.tenant_id, c.user.active_role = c.b, selection
    c.session.flush()
    result = await remove(c)
    assert c.user.tenant_id == c.b and c.user.active_role == selection
    assert not result.primary_tenant_cleared and not result.active_role_cleared


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['denied', 'missing_receipt', 'wrong_tenant', 'wrong_target', 'wrong_actor', 'expired', 'naive_expiry'])
async def test_authorization_failure_rolls_back_before_membership_writes(removal, case):
    c = removal
    if case == 'denied':
        c.authorization.authorize_removal.side_effect = PlatformMembershipRemovalDeniedError('denied')
    elif case == 'missing_receipt':
        c.authority = True
    else:
        field, value = {'wrong_tenant': ('tenant_id', uuid4()), 'wrong_target': ('target_user_id', uuid4()),
            'wrong_actor': ('actor_user_id', uuid4()), 'expired': ('valid_until', c.now),
            'naive_expiry': ('valid_until', datetime(2026, 10, 17))}[case]
        c.authority = replace(c.authority, **{field: value})
    with pytest.raises(PlatformMembershipRemovalDeniedError):
        await remove(c)
    assert all(statement.is_select for statement in c.bridge.statements)
    c.bridge.rollback.assert_awaited_once()
    assert c.user.tenant_id == c.a and c.roles['client'].status == 'active' and event_count(c) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['audit_error', 'wrong_audit', 'authority_expires_after_flush', 'postcondition'])
async def test_failures_after_real_writes_roll_back_membership_and_audit_together(removal, failure):
    c = removal
    real_event = c.service.events.create_event
    async def event(**values):
        assert c.user.tenant_id is None and c.roles['client'].status == 'revoked'
        audit = await real_event(**values)
        assert event_count(c) == 1
        if failure == 'audit_error':
            raise RuntimeError('mandatory audit failed after flush')
        if failure == 'wrong_audit':
            return SimpleNamespace(id=uuid4(), tenant_id=c.b, user_id=c.actor_id)
        if failure == 'authority_expires_after_flush':
            c.now = c.authority.valid_until
        if failure == 'postcondition':
            c.user.tenant_id = c.a
            c.session.flush()
        return audit
    c.service.events.create_event = event
    with pytest.raises((RuntimeError, PlatformMembershipRemovalError)):
        await remove(c)
    c.bridge.rollback.assert_awaited_once()
    assert c.user.tenant_id == c.a and c.user.active_role == 'specialist'
    assert c.roles['client'].status == c.roles['specialist'].status == 'active'
    assert c.scopes['client'].status == 'active' and c.scopes['client'].revoked_at is None
    assert event_count(c) == 0
    c.bridge.commit.assert_not_called()


@pytest.mark.asyncio
async def test_self_target_uses_one_user_lock_and_does_not_reresolve_revoked_membership(removal):
    c = removal
    c.actor = replace(c.actor, user_id=c.user_id)
    c.authority = replace(c.authority, actor_user_id=c.user_id)
    await remove(c)
    assert c.authorization.authorize_removal.await_count == 1
    assert c.user.status == 'active' and c.user.tenant_id is None
    assert c.session.scalar(select(EventLog.user_id)) == c.user_id


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['inactive_actor', 'missing_actor', 'invalid_target', 'trace', 'transaction'])
async def test_invalid_context_denied_without_membership_mutation(removal, case):
    c = removal
    values = {}
    if case == 'inactive_actor':
        c.session.get(User, c.actor_id).status = 'blocked'
        c.session.flush()
    elif case == 'missing_actor':
        c.actor = replace(c.actor, user_id=uuid4())
    elif case == 'invalid_target':
        values['user_id'] = 'untrusted'
    elif case == 'trace':
        values['trace_id'] = ' '
    elif case == 'transaction':
        c.session.rollback()
    with pytest.raises((ValueError, PlatformMembershipRemovalError)):
        await remove(c, **values)
    assert all(statement.is_select for statement in c.bridge.statements)
    c.bridge.rollback.assert_awaited_once()
    c.bridge.commit.assert_not_called()


@pytest.mark.parametrize('dependency', ['repository', 'event_repository', 'authorization_service'])
def test_shared_session_is_mandatory_and_authorizer_has_no_permissive_default(removal, dependency):
    c = removal
    values = dict(session=c.bridge, repository=c.repo, event_repository=c.events,
        authorization_service=c.authorization)
    values[dependency] = None
    with pytest.raises(ValueError):
        PlatformMembershipService(**values)


@pytest.mark.asyncio
async def test_missing_target_is_checked_after_authorization_and_never_audited(removal):
    c = removal
    c.user_id = uuid4()
    c.authority = replace(c.authority, target_user_id=c.user_id)
    with pytest.raises(PlatformMembershipRemovalError):
        await remove(c)
    c.authorization.authorize_removal.assert_awaited_once()
    assert all(statement.is_select for statement in c.bridge.statements)
    assert event_count(c) == 0


@pytest.mark.asyncio
async def test_invalid_server_clock_cannot_authorize_mutation(removal):
    c = removal
    c.service.now_provider = lambda: datetime(2026, 10, 16)
    with pytest.raises(PlatformMembershipRemovalError):
        await remove(c)
    assert all(statement.is_select for statement in c.bridge.statements)
    c.bridge.rollback.assert_awaited_once()
