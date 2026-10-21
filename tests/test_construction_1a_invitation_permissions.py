from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.parametrize("manager,grant_scope,target_scope,role,allowed", [
    ("OWNER", "tenant", "tenant", "ADMIN", True),
    ("ADMIN", "tenant", "tenant", "OWNER", False),
    ("PROJECT_MANAGER", "project", "own", "SURVEYOR", True),
    ("PROJECT_MANAGER", "project", "other", "SURVEYOR", False),
    ("SURVEYOR", "project", "own", "EXECUTOR", False),
    ("OWNER", "tenant", "tenant", "SURVEYOR", False),
    ("OWNER", "tenant", "document", "OBSERVER", False),
    ("OWNER", "none", "own", "SURVEYOR", False),
    ("OWNER", "tenant", "empty", "SURVEYOR", False),
])
def test_c005_invitation_permissions_respect_role_and_scope(
    manager, grant_scope, target_scope, role, allowed,
):
    from services.construction_invitations import (
        require_construction_invitation_permissions,
    )
    from services.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS,
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
        ConstructionScopeDeniedError,
    )

    tenant_id, user_id, project_id = uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(user_id=user_id, tenant_id=tenant_id, status="active")
    grants = () if grant_scope == "none" else (
        ConstructionGrantContext(
            role=manager,
            scope_type=grant_scope,
            scope_id=tenant_id if grant_scope == "tenant" else project_id,
        ),
    )
    context = ConstructionAccessContext(
        user_id=user_id, tenant_id=tenant_id,
        roles=(manager,),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS[manager]),
        grants=grants,
    )
    scope_type = "project" if target_scope in ("own", "other") else target_scope
    scope_id = (
        tenant_id if target_scope == "tenant"
        else project_id if target_scope == "own" else uuid4()
    )
    scopes = [] if target_scope == "empty" else [(scope_type, scope_id)]
    arguments = dict(actor=actor, access_context=context, role=role, scopes=scopes)

    if allowed:
        assert require_construction_invitation_permissions(**arguments) is None
    else:
        with pytest.raises((
            ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        )):
            require_construction_invitation_permissions(**arguments)
