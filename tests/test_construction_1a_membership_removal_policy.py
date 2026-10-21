"""Pure approved boundaries; no DB, concurrency or runtime authorization claim."""
from dataclasses import replace
from uuid import uuid4

import pytest

from services.api_identity import ApiActorContext
from services.construction_membership_removal_policy import (
    PlatformLastOwnerRemovalDeniedError, require_organization_removal_policy as require,
)
from services.construction_permissions import (
    CONSTRUCTION_PERMISSION_CODES, CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext,
    ConstructionGrantContext as Grant,
)
from services.platform_membership import PlatformMembershipRemovalDeniedError as Denied
from services.platform_membership_permissions import (
    PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE as MANAGE,
    require_platform_membership_management,
)


def access(user, tenant, *grants):
    roles = tuple(sorted({g.role for g in grants}))
    permissions = tuple(sorted(set().union(*(CONSTRUCTION_ROLE_PERMISSIONS[r] for r in roles))))
    return ConstructionAccessContext(user, tenant, roles, permissions, tuple(grants))


@pytest.fixture
def ctx():
    tenant, actor_id, target, other, project, foreign_project = [uuid4() for _ in range(6)]
    actor = ApiActorContext(actor_id, tenant, 'client', ('client',), 'uk', 'UTC', 'active',
        permissions=('construction.team.manage',))
    return dict(actor=actor, actor_access=access(actor_id, tenant, Grant('OWNER', 'tenant', tenant)),
        target_access=access(target, tenant, Grant('SURVEYOR', 'project', project)),
        platform_permission_codes=(MANAGE,), active_tenant_owner_user_ids=frozenset({actor_id})), other, project, foreign_project


@pytest.mark.parametrize('permissions', [(), ('construction.team.manage',), ('construction.access.manage',),
    tuple(CONSTRUCTION_PERMISSION_CODES), (' ' + MANAGE,), (MANAGE + ' ',), (None,), MANAGE, [MANAGE]])
def test_construction_permissions_and_malformed_values_never_substitute_platform_power(permissions):
    with pytest.raises(Denied): require_platform_membership_management(permission_codes=permissions)


def test_permission_is_platform_only_and_does_not_change_construction_role_matrix():
    assert MANAGE not in CONSTRUCTION_PERMISSION_CODES
    assert all(MANAGE not in p for p in CONSTRUCTION_ROLE_PERMISSIONS.values())
    assert require_platform_membership_management(permission_codes=(MANAGE,)) is None


@pytest.mark.parametrize('role', ['SURVEYOR', 'ESTIMATOR', 'PROJECT_MANAGER', 'EXECUTOR', 'OBSERVER', 'ADMIN'])
def test_owner_can_remove_lower_or_admin_when_separately_platform_authorized(ctx, role):
    c, _, project, _ = ctx
    c['target_access'] = access(c['target_access'].user_id, c['actor'].tenant_id, Grant(role, 'project', project))
    assert require(**c) is None
    c['platform_permission_codes'] = ('construction.team.manage',)
    with pytest.raises(Denied): require(**c)


def test_owner_can_remove_another_tenant_owner_when_one_survives(ctx):
    c, _, _, _ = ctx
    c['target_access'] = access(c['target_access'].user_id, c['actor'].tenant_id,
        Grant('OWNER', 'tenant', c['actor'].tenant_id))
    c['active_tenant_owner_user_ids'] |= {c['target_access'].user_id}
    require(**c)


def test_last_owner_self_removal_requires_ownership_operation(ctx):
    c, _, _, _ = ctx
    c['target_access'] = c['actor_access']
    with pytest.raises(PlatformLastOwnerRemovalDeniedError): require(**c)


def test_owner_self_removal_possible_with_another_effective_owner(ctx):
    c, other, _, _ = ctx
    c['target_access'] = c['actor_access']
    c['active_tenant_owner_user_ids'] |= {other}
    require(**c)


@pytest.mark.parametrize('role,scope', [('ADMIN','tenant'), ('ADMIN','project'), ('OWNER','tenant'), ('OWNER','project')])
def test_admin_cannot_remove_peer_admin_or_owner_even_with_platform_permission(ctx, role, scope):
    c, other, project, _ = ctx
    c['actor_access'] = access(c['actor'].user_id, c['actor'].tenant_id, Grant('ADMIN','tenant',c['actor'].tenant_id))
    c['target_access'] = access(c['target_access'].user_id, c['actor'].tenant_id,
        Grant(role, scope, c['actor'].tenant_id if scope == 'tenant' else project))
    c['active_tenant_owner_user_ids'] = frozenset({other, c['target_access'].user_id}) if scope == 'tenant' and role == 'OWNER' else frozenset({other})
    with pytest.raises(Denied): require(**c)


def test_admin_can_remove_lower_project_role_with_separate_platform_permission(ctx):
    c, other, _, _ = ctx
    c['actor_access'] = access(c['actor'].user_id, c['actor'].tenant_id, Grant('ADMIN','tenant',c['actor'].tenant_id))
    c['active_tenant_owner_user_ids'] = frozenset({other})
    require(**c)


def test_scope_ceiling_rejects_a_second_foreign_project(ctx):
    c, _, project, foreign = ctx
    c['actor_access'] = access(c['actor'].user_id, c['actor'].tenant_id, Grant('OWNER','project',project))
    c['active_tenant_owner_user_ids'] = frozenset()
    require(**c)
    c['target_access'] = access(c['target_access'].user_id, c['actor'].tenant_id,
        Grant('SURVEYOR','project',project), Grant('OBSERVER','project',foreign))
    with pytest.raises(Denied): require(**c)


def test_project_authority_cannot_remove_tenant_admin(ctx):
    c, _, project, _ = ctx
    c['actor_access'] = access(c['actor'].user_id, c['actor'].tenant_id, Grant('OWNER','project',project))
    c['active_tenant_owner_user_ids'] = frozenset()
    c['target_access'] = access(c['target_access'].user_id, c['actor'].tenant_id, Grant('ADMIN','tenant',c['actor'].tenant_id))
    with pytest.raises(Denied): require(**c)


def test_no_grants_does_not_remove_the_separate_platform_gate(ctx):
    c, _, _, _ = ctx
    c['target_access'] = access(c['target_access'].user_id, c['actor'].tenant_id)
    require(**c)
    c['platform_permission_codes'] = ()
    with pytest.raises(Denied): require(**c)


@pytest.mark.parametrize('case', ['inactive_actor', 'foreign_actor', 'foreign_target', 'bad_owner_roster',
    'missing_actor_owner', 'phantom_target_owner', 'forged_target_permissions', 'forged_actor_permissions'])
def test_invalid_or_inconsistent_server_evidence_denied(ctx, case):
    c, _, _, _ = ctx
    if case == 'inactive_actor': c['actor'] = replace(c['actor'], status='blocked')
    elif case == 'foreign_actor': c['actor_access'] = replace(c['actor_access'], tenant_id=uuid4())
    elif case == 'foreign_target': c['target_access'] = replace(c['target_access'], tenant_id=uuid4())
    elif case == 'bad_owner_roster': c['active_tenant_owner_user_ids'] = frozenset({'owner'})
    elif case == 'missing_actor_owner': c['active_tenant_owner_user_ids'] = frozenset()
    elif case == 'phantom_target_owner': c['active_tenant_owner_user_ids'] |= {c['target_access'].user_id}
    elif case == 'forged_target_permissions': c['target_access'] = replace(c['target_access'], permissions=())
    else: c['actor_access'] = replace(c['actor_access'], permissions=())
    with pytest.raises(Denied): require(**c)


def test_repeated_grants_do_not_turn_one_owner_into_two(ctx):
    c, _, _, _ = ctx
    c['actor_access'] = replace(c['actor_access'], grants=c['actor_access'].grants * 2)
    c['target_access'] = c['actor_access']
    with pytest.raises(PlatformLastOwnerRemovalDeniedError): require(**c)
