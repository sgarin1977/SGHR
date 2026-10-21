"""Real local SQL/ASGI composition; PostgreSQL locks/RLS are not emulated as acceptance."""
from dataclasses import replace
from datetime import timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from database.repositories.platform_organization_permissions import (
    PlatformOrganizationPermissionGrant as Record, PlatformOrganizationPermissionRepository as Repository,
    organization_permission_grants as table,
)
from services.platform_membership import PlatformMembershipRemovalDeniedError as Denied
from services.platform_membership_permissions import resolve_platform_membership_explicit_permissions as explicit
from test_construction_1a_locked_membership_authorization import case, authorize
from test_platform_membership_owner_default import access, wire_sqlite_authorizer
from test_platform_organization_removal_http import removal_http, post, receipt_count
from test_construction_1a_organization_removal_workflow import removal_workflow
from test_construction_1a_organization_removal_service import organization_removal, snapshot, events
from test_construction_1a_platform_membership_service import removal
from test_construction_1a_platform_membership_repository import membership_store

MANAGE = 'platform.organization.memberships.manage'


def record(c, **changes):
    row = Record(uuid4(), c.tenant, c.actor.user_id, MANAGE, 'active', 1,
        uuid4(), c.now-timedelta(days=1), None, None)
    return replace(row, **changes)


@pytest.mark.asyncio
async def test_real_explicit_assignment_allows_tenant_admin_without_template_or_jwt_permission(case):
    c=case; c.actor=replace(c.actor,permissions=(),roles=(),active_role=None)
    c.evidence.grants[0].role='ADMIN'
    c.evidence=replace(c.evidence,role_permissions=(),permission_grants=(record(c),))
    receipt=await authorize(c)
    assert receipt.actor_user_id==c.actor.user_id and receipt.target_user_id==c.target


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing','revoked','foreign_tenant','other_user','role_lost',
    'expired_admin','membership_lost','inactive_actor','only_project','duplicate','malformed',
    'future_grant','untyped_record'])
async def test_explicit_record_never_bypasses_fresh_identity_membership_role_or_scope(case,failure):
    c=case; c.evidence.grants[0].role='ADMIN'; rows=(record(c),)
    if failure=='missing': rows=()
    elif failure=='revoked': rows=(replace(rows[0],status='revoked',row_version=2,
        revoked_by_user_id=uuid4(),revoked_at=c.now),)
    elif failure=='foreign_tenant': rows=(replace(rows[0],tenant_id=uuid4()),)
    elif failure=='other_user': rows=(replace(rows[0],user_id=c.target),)
    elif failure=='role_lost':
        c.evidence.grants[0].role='OBSERVER';c.evidence.grants[0].scope_type='project'
        c.evidence.grants[0].scope_id=c.project
    elif failure=='expired_admin': c.evidence.grants[0].expires_at=c.now
    elif failure=='membership_lost':
        c.evidence.users[0].tenant_id=None
        c.evidence=replace(c.evidence,roles=(c.evidence.roles[1],))
    elif failure=='inactive_actor': c.evidence.users[0].status='blocked'
    elif failure=='only_project':
        c.evidence.grants[0].role='PROJECT_MANAGER'
        c.evidence.grants[0].scope_type='project'; c.evidence.grants[0].scope_id=c.project
    elif failure=='duplicate': rows=(rows[0],replace(rows[0],id=uuid4()))
    elif failure=='future_grant': rows=(replace(rows[0],granted_at=c.now+timedelta(hours=1)),)
    elif failure=='untyped_record': rows=(NS(),)
    else: rows=(replace(rows[0],row_version=True),)
    c.evidence=replace(c.evidence,permission_grants=rows)
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('target_role', ['ADMIN','OWNER'])
async def test_explicit_permission_does_not_raise_admin_delegation_ceiling(case,target_role):
    c=case; c.evidence.grants[0].role='ADMIN'
    c.evidence=replace(c.evidence,grants=(c.evidence.grants[0],c.grant(c.target,target_role)),
        permission_grants=(record(c),))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.parametrize('role', ['SURVEYOR','ESTIMATOR','PROJECT_MANAGER','EXECUTOR','OBSERVER'])
def test_other_roles_have_no_implicit_explicit_grant_policy(case,role):
    c=case
    assert explicit(actor=c.actor,construction_access_context=access(c.actor,role,'project'),
        permission_grants=(record(c),))==frozenset()


@pytest.fixture
def permission_store(membership_store):
    c=membership_store
    c.session.execute(text("CREATE UNIQUE INDEX local_active_permission ON platform_organization_permission_grants"
        "(tenant_id,user_id,permission_code) WHERE status='active'"))
    c.permission_repo=Repository(c.bridge)
    c.now=__import__('datetime').datetime(2026,10,21,12,tzinfo=timezone.utc)
    c.session.commit();c.session.begin()
    return c


async def create(c, *, tenant=None, user=None):
    return await c.permission_repo.create_grant(tenant_id=tenant or c.a,user_id=user or c.user_id,
        granted_by_user_id=c.actor_id,as_of=c.now-timedelta(hours=1))


def permission_snapshot(c):
    return c.session.execute(select(table).order_by(table.c.id)
        .execution_options(schema_translate_map={'public':None})).all()


@pytest.mark.asyncio
async def test_storage_queries_are_exact_tenant_user_and_preserve_historical_rows(permission_store):
    c=permission_store
    own=await create(c); foreign=await create(c,tenant=c.b); other=await create(c,user=c.other_id)
    c.session.commit();c.session.begin()
    rows=await c.permission_repo.list_for_update(tenant_id=c.a,user_ids=(c.user_id,))
    assert rows==(own,)
    before=permission_snapshot(c)
    changed=await c.permission_repo.revoke_user_grants(tenant_id=c.a,user_id=c.user_id,
        revoked_by_user_id=c.actor_id,as_of=c.now)
    assert len(changed)==1 and changed[0].id==own.id and changed[0].row_version==2
    assert changed[0].status=='revoked' and changed[0].revoked_at==c.now
    assert changed[0].granted_at==own.granted_at
    assert await c.permission_repo.revoke_user_grants(tenant_id=c.a,user_id=c.user_id,
        revoked_by_user_id=c.actor_id,as_of=c.now)==()
    after=permission_snapshot(c)
    assert {row.id for row in after}=={own.id,foreign.id,other.id}
    assert [row for row in before if row.id!=own.id]==[row for row in after if row.id!=own.id]
    c.bridge.commit.assert_not_called();c.bridge.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_active_uniqueness_is_not_silently_merged_or_committed(permission_store):
    c=permission_store;await create(c);c.session.commit();c.session.begin()
    with pytest.raises(IntegrityError): await create(c)
    c.session.rollback();c.session.begin()
    assert len(permission_snapshot(c))==1
    c.bridge.commit.assert_not_called()


@pytest.mark.asyncio
async def test_storage_rollback_restores_permission_and_version(permission_store):
    c=permission_store;await create(c);c.session.commit();c.session.begin();before=permission_snapshot(c)
    await c.permission_repo.revoke_user_grants(tenant_id=c.a,user_id=c.user_id,
        revoked_by_user_id=c.actor_id,as_of=c.now)
    c.session.rollback();c.session.begin()
    assert permission_snapshot(c)==before


@pytest.mark.asyncio
async def test_new_explicit_issuance_after_revocation_is_new_historical_record(permission_store):
    c=permission_store;old=await create(c)
    await c.permission_repo.revoke_user_grants(tenant_id=c.a,user_id=c.user_id,
        revoked_by_user_id=c.actor_id,as_of=c.now)
    new=await create(c)
    assert new.id!=old.id and new.row_version==1
    assert len(permission_snapshot(c))==2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['no_transaction','bad_uuid','bad_clock'])
async def test_storage_rejects_missing_transaction_and_noncanonical_input_before_sql(permission_store,failure):
    c=permission_store;before=len(c.bridge.statements)
    if failure=='no_transaction': c.session.rollback()
    elif failure=='bad_uuid': c.a='not-a-server-tenant'
    else: c.now=c.now.replace(tzinfo=None)
    with pytest.raises(ValueError): await create(c)
    assert len(c.bridge.statements)==before


async def wire_native_projection(c, role='OWNER', lower_target=False):
    if lower_target:
        c.grants['tenant'].role='SURVEYOR';c.grants['tenant'].scope_type='project'
        c.grants['tenant'].scope_id=uuid4()
    authorizer=wire_sqlite_authorizer(c,role)
    repo=Repository(c.bridge)
    original=authorizer.repository.prepare_evidence.side_effect
    async def evidence(**values):
        result=await original(**values)
        rows=await repo.list_for_update(tenant_id=c.a,user_ids=tuple(sorted(
            {c.actor_id,c.user_id},key=lambda value:value.int)))
        return replace(result,permission_grants=rows)
    authorizer.repository.prepare_evidence.side_effect=evidence
    return repo,authorizer


@pytest.mark.asyncio
async def test_admin_with_real_scoped_assignment_reaches_http_and_atomic_removal(removal_http):
    c=removal_http;repo,auth=await wire_native_projection(c,'ADMIN',lower_target=True)
    assignment=await repo.create_grant(tenant_id=c.a,user_id=c.actor_id,granted_by_user_id=c.other_id,as_of=c.now)
    target=await repo.create_grant(tenant_id=c.a,user_id=c.user_id,granted_by_user_id=c.actor_id,as_of=c.now)
    c.session.commit();c.session.begin()
    response=await post(c,key='explicit-admin')
    assert response.status_code==200,response.text
    assert response.json()['data']['changed'] is True
    rows={row.id:row for row in permission_snapshot(c)}
    assert rows[assignment.id].status=='active' and rows[assignment.id].row_version==1
    assert rows[target.id].status=='revoked' and rows[target.id].row_version==2
    audit=next(row for row in events(c) if row.event_type=='platform_tenant_membership_removed')
    assert audit.payload['revoked_permission_grant_ids']==[str(target.id)]
    assert c.user.tenant_id is None and receipt_count(c)==1
    before=snapshot(c),permission_snapshot(c)
    replay=await post(c,key='explicit-admin')
    assert replay.status_code==200 and replay.json()['data']==response.json()['data']
    assert (snapshot(c),permission_snapshot(c))==before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['audit','commit'])
async def test_permission_revocation_rolls_back_with_membership_grants_audit_or_commit_failure(removal_http,failure):
    c=removal_http;repo,_=await wire_native_projection(c)
    await repo.create_grant(tenant_id=c.a,user_id=c.user_id,granted_by_user_id=c.actor_id,as_of=c.now)
    c.session.commit();c.session.begin();before=snapshot(c),permission_snapshot(c)
    if failure=='audit':
        original=c.operation.events.create_event
        async def failed(**values):
            row=await original(**values)
            if values['event_type']=='platform_tenant_membership_removed': raise RuntimeError('audit after real SQL')
            return row
        c.operation.events.create_event=failed
    else:
        c.bridge.commit.side_effect=RuntimeError('commit before persistence')
    response=await post(c,key='rollback-permissions')
    assert response.status_code in (500,503),response.text
    assert (snapshot(c),permission_snapshot(c))==before and receipt_count(c)==0


@pytest.mark.asyncio
async def test_removal_clears_permission_only_debris_then_is_noop_and_does_not_revive_after_rejoin(removal):
    c=removal;repo=Repository(c.bridge)
    # First removal leaves no membership/grants. Seed a stale explicit record
    # to prove cleanup does not depend on finding active membership rows.
    await c.service.remove_from_organization(actor=c.actor,user_id=c.user_id)
    c.session.commit();c.session.begin()
    target=await repo.create_grant(tenant_id=c.a,user_id=c.user_id,granted_by_user_id=c.actor_id,as_of=c.now)
    result=await c.service.remove_from_organization(actor=c.actor,user_id=c.user_id)
    assert result.changed and result.revoked_permission_grant_ids==(target.id,) and result.audit_id is not None
    repeat=await c.service.remove_from_organization(actor=c.actor,user_id=c.user_id)
    assert not repeat.changed and repeat.audit_id is None
    c.user.tenant_id=c.a;c.session.flush()
    assert permission_snapshot(c)[0].status=='revoked'
