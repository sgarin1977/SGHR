"""Real local membership/grant/audit commit and rollback, injected lock receipt.

Local SQLite cannot prove native PostgreSQL advisory-lock concurrency.
"""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from database.repositories.platform_membership_serialization import PlatformMembershipSerializationReceipt as Receipt
from services.construction.construction_organization_removal_workflow import (
    ConstructionOrganizationRemovalWorkflow as Workflow, build_construction_organization_removal_workflow,
)
from services.platform_membership import PlatformMembershipRemovalError as Error, PlatformMembershipRemovalDeniedError
from test_construction_1a_organization_removal_service import organization_removal, snapshot, events
from test_construction_1a_platform_membership_service import removal
from test_construction_1a_platform_membership_repository import membership_store


@pytest.fixture
def removal_workflow(organization_removal):
    c = organization_removal
    c.bridge.get_transaction = c.session.get_transaction
    c.bridge.get_nested_transaction = c.session.get_nested_transaction
    before_statements = len(c.bridge.statements)
    async def lock(*, tenant_id, user_id):
        assert len(c.bridge.statements) == before_statements  # Before any user/grant row lock.
        assert tenant_id == c.a and user_id in (c.actor_id, c.user_id)
        c.serialization_complete = True
        return Receipt(tenant_id, user_id, c.session.get_transaction())
    c.serialization = SimpleNamespace(session=c.bridge, lock_member=AsyncMock(side_effect=lock))
    c.workflow = Workflow(session=c.bridge, removal_service=c.operation, serialization_repository=c.serialization)
    return c


async def run(c, **values):
    return await c.workflow.remove_from_organization(**dict(
        dict(actor=c.actor, user_id=c.user_id, trace_id='organization-workflow'), **values))


@pytest.mark.asyncio
async def test_one_commit_covers_platform_membership_all_effective_grants_and_audit(removal_workflow):
    c = removal_workflow
    authorize = c.authorization.authorize_removal.side_effect
    async def authorized(**values):
        assert c.serialization_complete is True
        return await authorize(**values)
    c.authorization.authorize_removal.side_effect = authorized
    result = await run(c)
    c.bridge.commit.assert_awaited_once(); c.bridge.rollback.assert_not_called()
    assert not c.session.in_transaction() and result.changed
    c.session.expire_all()
    assert c.user.tenant_id is None and c.roles['client'].status == 'revoked'
    assert c.roles['foreign'].status == c.roles['global_role'].status == 'active'
    for name in ('tenant','project','document'): assert c.grants[name].status == 'revoked'
    assert c.grants['foreign'].status == c.grants['other_user'].status == 'active'
    assert len(events(c)) == 4


@pytest.mark.asyncio
async def test_fresh_authorized_repeat_commits_without_new_changes_or_audit(removal_workflow):
    c = removal_workflow
    await run(c)
    before = snapshot(c)
    c.serialization.lock_member.side_effect = lambda **kw: Receipt(kw['tenant_id'], kw['user_id'], c.session.get_transaction())
    result = await run(c)
    assert not result.changed and result.membership.audit_id is None
    assert c.authorization.authorize_removal.await_count == 2
    assert c.serialization.lock_member.await_count == 4 and c.bridge.commit.await_count == 2
    assert snapshot(c) == before and len(events(c)) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['serialization', 'lock_receipt', 'denied', 'audit_after_flush', 'commit', 'cancel_lock', 'cancel_commit',
    'transaction_replaced', 'invalid_result', 'missing_grant_audit', 'foreign_membership', 'duplicate_audit'])
async def test_failures_restore_membership_grants_and_real_audit_as_one_unit(removal_workflow, phase):
    c = removal_workflow
    before = snapshot(c)
    if phase == 'serialization': c.serialization.lock_member.side_effect = RuntimeError('lock failed')
    elif phase == 'lock_receipt': c.serialization.lock_member.side_effect = lambda **kw: Receipt(c.b, c.user_id, c.session.get_transaction())
    elif phase == 'denied': c.authorization.authorize_removal.side_effect = PlatformMembershipRemovalDeniedError('denied')
    elif phase == 'audit_after_flush':
        real = c.events.create_event
        async def audited(**values):
            event = await real(**values)
            if values['event_type'] == 'construction_access_grant_revoked':
                assert len(events(c)) >= 2
                raise RuntimeError('audit failed after real flush')
            return event
        c.events.create_event = audited
    elif phase in ('commit','cancel_commit'):
        async def fail_commit():
            assert c.user.tenant_id is None and len(events(c)) == 4
            raise asyncio.CancelledError() if phase == 'cancel_commit' else RuntimeError('commit failed before persistence')
        c.bridge.commit.side_effect = fail_commit
    elif phase == 'cancel_lock': c.serialization.lock_member.side_effect = asyncio.CancelledError()
    else:
        original = c.operation.remove_from_organization
        async def altered(**values):
            result = await original(**values)
            if phase == 'transaction_replaced':
                c.bridge.get_transaction = lambda: SimpleNamespace(is_active=True)
                return result
            if phase == 'invalid_result': return True
            if phase == 'missing_grant_audit': return replace(result, grant_audit_ids=())
            if phase == 'foreign_membership': return replace(result, membership=replace(result.membership, tenant_id=c.b))
            return replace(result, grant_audit_ids=(result.grant_audit_ids[0],)*len(result.grant_audit_ids))
        c.operation.remove_from_organization = altered
    expected = asyncio.CancelledError if phase.startswith('cancel_') else Error
    with pytest.raises(expected): await run(c)
    assert c.bridge.rollback.await_count >= 1
    assert snapshot(c) == before and len(events(c)) == 0
    if phase not in ('commit','cancel_commit'): c.bridge.commit.assert_not_called()


@pytest.mark.asyncio
async def test_expired_authority_after_wait_is_checked_before_any_membership_mutation(removal_workflow):
    c = removal_workflow
    before = snapshot(c)
    async def waited(**kwargs):
        c.now = c.authority.valid_until
        return Receipt(kwargs['tenant_id'], kwargs['user_id'], c.session.get_transaction())
    c.serialization.lock_member.side_effect = waited
    with pytest.raises(PlatformMembershipRemovalDeniedError): await run(c)
    assert snapshot(c) == before and not events(c)
    c.bridge.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['no_transaction', 'nested', 'inactive_actor', 'bad_user', 'bad_trace'])
async def test_invalid_boundary_never_requests_a_lock_or_commit(removal_workflow, case):
    c = removal_workflow
    values = {}
    if case == 'no_transaction': c.session.rollback()
    elif case == 'nested': c.session.begin_nested()
    elif case == 'inactive_actor': values['actor'] = replace(c.actor, status='blocked')
    elif case == 'bad_user': values['user_id'] = str(c.user_id)
    else: values['trace_id'] = ' '
    with pytest.raises((Error, ValueError)): await run(c, **values)
    c.serialization.lock_member.assert_not_called(); c.bridge.commit.assert_not_called()
    c.bridge.rollback.assert_awaited_once()


def test_constructor_and_factory_require_shared_explicit_authorization(removal_workflow):
    c = removal_workflow
    with pytest.raises(ValueError): Workflow(session=c.bridge, removal_service=c.operation,
        serialization_repository=SimpleNamespace(session=object(), lock_member=AsyncMock()))
    with pytest.raises(TypeError): build_construction_organization_removal_workflow(session=c.bridge)
    c.bridge.get_bind = lambda: c.session.get_bind()
    factory = build_construction_organization_removal_workflow(session=c.bridge,
        authorization_service=c.authorization, now_provider=lambda: c.now)
    assert factory.session is factory.service.session is factory.serialization.session is c.bridge
    assert factory.service.authorization is c.authorization



@pytest.mark.asyncio
async def test_locked_platform_preparation_runs_after_barriers_before_core_user_locks(removal_workflow):
    c = removal_workflow
    statements = len(c.bridge.statements)
    async def prepare(**values):
        assert values == {'actor': c.actor, 'target_user_id': c.user_id}
        assert c.serialization.lock_member.await_count == len({c.actor_id,c.user_id})
        assert len(c.bridge.statements) == statements
    c.authorization.prepare_removal = AsyncMock(side_effect=prepare)
    await run(c)
    c.authorization.prepare_removal.assert_awaited_once()
    c.bridge.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError('authority unavailable'), asyncio.CancelledError()])
async def test_preparation_failure_or_cancel_rolls_back_before_membership_mutation(removal_workflow, failure):
    c = removal_workflow
    c.authorization.prepare_removal = AsyncMock(side_effect=failure)
    with pytest.raises(asyncio.CancelledError if isinstance(failure,asyncio.CancelledError) else Error):
        await run(c)
    c.authorization.authorize_removal.assert_not_awaited()
    c.bridge.commit.assert_not_called()
    assert c.bridge.rollback.await_count >= 1
