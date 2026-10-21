"""Platform policy for organization membership management.

An effective tenant OWNER receives the default permission. Other roles receive
no default. Shared role_permissions templates and JWT claims are not an explicit
user/tenant grant. No database assignments are created by this policy.
"""
from services.platform_membership import PlatformMembershipRemovalDeniedError

PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE = 'platform.organization.memberships.manage'


def require_platform_membership_management(*, permission_codes):
    if (not isinstance(permission_codes, (tuple, frozenset))
            or any(type(code) is not str or not code or code != code.strip()
                   for code in permission_codes)
            or PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE not in permission_codes):
        raise PlatformMembershipRemovalDeniedError('Platform membership management permission is required.')



def resolve_platform_membership_default_permissions(*, actor, construction_access_context):
    """Map fresh server-side tenant ownership to Platform permission codes.

    This pure policy is not authentication, a DB lock or an authority receipt.
    Its runtime caller must verify active membership and prepare locked grants,
    re-evaluate their expiration and retain the ownership barrier until commit.
    No default is derived from actor.roles, actor.permissions or global roles.
    ADMIN explicit tenant grants require their own canonical Platform source;
    an assignment to a shared role template must never stand in for that source.
    """
    from uuid import UUID
    from services.api_identity import ApiActorContext
    from services.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
        ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        require_construction_role_scope,
    )
    context = construction_access_context
    empty = frozenset()
    if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
            or not all(isinstance(uid, UUID) for uid in (actor.user_id, actor.tenant_id))
            or not isinstance(context, ConstructionAccessContext)
            or context.user_id != actor.user_id or context.tenant_id != actor.tenant_id
            or not isinstance(context.roles, tuple) or not isinstance(context.permissions, tuple)
            or not isinstance(context.grants, tuple)):
        return empty
    roles = set()
    owner = False
    for grant in context.grants:
        if (not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID)
                or (grant.scope_type == 'tenant' and grant.scope_id != actor.tenant_id)):
            return empty
        try:
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            return empty
        roles.add(grant.role)
        owner = owner or (grant.role == 'OWNER' and grant.scope_type == 'tenant'
            and grant.scope_id == actor.tenant_id)
    permissions = set().union(*(CONSTRUCTION_ROLE_PERMISSIONS[role] for role in roles))
    if (context.roles != tuple(sorted(roles)) or context.permissions != tuple(sorted(permissions))):
        return empty
    return frozenset({PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE}) if owner else empty



def resolve_platform_membership_explicit_permissions(*, actor, construction_access_context, permission_grants):
    """Only the approved tenant ADMIN policy accepts explicit canonical grants.

    No other role acquires an implicit eligibility policy. Current membership,
    authority expiry and transaction locks remain the runtime caller's checks.
    """
    from uuid import UUID
    from database.repositories.platform_organization_permissions import validate_permission_grant
    from services.api_identity import ApiActorContext
    from services.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
        require_construction_role_scope,
    )
    context = construction_access_context
    if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
            or not all(isinstance(value, UUID) for value in (actor.user_id, actor.tenant_id))
            or not isinstance(context, ConstructionAccessContext)
            or (context.user_id, context.tenant_id) != (actor.user_id, actor.tenant_id)
            or any(not isinstance(values, tuple) for values in (
                context.grants, context.roles, context.permissions, permission_grants))):
        return frozenset()
    roles = set()
    tenant_admin = False
    for grant in context.grants:
        if (not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID)
                or (grant.scope_type == 'tenant' and grant.scope_id != actor.tenant_id)):
            return frozenset()
        require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        roles.add(grant.role)
        tenant_admin = tenant_admin or (grant.role == 'ADMIN' and grant.scope_type == 'tenant'
            and grant.scope_id == actor.tenant_id)
    expected = tuple(sorted(set().union(*(CONSTRUCTION_ROLE_PERMISSIONS[role] for role in roles))))
    if context.roles != tuple(sorted(roles)) or context.permissions != expected:
        return frozenset()
    active_users, identifiers = set(), set()
    for row in permission_grants:
        try:
            validate_permission_grant(row)
        except ValueError:
            raise PlatformMembershipRemovalDeniedError('Explicit Platform permission evidence is invalid.') from None
        if row.tenant_id != actor.tenant_id or row.id in identifiers:
            raise PlatformMembershipRemovalDeniedError('Explicit Platform permission scope is invalid.')
        identifiers.add(row.id)
        if row.status == 'active':
            if row.user_id in active_users:
                raise PlatformMembershipRemovalDeniedError('Explicit Platform permission evidence is ambiguous.')
            active_users.add(row.user_id)
    if tenant_admin and actor.user_id in active_users:
        return frozenset({PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE})
    return frozenset()
