"""Locked evidence and actual shared permission matrix; SQL contracts, no native acceptance."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from database.repositories.platform_membership_authorization import MembershipRemovalEvidence as Evidence, PlatformMembershipAuthorizationRepository as Repository
from database.repositories.platform_membership_serialization import ownership_lock_key, membership_lock_key
from services.api_identity import ApiActorContext
from services.platform_membership import PlatformMembershipRemovalAuthority, PlatformMembershipRemovalDeniedError as Denied
from services.platform_membership_permissions import PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE as MANAGE
from services.platform_membership_authorization import PlatformMembershipRemovalAuthorizer as Authorizer


@pytest.fixture
def case():
    tenant, actor_id, target_id, project = [uuid4() for _ in range(4)]
    now = datetime(2026,10,21,10,0,tzinfo=timezone.utc)
    actor = ApiActorContext(actor_id,tenant,'client',('client',),'uk','UTC','active',permissions=(MANAGE,))
    def user(uid): return NS(id=uid,tenant_id=tenant,status='active')
    def role(uid,name): return NS(id=uuid4(),user_id=uid,tenant_id=tenant,role=name,status='active')
    def grant(uid,name,scope='tenant',scope_id=None,expiry=None):
        return NS(id=uuid4(),user_id=uid,tenant_id=tenant,role=name,scope_type=scope,
            scope_id=scope_id or tenant,status='active',revoked_at=None,revoked_by_user_id=None,expires_at=expiry)
    transaction = NS(is_active=True)
    session = NS()
    users = (user(actor_id),user(target_id))
    roles = (role(actor_id,'admin'),role(target_id,'client'))
    grants = (grant(actor_id,'OWNER'),grant(target_id,'SURVEYOR','project',project))
    e = Evidence(tenant,actor_id,target_id,transaction,users,roles,(),grants,
        ({'role':'admin','permission_code':MANAGE},))
    repo = NS(session=session,transaction=Mock(return_value=transaction),prepare_evidence=AsyncMock(return_value=e))
    c = NS(actor=actor,target=target_id,tenant=tenant,project=project,now=now,transaction=transaction,
        session=session,repo=repo,evidence=e,user=user,role=role,grant=grant)
    c.auth = Authorizer(session=session,repository=repo,now_provider=lambda:c.now)
    return c


async def authorize(c):
    c.repo.prepare_evidence.return_value=c.evidence
    await c.auth.prepare_removal(actor=c.actor,target_user_id=c.target)
    return await c.auth.authorize_removal(actor=c.actor,target_user_id=c.target)


@pytest.mark.asyncio
async def test_receipt_comes_from_prepared_shared_transaction_not_actor_claims(case):
    c=case; c.actor=replace(c.actor,permissions=())
    result=await authorize(c)
    assert isinstance(result,PlatformMembershipRemovalAuthority)
    assert (result.actor_user_id,result.tenant_id,result.target_user_id)==(c.actor.user_id,c.tenant,c.target)


@pytest.mark.asyncio
async def test_token_claims_never_replace_missing_current_platform_permission_for_nonowner(case):
    c=case; c.evidence.grants[0].role='ADMIN'
    c.evidence=replace(c.evidence,role_permissions=())
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('state',['suspended','revoked'])
async def test_inactive_current_platform_role_cannot_supply_permission(case,state):
    c=case; c.evidence.roles[0].status=state; c.evidence.grants[0].role='ADMIN'
    # Real repository does not return mappings for inactive roles.
    c.evidence=replace(c.evidence,role_permissions=())
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_parent',[False,True])
async def test_region_scoped_platform_role_is_not_promoted_to_whole_organization(case,legacy_parent):
    c=case; c.evidence.grants[0].role='ADMIN'; r=c.evidence.roles[0]
    s=NS(id=uuid4(),tenant_id=c.tenant,user_id=c.actor.user_id,role=r.role,status='active',
        user_role_id=None if legacy_parent else uuid4(),scope_type='country',scope_id=uuid4())
    c.evidence=replace(c.evidence,scopes=(s,))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
async def test_preparation_is_mandatory_and_bound_to_exact_transaction_and_target(case):
    c=case
    with pytest.raises(Denied): await c.auth.authorize_removal(actor=c.actor,target_user_id=c.target)
    await c.auth.prepare_removal(actor=c.actor,target_user_id=c.target)
    with pytest.raises(Denied): await c.auth.authorize_removal(actor=c.actor,target_user_id=uuid4())
    c.repo.transaction.return_value=NS(is_active=True)
    with pytest.raises(Denied): await c.auth.authorize_removal(actor=c.actor,target_user_id=c.target)


@pytest.mark.asyncio
async def test_preparation_refreshes_expiry_after_wait_and_binds_receipt_deadline(case):
    c=case; c.evidence.grants[0].expires_at=c.now+timedelta(seconds=1)
    result=await authorize(c)
    assert result.valid_until==c.now+timedelta(seconds=1)
    c.now+=timedelta(seconds=2)
    with pytest.raises(Denied): await c.auth.authorize_removal(actor=c.actor,target_user_id=c.target)


@pytest.mark.asyncio
async def test_last_owner_is_distinct_active_member_not_number_of_grants(case):
    c=case; c.target=c.actor.user_id
    c.evidence=replace(c.evidence,target_user_id=c.target,grants=(c.evidence.grants[0],c.grant(c.target,'OWNER')))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('unavailable',['expired','blocked','not_member'])
async def test_unavailable_other_owner_does_not_save_last_owner_removal(case,unavailable):
    c=case; other=c.user(uuid4()); g=c.grant(other.id,'OWNER')
    if unavailable=='expired': g.expires_at=c.now
    elif unavailable=='blocked': other.status='blocked'
    else: other.tenant_id=None
    c.target=c.actor.user_id
    c.evidence=replace(c.evidence,target_user_id=c.target,users=(*c.evidence.users,other),
        grants=(c.evidence.grants[0],g))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
async def test_other_owner_with_active_secondary_membership_is_eligible(case):
    c=case; other=c.user(uuid4()); other.tenant_id=None
    c.target=c.actor.user_id
    c.evidence=replace(c.evidence,target_user_id=c.target,users=(*c.evidence.users,other),
        roles=(*c.evidence.roles,c.role(other.id,'client')),grants=(c.evidence.grants[0],c.grant(other.id,'OWNER')))
    result=await authorize(c)
    assert result.target_user_id==c.actor.user_id


@pytest.mark.asyncio
async def test_inactive_owner_grant_can_be_revoked_without_counting_as_last_effective_owner(case):
    c=case; c.evidence.users[1].status='blocked'
    c.evidence=replace(c.evidence,grants=(c.evidence.grants[0],c.grant(c.target,'OWNER')))
    await authorize(c)


@pytest.mark.asyncio
async def test_non_owner_platform_admin_permission_ceiling_is_separate_from_construction_roles(case):
    c=case
    c.evidence.grants[0].role='ADMIN'
    c.evidence=replace(c.evidence,roles=(c.evidence.roles[0],c.role(c.target,'support')),
        role_permissions=(*c.evidence.role_permissions,{'role':'support','permission_code':'platform.sensitive.manage'}))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
async def test_tenant_owner_can_remove_platform_admin_within_existing_platform_ceiling(case):
    c=case
    c.evidence=replace(c.evidence,roles=(c.evidence.roles[0],c.role(c.target,'admin')))
    await authorize(c)


@pytest.mark.asyncio
async def test_platform_owner_default_can_remove_admin_without_receiving_other_admin_permissions(case):
    c=case; c.evidence.roles[0].role='client'
    c.evidence=replace(c.evidence,roles=(c.evidence.roles[0],c.role(c.target,'admin')),
        role_permissions=({'role':'client','permission_code':MANAGE},
            {'role':'admin','permission_code':'admin.users.read'}))
    await authorize(c)
    assert 'admin.users.read' not in c.actor.permissions


@pytest.mark.asyncio
async def test_global_super_admin_semantics_are_not_ordinary_tenant_admin_membership(case):
    c=case
    c.evidence=replace(c.evidence,roles=(c.evidence.roles[0],c.role(c.target,'super_admin')))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
async def test_non_owner_cannot_remove_equal_platform_admin_role(case):
    c=case; c.evidence.grants[0].role='ADMIN'
    c.evidence=replace(c.evidence,roles=(c.evidence.roles[0],c.role(c.target,'admin')))
    with pytest.raises(Denied): await authorize(c)


@pytest.mark.asyncio
async def test_global_or_foreign_platform_roles_never_supply_tenant_authority(case):
    c=case; c.evidence.roles[0].tenant_id=None
    with pytest.raises(Denied): await authorize(c)


def rows(values):
    return NS(scalars=lambda:NS(all=lambda:list(values)), mappings=lambda:NS(all=lambda:list(values)))


def repository_case(c, **changes):
    context=dict(role_name='postgres',read_only='off',isolation='read committed',ownership_locked=True)
    context.update(changes)
    result=NS(mappings=lambda:NS(one=lambda:context))
    s=NS(no_autoflush=nullcontext(),get_transaction=Mock(return_value=c.transaction),in_transaction=lambda:True,
        get_nested_transaction=lambda:None,get_bind=lambda:NS(dialect=NS(name='postgresql')),
        execute=AsyncMock(side_effect=[result,NS(),NS(),rows(c.evidence.grants),rows(c.evidence.users),
            rows(c.evidence.roles),rows(c.evidence.scopes),rows(c.evidence.role_permissions),rows(())]))
    return s,Repository(s)


@pytest.mark.asyncio
async def test_actual_repository_sql_locks_catalogue_then_rows_and_uses_exact_tenant_parameters(case):
    c=case; s,repo=repository_case(c)
    evidence=await repo.prepare_evidence(tenant_id=c.tenant,actor_user_id=c.actor.user_id,target_user_id=c.target)
    assert evidence.transaction is c.transaction and evidence.tenant_id==c.tenant
    calls=s.execute.await_args_list
    assert len(calls)==9
    assert str(calls[1].args[0])=='LOCK TABLE public.permissions, public.role_permissions IN SHARE MODE'
    assert str(calls[2].args[0])=='LOCK TABLE public.platform_organization_permission_grants IN SHARE ROW EXCLUSIVE MODE'
    for call in calls[3:7]:
        query=str(call.args[0].compile(dialect=postgresql.dialect()))
        assert 'FOR UPDATE' in query and str(c.tenant) not in query and str(c.actor.user_id) not in query
    assert 'ORDER BY users.id' in str(calls[4].args[0])
    assert 'JOIN public.permissions' in str(calls[-2].args[0])
    assert 'roles' in calls[-2].args[1]
    assert 'public.platform_organization_permission_grants' in str(calls[-1].args[0])
    assert not any(str(call.args[0]).lstrip().split()[0] in ('INSERT','UPDATE','DELETE','GRANT','ALTER') for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('ownership_locked',False),('read_only','on'),('isolation','repeatable read'),
    ('role_name','construction_api'),('role_name','anon'),('role_name','authenticated'),('role_name','service_role')])
async def test_missing_barrier_or_wrong_connection_denies_before_catalogue_or_row_locks(case,field,value):
    c=case; s,repo=repository_case(c,**{field:value})
    with pytest.raises(Denied):
        await repo.prepare_evidence(tenant_id=c.tenant,actor_user_id=c.actor.user_id,target_user_id=c.target)
    assert s.execute.await_count==1


def test_ownership_key_is_stable_distinct_and_shared_by_different_member_targets(case):
    c=case
    key=ownership_lock_key(tenant_id=c.tenant)
    assert key==ownership_lock_key(tenant_id=c.tenant)
    assert key!=ownership_lock_key(tenant_id=uuid4())
    assert key not in {membership_lock_key(tenant_id=c.tenant,user_id=uid) for uid in (c.actor.user_id,c.target)}
