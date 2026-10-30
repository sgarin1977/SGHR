from services.construction.construction_permissions import CONSTRUCTION_ROLE_PERMISSIONS
import pytest
from types import SimpleNamespace
from uuid import uuid4

from services.construction.construction_permissions import (
    ConstructionAccessContext,
    ConstructionGrantContext,
)


def test_con_c002_settings_manage_requires_tenant_owner_or_admin():
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsPermissionDeniedError,
        require_construction_settings_manage,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        user_id=user_id, tenant_id=tenant_id, status="active"
    )

    def context(role, scope_type="tenant", scope_id=tenant_id):
        return ConstructionAccessContext(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=(role,),
            permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get(role, frozenset()))),
            grants=(
                ConstructionGrantContext(
                    role=role,
                    scope_type=scope_type,
                    scope_id=scope_id,
                ),
            ),
        )

    for role in ("OWNER", "ADMIN"):
        require_construction_settings_manage(
            actor=actor, access_context=context(role)
        )

    for denied in (
        context("SURVEYOR"),
        context("OWNER", "project", uuid4()),
        context("ADMIN", "tenant", uuid4()),
    ):
        with pytest.raises(
            ConstructionTenantSettingsPermissionDeniedError
        ):
            require_construction_settings_manage(
                actor=actor, access_context=denied
            )

    with pytest.raises(ConstructionTenantSettingsPermissionDeniedError):
        require_construction_settings_manage(
            actor=SimpleNamespace(
                user_id=uuid4(), tenant_id=tenant_id, status="active"
            ),
            access_context=context("OWNER"),
        )
