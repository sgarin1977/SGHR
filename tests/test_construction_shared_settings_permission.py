from construction_rate_limit_fixture import with_local_construction_rate_limit
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from api.app import create_app
from api.auth import get_current_actor
import api.routes.construction_settings as routes
from services.construction_permissions import (
    CONSTRUCTION_PERMISSION_CODES, CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionRolePermissionPolicy, require_delegable_construction_role,
)
from services.construction_tenant_settings import (
    ConstructionTenantSettingsPermissionDeniedError, require_construction_settings_manage,
)


def codes(value):
    return frozenset("construction." + item for item in value.split())


EXPECTED = {
    "SURVEYOR": codes("""
        projects.read measurements.read measurements.create measurements.edit measurements.submit
        materials.read documents.generate exports.internal logistics.read planning.read
        execution.read project_documents.read
    """),
    "ESTIMATOR": codes("""
        projects.read measurements.read estimates.read estimates.create estimates.edit estimates.submit
        pricing.read costs.read costs.edit materials.read materials.edit tax.read documents.generate
        exports.internal exports.client exports.procurement logistics.read planning.read workforce.read
        project_documents.read contracts.read
    """),
    "PROJECT_MANAGER": codes("""
        projects.read projects.create projects.edit measurements.read measurements.create
        measurements.edit measurements.submit measurements.verify estimates.read estimates.create
        estimates.edit estimates.submit estimates.approve pricing.read costs.read costs.edit
        materials.read materials.edit tax.read documents.generate exports.internal exports.client
        exports.procurement team.manage logistics.read logistics.edit planning.read planning.edit
        planning.approve workforce.read workforce.assign workforce.crews.read workforce.crews.manage
        workforce.cv.read execution.read execution.update project_documents.read
        project_documents.manage contracts.read contracts.manage
    """),
    "EXECUTOR": codes("""
        projects.read measurements.read materials.read logistics.read planning.read workforce.read
        workforce.crews.read execution.read execution.update project_documents.read
    """),
    "OBSERVER": codes("""
        projects.read measurements.read estimates.read pricing.read costs.read materials.read tax.read
        logistics.read planning.read workforce.read workforce.crews.read execution.read
        project_documents.read contracts.read
    """),
}
REGISTRY = codes("""
    projects.read projects.create projects.edit measurements.read measurements.create measurements.edit
    measurements.submit measurements.verify estimates.read estimates.create estimates.edit
    estimates.submit estimates.approve pricing.read pricing.edit costs.read costs.edit materials.read
    materials.edit tax.read tax.edit documents.generate exports.internal exports.client
    exports.procurement team.manage access.manage logistics.read logistics.edit planning.read
    planning.edit planning.approve workforce.read workforce.assign workforce.crews.read
    workforce.crews.manage workforce.cv.read workforce.cv.import workforce.profile.edit
    workforce.profile.verify execution.read execution.update project_documents.read
    project_documents.manage contracts.read contracts.manage contracts.activate settings.manage
""")
EXPECTED["OWNER"] = REGISTRY
EXPECTED["ADMIN"] = REGISTRY - {"construction.contracts.activate"}


@pytest.mark.parametrize("role", sorted(EXPECTED))
def test_approved_full_role_sets_and_registry(role):
    assert CONSTRUCTION_PERMISSION_CODES == REGISTRY
    assert CONSTRUCTION_ROLE_PERMISSIONS[role] == EXPECTED[role]
    assert len(REGISTRY) == 48


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "SURVEYOR", "ESTIMATOR", "PROJECT_MANAGER", "EXECUTOR", "OBSERVER"])
async def test_common_policy_resolves_settings_only_for_owner_admin(role):
    permissions = await ConstructionRolePermissionPolicy().list_permissions(roles=(role,))
    assert ("construction.settings.manage" in permissions) == (role in ("OWNER", "ADMIN"))


def actor_context(role="OWNER", scope="tenant", *, permissions=None):
    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4(), status="active")
    context = ConstructionAccessContext(
        actor.user_id, actor.tenant_id, (role,),
        tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS[role])) if permissions is None else permissions,
        (ConstructionGrantContext(role, scope, actor.tenant_id if scope == "tenant" else uuid4()),),
    )
    return actor, context


@pytest.mark.parametrize("failure", ["missing_permission", "forged_role", "project", "document", "foreign_tenant", "foreign_user", "inactive"])
def test_settings_guard_fails_closed_through_common_engine(failure):
    role = "SURVEYOR" if failure == "forged_role" else "OWNER"
    scope = failure if failure in ("project", "document") else "tenant"
    permissions = () if failure == "missing_permission" else ("construction.settings.manage",)
    actor, context = actor_context(role, scope, permissions=permissions)
    if failure == "foreign_tenant":
        actor.tenant_id = uuid4()
    elif failure == "foreign_user":
        actor.user_id = uuid4()
    elif failure == "inactive":
        actor.status = "blocked"
    with pytest.raises(ConstructionTenantSettingsPermissionDeniedError):
        require_construction_settings_manage(actor=actor, access_context=context)


def test_settings_guard_calls_shared_access_guard(monkeypatch):
    import services.construction_permissions as permissions
    actor, context = actor_context()
    calls = []
    def deny(**kwargs):
        calls.append(kwargs)
        raise ConstructionPermissionDeniedError("Shared policy denied")
    monkeypatch.setattr(permissions, "require_construction_access", deny)
    with pytest.raises(ConstructionTenantSettingsPermissionDeniedError):
        require_construction_settings_manage(actor=actor, access_context=context)
    assert calls == [dict(actor=actor, access_context=context, permission_code="construction.settings.manage")]


@pytest.mark.asyncio
async def test_live_resolver_includes_settings_and_excludes_expired_revoked_grants():
    from services.construction_grants import ConstructionAccessContextResolver
    actor, _ = actor_context()
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    def grant(role, status="active", deadline=None):
        return SimpleNamespace(role=role, status=status, expires_at=deadline,
                               scope_type="tenant", scope_id=actor.tenant_id)
    repository = SimpleNamespace(list_active_grants=AsyncMock(return_value=[
        grant("OWNER", deadline=now),
        grant("OWNER", status="revoked"),
        grant("ADMIN", deadline=now + timedelta(days=1)),
    ]))
    resolver = ConstructionAccessContextResolver(
        repository=repository, role_policy=ConstructionRolePermissionPolicy(), now_provider=lambda: now,
    )
    context = await resolver.resolve(actor=actor)
    assert context.roles == ("ADMIN",)
    assert "construction.settings.manage" in context.permissions
    assert "construction.contracts.activate" not in context.permissions
    require_construction_settings_manage(actor=actor, access_context=context)


def test_admin_delegation_cannot_escalate_to_owner():
    actor, context = actor_context("ADMIN")
    require_delegable_construction_role(
        actor=actor, access_context=context, target_role="ADMIN",
        scope_type="tenant", scope_id=actor.tenant_id,
    )
    with pytest.raises(ConstructionPermissionDeniedError):
        require_delegable_construction_role(
            actor=actor, access_context=context, target_role="OWNER",
            scope_type="tenant", scope_id=actor.tenant_id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("role,permissions", [("OWNER", ()), ("SURVEYOR", ("construction.settings.manage",))])
async def test_settings_http_uses_common_permission_before_mocked_service(method, role, permissions):
    actor, context = actor_context(role, permissions=permissions)
    service = SimpleNamespace(create_for_actor=AsyncMock(), update_for_actor=AsyncMock())
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[routes.get_construction_scoped_session] = lambda: object()
    app.dependency_overrides[routes.get_construction_access_context] = lambda: context
    app.dependency_overrides[routes.get_settings_service] = lambda: service
    for dependency in routes.router.dependencies[2:]:
        app.dependency_overrides[dependency.dependency] = lambda: actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
    ) as client:
        response = await client.request(method.upper(), "/api/v1/construction/settings/company", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_required"
    service.create_for_actor.assert_not_called()
    service.update_for_actor.assert_not_called()
