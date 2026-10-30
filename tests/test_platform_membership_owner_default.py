"""Approved OWNER defaults, no global grants, real ASGI/SQLite operation composition.

PostgreSQL evidence locks are injected in local integration tests. Their native
implementation remains the existing reviewed repository; no native claim here.
"""
from dataclasses import replace
from datetime import timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select

from database.models import ConstructionAccessGrant as Grant, RoleScope, User, UserRoleMapping
from database.repositories.platform_membership_authorization import MembershipRemovalEvidence as Evidence
from services.api_identity import ApiActorContext
from services.construction.construction_permissions import (
    CONSTRUCTION_PERMISSION_CODES, CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext, ConstructionGrantContext,
)
from services.platform_membership import PlatformMembershipRemovalDeniedError as Denied
from services.platform_membership_authorization import PlatformMembershipRemovalAuthorizer as Authorizer
from services.platform_membership_permissions import (
    PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE as MANAGE,
    require_platform_membership_management,
    resolve_platform_membership_default_permissions as resolve,
)
from test_construction_1a_locked_membership_authorization import case, authorize
from test_platform_organization_removal_http import removal_http, post, receipt_count
from test_construction_1a_organization_removal_workflow import removal_workflow
from test_construction_1a_organization_removal_service import organization_removal, snapshot, events
from test_construction_1a_platform_membership_service import removal
from test_construction_1a_platform_membership_repository import membership_store


def access(actor, role='OWNER', scope='tenant', scope_id=None):
    return ConstructionAccessContext(actor.user_id, actor.tenant_id, (role,),
        tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS[role])),
        (ConstructionGrantContext(role, scope, scope_id or actor.tenant_id),))


def test_owner_default_is_a_platform_permission_policy_not_a_construction_permission(case):
    c = case
    actor = replace(c.actor, permissions=(), roles=(), active_role=None)
    codes = resolve(actor=actor, construction_access_context=access(actor))
    assert codes == frozenset({MANAGE})
    assert require_platform_membership_management(permission_codes=codes) is None
    assert MANAGE not in CONSTRUCTION_PERMISSION_CODES
    assert all(MANAGE not in values for values in CONSTRUCTION_ROLE_PERMISSIONS.values())


@pytest.mark.parametrize('role', ['ADMIN','SURVEYOR','ESTIMATOR','PROJECT_MANAGER','EXECUTOR','OBSERVER'])
def test_other_construction_roles_never_receive_default_even_with_actor_permission_claim(case, role):
    actor = replace(case.actor, permissions=(MANAGE,))
    scope = 'tenant' if role == 'ADMIN' else 'project'
    assert resolve(actor=actor, construction_access_context=access(actor, role, scope)) == frozenset()


@pytest.mark.parametrize('role', ['admin', 'super_admin'])
def test_platform_role_names_cannot_manufacture_a_default(case, role):
    actor = replace(case.actor, active_role=role, roles=(role,), permissions=(MANAGE,))
    context = ConstructionAccessContext(actor.user_id, actor.tenant_id, (), (), ())
    assert resolve(actor=actor, construction_access_context=context) == frozenset()


@pytest.mark.parametrize('failure', ['project_owner','document_owner','foreign_tenant_owner','foreign_user',
    'foreign_context','inactive_actor','forged_permissions','forged_roles','list_grants','invalid_grant','missing_context'])
def test_default_requires_exact_valid_tenant_owner_context(case, failure):
    actor = case.actor
    context = access(actor)
    if failure == 'project_owner': context = access(actor, scope='project', scope_id=uuid4())
    elif failure == 'document_owner': context = access(actor, scope='document', scope_id=uuid4())
    elif failure == 'foreign_tenant_owner': context = access(actor, scope_id=uuid4())
    elif failure == 'foreign_user': actor = replace(actor, user_id=uuid4())
    elif failure == 'foreign_context': context = replace(context, tenant_id=uuid4())
    elif failure == 'inactive_actor': actor = replace(actor, status='blocked')
    elif failure == 'forged_permissions': context = replace(context, permissions=(MANAGE,))
    elif failure == 'forged_roles': context = replace(context, roles=('OWNER','ADMIN'))
    elif failure == 'list_grants': context = replace(context, grants=list(context.grants))
    elif failure == 'invalid_grant': context = replace(context, grants=(NS(role='OWNER',scope_type='tenant',scope_id=actor.tenant_id),))
    else: context = None
    assert resolve(actor=actor, construction_access_context=context) == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize('platform_state', ['no_roles','no_mapping','suspended','revoked','country_scope'])
async def test_fresh_tenant_owner_default_needs_no_manual_permission_assignment(case, platform_state):
    c = case
    c.actor = replace(c.actor, permissions=(), roles=(), active_role=None)
    c.evidence = replace(c.evidence, role_permissions=())
    if platform_state == 'no_roles': c.evidence = replace(c.evidence, roles=())
    elif platform_state in ('suspended','revoked'): c.evidence.roles[0].status=platform_state
    elif platform_state == 'country_scope':
        r = c.evidence.roles[0]
        c.evidence = replace(c.evidence, scopes=(NS(id=uuid4(),tenant_id=c.tenant,user_id=c.actor.user_id,
            role=r.role,status='active',scope_type='country',scope_id=uuid4()),))
    result = await authorize(c)
    assert result.actor_user_id == c.actor.user_id and result.tenant_id == c.tenant
    assert result.target_user_id == c.target


@pytest.mark.asyncio
@pytest.mark.parametrize('platform_role', ['client','admin','super_admin'])
async def test_shared_role_template_or_jwt_permission_is_not_an_explicit_tenant_grant(case, platform_role):
    c = case
    c.actor = replace(c.actor, active_role=platform_role, roles=(platform_role,), permissions=(MANAGE,))
    c.evidence.grants[0].role = 'ADMIN'
    c.evidence.roles[0].role = platform_role
    c.evidence = replace(c.evidence, role_permissions=({'role':platform_role,'permission_code':MANAGE},))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['expired','not_member','inactive','wrong_tenant','project_only'])
async def test_owner_default_does_not_outlive_effective_ownership_or_membership(case, failure):
    c=case
    c.evidence=replace(c.evidence,role_permissions=())
    if failure == 'expired': c.evidence.grants[0].expires_at=c.now
    elif failure == 'not_member':
        c.evidence.users[0].tenant_id=None
        c.evidence=replace(c.evidence,roles=(c.evidence.roles[1],))
    elif failure == 'inactive': c.evidence.users[0].status='blocked'
    elif failure == 'wrong_tenant': c.evidence.grants[0].tenant_id=uuid4()
    else:
        c.evidence.grants[0].scope_type='project';c.evidence.grants[0].scope_id=c.project
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
async def test_default_permission_still_cannot_remove_last_owner(case):
    c=case;c.actor=replace(c.actor,permissions=());c.target=c.actor.user_id
    c.evidence=replace(c.evidence,target_user_id=c.target,role_permissions=(),grants=(c.evidence.grants[0],))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
async def test_owner_default_can_remove_another_owner_when_one_survives(case):
    c=case;c.actor=replace(c.actor,permissions=())
    c.evidence=replace(c.evidence,role_permissions=(),grants=(c.evidence.grants[0],c.grant(c.target,'OWNER')))
    result=await authorize(c)
    assert result.target_user_id==c.target


def wire_sqlite_authorizer(c, role='OWNER'):
    # A real canonical grant in the same local SQL transaction, not actor/JWT claims.
    authority_grant=Grant(id=uuid4(),tenant_id=c.a,user_id=c.actor_id,role=role,
        scope_type='tenant',scope_id=c.a,status='active',expires_at=None,
        granted_by_user_id=c.actor_id,granted_at=c.now-timedelta(days=1))
    c.session.add(authority_grant)
    # The existing fixture actor has a different primary tenant. Give it real
    # secondary membership, without assigning any membership-management power.
    c.session.add(UserRoleMapping(id=uuid4(),tenant_id=c.a,user_id=c.actor_id,
        role='client',status='active',granted_by=c.actor_id))
    c.session.commit();c.session.begin()
    c.actor=replace(c.actor,permissions=())
    def aware(row):
        values={column.name:getattr(row,column.name) for column in Grant.__table__.c}
        for field in ('expires_at','granted_at','revoked_at'):
            value=values[field]
            if value is not None and value.tzinfo is None: values[field]=value.replace(tzinfo=timezone.utc)
        return NS(**values)
    async def prepare_evidence(**v):
        assert v==dict(tenant_id=c.a,actor_user_id=c.actor_id,target_user_id=c.user_id)
        # This adapter replaces only PostgreSQL evidence locks in local SQLite.
        # Membership/grant/audit writes still run through their actual repositories.
        grants=tuple(aware(row) for row in c.session.scalars(select(Grant).where(
            Grant.tenant_id==c.a,Grant.status=='active')).all())
        users=tuple(c.session.scalars(select(User).where(User.id.in_((c.actor_id,c.user_id)))).all())
        roles=tuple(c.session.scalars(select(UserRoleMapping).where(
            UserRoleMapping.tenant_id==c.a,UserRoleMapping.user_id.in_((c.actor_id,c.user_id)))).all())
        scopes=tuple(c.session.scalars(select(RoleScope).where(
            RoleScope.tenant_id==c.a,RoleScope.user_id.in_((c.actor_id,c.user_id)))).all())
        return Evidence(c.a,c.actor_id,c.user_id,c.session.get_transaction(),users,roles,scopes,grants,())
    repo=NS(session=c.bridge,transaction=c.session.get_transaction,prepare_evidence=AsyncMock(side_effect=prepare_evidence))
    authorizer=Authorizer(session=c.bridge,repository=repo,now_provider=lambda:c.now)
    c.operation.authorization=authorizer
    return authorizer


@pytest.mark.asyncio
async def test_owner_default_reaches_existing_http_and_commits_real_local_business_operation(removal_http):
    c=removal_http;authorizer=wire_sqlite_authorizer(c)
    response=await post(c)
    assert response.status_code==200,response.text
    assert response.json()['data']==dict(user_id=str(c.user_id),changed=True)
    assert authorizer.repository.prepare_evidence.await_count==1
    assert c.user.tenant_id is None and c.roles['client'].status=='revoked'
    assert all(c.grants[name].status=='revoked' for name in ('tenant','project','document'))
    assert c.roles['foreign'].status==c.roles['global_role'].status=='active'
    assert len(events(c))==4
    c.bridge.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_default_keyed_replay_reauthorizes_without_second_mutation(removal_http):
    c=removal_http;authorizer=wire_sqlite_authorizer(c)
    first=await post(c,key='owner-default');before=snapshot(c)
    second=await post(c,key='owner-default')
    assert first.status_code==second.status_code==200,(first.text,second.text)
    assert first.json()['data']==second.json()['data']
    assert snapshot(c)==before and receipt_count(c)==1
    assert authorizer.repository.prepare_evidence.await_count==2


@pytest.mark.asyncio
async def test_admin_without_explicit_tenant_assignment_gets_403_before_real_local_changes(removal_http):
    c=removal_http;wire_sqlite_authorizer(c,role='ADMIN');before=snapshot(c)
    response=await post(c)
    assert response.status_code==403,response.text
    assert snapshot(c)==before and not events(c)
    c.bridge.commit.assert_not_called()


@pytest.mark.asyncio
async def test_default_owner_audit_failure_rolls_back_real_local_membership_and_grants(removal_http):
    c=removal_http;wire_sqlite_authorizer(c);before=snapshot(c)
    original=c.operation.events.create_event
    async def fail_after_flush(**values):
        event=await original(**values)
        if values['event_type']=='construction_access_grant_revoked':
            raise RuntimeError('mandatory local audit failure after SQL flush')
        return event
    c.operation.events.create_event=fail_after_flush
    response=await post(c)
    assert response.status_code in (500,503),response.text
    assert snapshot(c)==before and not events(c)
    c.bridge.commit.assert_not_called()
