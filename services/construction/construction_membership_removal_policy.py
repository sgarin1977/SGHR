"""Approved pure removal checks, NOT a runtime authorizer or a lock receipt.

Call only with fresh server-resolved access and an eligible, effective owner
roster for this tenant. The caller must serialize ownership changes, lock and
refresh this evidence, enforce Platform administrative scopes/ceiling, and retain
authority through commit. This function performs no I/O and cannot establish
those facts. In particular it must not be used to manufacture the authority
receipt accepted by PlatformMembershipService from client-supplied contexts.
"""
from uuid import UUID

from services.api_identity import ApiActorContext
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_role_scope, require_delegable_construction_role,
)
from services.platform_membership import PlatformMembershipRemovalDeniedError
from services.platform_membership_permissions import require_platform_membership_management


class PlatformLastOwnerRemovalDeniedError(PlatformMembershipRemovalDeniedError):
    """Ordinary removal cannot remove the last effective tenant OWNER."""


def _deny():
    raise PlatformMembershipRemovalDeniedError('Organization removal exceeds current authority.')


def _context(context, *, tenant_id, user_id):
    if (not isinstance(context, ConstructionAccessContext)
            or context.tenant_id != tenant_id or context.user_id != user_id
            or not isinstance(context.roles, tuple) or not isinstance(context.permissions, tuple)
            or not isinstance(context.grants, tuple)):
        _deny()
    expected_roles = set()
    for grant in context.grants:
        if (not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID)
                or (grant.scope_type == 'tenant' and grant.scope_id != tenant_id)):
            _deny()
        require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        expected_roles.add(grant.role)
    expected_permissions = set().union(*(CONSTRUCTION_ROLE_PERMISSIONS[r] for r in expected_roles))
    if (context.roles != tuple(sorted(expected_roles))
            or context.permissions != tuple(sorted(expected_permissions))):
        _deny()


def _tenant_owner(context):
    return any(g.role == 'OWNER' and g.scope_type == 'tenant'
               and g.scope_id == context.tenant_id for g in context.grants)


def _covering_owner(context, target):
    return any(g.role == 'OWNER' and (
        (g.scope_type == 'tenant' and g.scope_id == context.tenant_id)
        or (g.scope_type == target.scope_type and g.scope_id == target.scope_id)
    ) for g in context.grants)


def require_organization_removal_policy(*, actor, actor_access, target_access,
        platform_permission_codes, active_tenant_owner_user_ids):
    """Require separate Platform permission, owner protection and grant ceiling.

Returns None, not a capability. Success is only the pure policy part of the
future locked Platform authorizer. No role assignment, mutation or commit.
"""
    try:
        require_platform_membership_management(permission_codes=platform_permission_codes)
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)
                or not isinstance(target_access, ConstructionAccessContext)
                or not isinstance(target_access.user_id, UUID)):
            _deny()
        _context(actor_access, tenant_id=actor.tenant_id, user_id=actor.user_id)
        _context(target_access, tenant_id=actor.tenant_id, user_id=target_access.user_id)
        owners = active_tenant_owner_user_ids
        if (not isinstance(owners, frozenset) or any(not isinstance(uid, UUID) for uid in owners)
                or _tenant_owner(actor_access) != (actor.user_id in owners)
                or (target_access.user_id in owners and not _tenant_owner(target_access))):
            _deny()
        if target_access.user_id in owners and len(owners) <= 1:
            raise PlatformLastOwnerRemovalDeniedError('The last tenant OWNER requires a separate ownership operation.')
        for grant in target_access.grants:
            # ADMIN cannot remove a peer ADMIN or an OWNER. A covering OWNER
            # remains subject to the same exact scope/delegation checks below.
            if grant.role in ('ADMIN', 'OWNER') and not _covering_owner(actor_access, grant):
                _deny()
            require_delegable_construction_role(actor=actor, access_context=actor_access,
                target_role=grant.role, scope_type=grant.scope_type, scope_id=grant.scope_id)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise PlatformMembershipRemovalDeniedError('Organization removal exceeds current authority.') from None
