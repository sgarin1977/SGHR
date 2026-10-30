from uuid import UUID

from services.construction.construction_permissions import (
    CONSTRUCTION_ROLES, CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access, require_construction_role_scope,
)

AREA_EDIT_PERMISSION = "construction.projects.edit"


def require_area_edit_project_scope(actor, context):
    if (
        getattr(actor, "status", None) != "active"
        or not isinstance(getattr(actor, "user_id", None), UUID)
        or not isinstance(getattr(actor, "tenant_id", None), UUID)
        or not isinstance(context, ConstructionAccessContext)
        or context.user_id != actor.user_id or context.tenant_id != actor.tenant_id
        or AREA_EDIT_PERMISSION not in context.permissions
    ):
        raise ConstructionPermissionDeniedError("Area edit access is not available.")
    roles = set(context.roles)
    if not roles or not roles.issubset(CONSTRUCTION_ROLES):
        raise ConstructionPermissionDeniedError("Area edit roles are invalid.")
    permission_roles = {
        role for role in roles if AREA_EDIT_PERMISSION in CONSTRUCTION_ROLE_PERMISSIONS[role]
    }
    if not permission_roles:
        raise ConstructionPermissionDeniedError("Area edit permission is not available.")
    projects = set()
    for grant in context.grants:
        if grant.role not in permission_roles:
            continue
        require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        if not isinstance(grant.scope_id, UUID):
            raise ConstructionScopeDeniedError("Area edit scope is invalid.")
        if grant.scope_type == "tenant" and grant.scope_id == actor.tenant_id:
            require_construction_access(
                actor=actor, access_context=context, permission_code=AREA_EDIT_PERMISSION,
            )
            return None
        if grant.scope_type == "project":
            projects.add(grant.scope_id)
    if not projects:
        raise ConstructionScopeDeniedError("Area edit scope is not available.")
    return tuple(sorted(projects, key=str))
