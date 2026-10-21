"""Real local SQL transaction composition, not public policy/native concurrency acceptance."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, Uuid, select
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from database.models import ConstructionAccessGrant as Grant, EventLog, User, UserRoleMapping, RoleScope
from services.api_identity import ApiIdentityAccessError
from services.construction_organization_removal import (
    ConstructionOrganizationRemovalService, ConstructionOrganizationRemovalResult,
    build_construction_organization_removal_service,
)
from services.platform_membership import PlatformMembershipRemovalDeniedError, PlatformMembershipRemovalError
from test_construction_1a_platform_membership_service import removal
from test_construction_1a_platform_membership_repository import membership_store


@pytest.fixture
def organization_removal(removal):
    c = removal
    metadata = MetaData()
    for name in ('users', 'tenants'): Table(name, metadata, Column('id', Uuid, primary_key=True))
    table = Grant.__table__.to_metadata(metadata)
    for constraint in list(table.constraints):
        if isinstance(constraint, ExcludeConstraint): table.constraints.remove(constraint)
    table.create(c.session.bind)
    c.grants = {}
    def seed(name, *, role='SURVEYOR', scope='project', tenant=None, user=None, expires=None, revoked=False):
        tenant = tenant or c.a
        row = Grant(id=uuid4(), tenant_id=tenant, user_id=user or c.user_id, role=role,
            scope_type=scope, scope_id=tenant if scope == 'tenant' else uuid4(), expires_at=expires,
            granted_by_user_id=c.actor_id, granted_at=c.now-timedelta(days=3),
            status='revoked' if revoked else 'active', revoked_by_user_id=c.actor_id if revoked else None,
            revoked_at=c.now-timedelta(days=1) if revoked else None)
        c.session.add(row); c.grants[name] = row
    seed('tenant', role='ADMIN', scope='tenant')
    seed('project')
    seed('document', role='OBSERVER', scope='document', expires=c.now+timedelta(hours=1))
    seed('expired', role='EXECUTOR', expires=c.now)
    seed('historical', role='ESTIMATOR', revoked=True)
    seed('foreign', role='ADMIN', scope='tenant', tenant=c.b)
    seed('other_user', role='EXECUTOR', user=c.other_id)
    c.session.commit(); c.session.begin()
    original_execute = c.bridge.execute
    async def native_timestamps(statement):
        result = await original_execute(statement)
        is_grant = (getattr(getattr(statement,'table',None),'name',None) == 'construction_access_grants'
            or any(item.get('entity') is Grant for item in getattr(statement,'column_descriptions',())))
        if not is_grant: return result
        def aware(row):
            if row is None: return None
            values = {column.name:getattr(row,column.name) for column in Grant.__table__.c}
            for name in ('expires_at','granted_at','revoked_at'):
                value=values[name]
                if value is not None and value.tzinfo is None: values[name]=value.replace(tzinfo=timezone.utc)
            return SimpleNamespace(**values)
        # Only decoding is adapted. SQL mutations, FKs, audit, and rollback
        # execute against the real local database in the shared transaction.
        return SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[aware(row) for row in result.scalars().all()]),
            scalar_one_or_none=lambda:aware(result.scalar_one_or_none()))
    c.bridge.execute = native_timestamps
    c.bridge.commit = AsyncMock(side_effect=c.session.commit)
    c.operation = build_construction_organization_removal_service(session=c.bridge,
        authorization_service=c.authorization, now_provider=lambda: c.now)
    c.operation.grants.list_active_grants = AsyncMock(wraps=c.operation.grants.list_active_grants)
    c.operation.grants.revoke_effective_user_grants = AsyncMock(wraps=c.operation.grants.revoke_effective_user_grants)
    c.operation.grants.get_grant_for_update = AsyncMock(wraps=c.operation.grants.get_grant_for_update)
    c.events = c.operation.events
    return c


async def run(c, **changes):
    if not c.session.in_transaction(): c.session.begin()
    return await c.operation.remove_from_organization(**dict(dict(actor=c.actor, user_id=c.user_id, trace_id='organization-removal'), **changes))


def snapshot(c):
    return {model.__tablename__: c.session.execute(select(model.__table__).order_by(model.id)).all()
        for model in (User, UserRoleMapping, RoleScope, Grant, EventLog)}


def events(c):
    return c.session.scalars(select(EventLog).order_by(EventLog.id)).all()


@pytest.mark.asyncio
async def test_platform_membership_and_all_scope_grants_share_one_uncommitted_transaction(organization_removal):
    c = organization_removal
    before = snapshot(c)
    result = await run(c)
    assert isinstance(result, ConstructionOrganizationRemovalResult) and result.changed
    assert result.membership.changed and result.membership.primary_tenant_cleared and result.membership.active_role_cleared
    assert set(result.revoked_grant_ids) == {c.grants[name].id for name in ('tenant', 'project', 'document')}
    assert c.user.tenant_id is None and c.user.active_role is None and c.user.status == 'active'
    assert c.roles['client'].status == c.roles['specialist'].status == 'revoked'
    assert c.roles['global_role'].status == c.roles['foreign'].status == c.roles['other'].status == 'active'
    for name in ('tenant', 'project', 'document'):
        c.session.refresh(c.grants[name])
        assert c.grants[name].status == 'revoked' and c.grants[name].revoked_by_user_id == c.actor_id
        assert c.grants[name].revoked_at.replace(tzinfo=timezone.utc) == c.now
    for name in ('expired', 'foreign', 'other_user'):
        c.session.refresh(c.grants[name]); assert c.grants[name].status == 'active'
    assert c.grants['historical'].status == 'revoked'
    records = events(c)
    assert len(records) == 4
    assert {record.id for record in records} == {result.membership.audit_id, *result.grant_audit_ids}
    assert sum(record.event_type == 'platform_tenant_membership_removed' for record in records) == 1
    for record in records:
        assert record.tenant_id == c.a and record.user_id == c.actor_id and record.trace_id == 'organization-removal'
        if record.event_type == 'construction_access_grant_revoked':
            assert record.entity_id in result.revoked_grant_ids and record.entity_type == 'construction_access_grant'
            assert record.payload['team_action'] == 'remove_from_organization'
            assert record.payload['target_user_id'] == str(c.user_id) and len(record.payload['changes']) == 3
    c.bridge.commit.assert_not_called(); c.bridge.rollback.assert_not_called()
    assert c.session.in_transaction()
    await c.bridge.rollback()
    assert snapshot(c) == before  # Caller rollback restores Core, grants and every audit.


@pytest.mark.asyncio
async def test_caller_commit_persists_the_whole_operation_and_repeat_is_authorized_noop(organization_removal):
    c = organization_removal
    first = await run(c)
    await c.bridge.commit()
    c.session.expire_all()
    before = snapshot(c)
    second = await run(c)
    assert not second.changed and second.revoked_grant_ids == second.grant_audit_ids == ()
    assert second.membership.audit_id is None and snapshot(c) == before
    assert c.authorization.authorize_removal.await_count == 2
    assert len(events(c)) == 4 and c.bridge.commit.await_count == 1
    with pytest.raises(FrozenInstanceError): first.revoked_grant_ids = ()


@pytest.mark.asyncio
async def test_explicit_platform_authorization_covers_all_tenant_role_names_but_not_global_roles(organization_removal):
    c = organization_removal
    extra = []
    for name in ('admin','support'):
        role = UserRoleMapping(id=uuid4(),tenant_id=c.a,user_id=c.user_id,role=name,status='active',granted_by=c.actor_id)
        c.session.add(role);c.session.flush()
        c.session.add(RoleScope(id=uuid4(),tenant_id=c.a,user_id=c.user_id,user_role_id=role.id,role=name,scope_type='tenant',status='active'))
        extra.append(role.id)
    c.session.commit()
    result = await run(c)
    assert set(extra) <= set(result.membership.revoked_role_ids)
    c.session.refresh(c.roles['global_role'])
    assert c.roles['global_role'].role == 'admin' and c.roles['global_role'].tenant_id is None and c.roles['global_role'].status == 'active'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['grant_only', 'membership_only', 'no_changes'])
async def test_preexisting_partial_access_is_removed_without_spurious_audits(organization_removal, case):
    c = organization_removal
    if case in ('grant_only', 'no_changes'):
        # Previously completed Core changes are committed independently as setup.
        await c.service.remove_from_organization(actor=c.actor, user_id=c.user_id)
    if case in ('membership_only', 'no_changes'):
        await c.operation.grants.revoke_effective_user_grants(tenant_id=c.a, user_id=c.user_id,
            revoked_by_user_id=c.actor_id, as_of=c.now)
    c.session.commit()
    count = len(events(c)); before = snapshot(c)
    result = await run(c)
    assert result.membership.changed == (case == 'membership_only')
    assert bool(result.revoked_grant_ids) == (case == 'grant_only')
    assert len(events(c)) == count + (3 if case == 'grant_only' else 1 if case == 'membership_only' else 0)
    if case == 'no_changes': assert not result.changed and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['denied', 'wrong_receipt', 'expired', 'expiry_after_membership', 'expiry_after_grants',
    'membership_audit', 'first_grant_audit', 'later_grant_audit', 'bad_grant_audit_receipt', 'bulk', 'missing_revocation',
    'duplicate_revocation', 'foreign_revocation', 'history_changed', 'remaining_grant', 'remaining_membership', 'cancelled'])
async def test_failure_at_every_phase_restores_core_grants_and_mandatory_audit(organization_removal, failure):
    c = organization_removal
    before = snapshot(c)
    original_event = c.events.create_event
    c.events.create_event = AsyncMock(wraps=original_event)
    if failure == 'denied': c.authorization.authorize_removal.side_effect = PlatformMembershipRemovalDeniedError('private denial')
    elif failure == 'wrong_receipt': c.authorization.authorize_removal.side_effect = None; c.authorization.authorize_removal.return_value = True
    elif failure == 'expired': c.authority = replace(c.authority, valid_until=c.now)
    elif failure in ('membership_audit','first_grant_audit','later_grant_audit','bad_grant_audit_receipt','cancelled','expiry_after_membership'):
        count = [0]
        async def audit(**kw):
            event = await original_event(**kw); count[0] += 1
            if failure == 'expiry_after_membership' and count[0] == 1: c.now = c.authority.valid_until
            if (failure == 'membership_audit' and count[0] == 1) or (failure == 'first_grant_audit' and count[0] == 2) or (failure == 'later_grant_audit' and count[0] == 3):
                raise RuntimeError('private audit detail')
            if failure == 'cancelled' and count[0] == 3: raise asyncio.CancelledError()
            if failure == 'bad_grant_audit_receipt' and count[0] == 2: return SimpleNamespace(**dict(event.__dict__, tenant_id=c.b))
            return event
        c.events.create_event.side_effect = audit
    elif failure == 'bulk': c.operation.grants.revoke_effective_user_grants.side_effect = RuntimeError('private SQL detail')
    elif failure in ('missing_revocation','duplicate_revocation','foreign_revocation','history_changed','expiry_after_grants'):
        original = c.operation.grants.revoke_effective_user_grants._mock_wraps
        async def revoke(**kw):
            rows = await original(**kw)
            if failure == 'expiry_after_grants': c.now = c.authority.valid_until
            elif failure == 'missing_revocation': return rows[:-1]
            elif failure == 'duplicate_revocation': return rows+[rows[0]]
            elif failure == 'foreign_revocation': rows[0].tenant_id = c.b
            else: rows[0].role = 'ESTIMATOR'
            return rows
        c.operation.grants.revoke_effective_user_grants.side_effect = revoke
    elif failure == 'remaining_grant':
        original = c.operation.grants.list_active_grants._mock_wraps
        calls = [0]
        async def active(**kw):
            calls[0] += 1
            return await original(**kw) if calls[0] == 1 else [c.grants['project']]
        c.operation.grants.list_active_grants.side_effect = active
    else: c.operation.grants.has_active_platform_membership = AsyncMock(return_value=True)
    with pytest.raises(asyncio.CancelledError if failure == 'cancelled' else PlatformMembershipRemovalError):
        await run(c)
    assert snapshot(c) == before
    c.bridge.commit.assert_not_called()
    assert c.bridge.rollback.await_count >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('user_id','caller-user'), ('actor',object()), ('trace_id',' '), ('trace_id','x'*129)])
async def test_invalid_inputs_never_reach_core_authorization_or_grant_writes(organization_removal, field, value):
    c = organization_removal; before = snapshot(c)
    with pytest.raises((ValueError, PlatformMembershipRemovalDeniedError)): await run(c, **{field:value})
    assert snapshot(c) == before
    c.authorization.authorize_removal.assert_not_called()
    c.operation.grants.revoke_effective_user_grants.assert_not_called()


@pytest.mark.asyncio
async def test_authority_expiring_while_waiting_for_grant_locks_rolls_back_platform_changes(organization_removal):
    c = organization_removal; before = snapshot(c)
    original = c.operation.grants.get_grant_for_update._mock_wraps
    async def lock(**kw):
        row = await original(**kw); c.now = c.authority.valid_until; return row
    c.operation.grants.get_grant_for_update.side_effect = lock
    with pytest.raises(PlatformMembershipRemovalDeniedError): await run(c)
    assert snapshot(c) == before
    c.operation.grants.revoke_effective_user_grants.assert_not_called()


@pytest.mark.asyncio
async def test_expiring_target_grant_during_locks_is_not_rewritten_as_a_new_revocation(organization_removal):
    c = organization_removal
    original = c.operation.grants.get_grant_for_update._mock_wraps
    async def lock(**kw):
        row = await original(**kw); c.now += timedelta(minutes=40); return row
    c.operation.grants.get_grant_for_update.side_effect = lock
    c.authority = replace(c.authority, valid_until=c.now+timedelta(days=1))
    result = await run(c)
    assert c.grants['document'].id not in result.revoked_grant_ids
    assert set(result.revoked_grant_ids) == {c.grants['tenant'].id,c.grants['project'].id}
    assert len(events(c)) == 3


@pytest.mark.asyncio
async def test_no_transaction_fails_closed_without_opening_or_committing_one(organization_removal):
    c = organization_removal; c.session.rollback()
    with pytest.raises(PlatformMembershipRemovalError):
        await c.operation.remove_from_organization(actor=c.actor, user_id=c.user_id)
    c.authorization.authorize_removal.assert_not_called(); c.bridge.commit.assert_not_called()


@pytest.mark.parametrize('case',['no_authorizer','foreign_authorizer_session','missing_authorize','foreign_grants_session','foreign_audit_session','bad_clock'])
def test_service_wiring_requires_explicit_shared_server_policy_and_no_implicit_permission(organization_removal, case):
    c = organization_removal
    kwargs = dict(session=c.bridge,membership_repository=c.repo,grant_repository=c.operation.grants,
        event_repository=c.events,authorization_service=c.authorization,now_provider=lambda:c.now)
    if case == 'no_authorizer': kwargs['authorization_service'] = None
    elif case == 'foreign_authorizer_session': kwargs['authorization_service'] = SimpleNamespace(session=object(),authorize_removal=AsyncMock())
    elif case == 'missing_authorize': kwargs['authorization_service'] = SimpleNamespace(session=c.bridge)
    elif case == 'foreign_grants_session': kwargs['grant_repository'] = SimpleNamespace(session=object())
    elif case == 'foreign_audit_session': kwargs['event_repository'] = SimpleNamespace(session=object())
    else: kwargs['now_provider'] = 'caller-clock'
    with pytest.raises(ValueError): ConstructionOrganizationRemovalService(**kwargs)
    assert inspect.signature(build_construction_organization_removal_service).parameters['authorization_service'].default is inspect.Parameter.empty
