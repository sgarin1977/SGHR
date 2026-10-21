from construction_rate_limit_fixture import with_local_construction_rate_limit, construction_module_dependencies
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
)
from services.construction_team import ConstructionTeamService


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    "anonymous", "inactive", "no_permission", "other_project",
    "foreign_user_context", "foreign_tenant_context",
    "suite_disabled", "module_disabled",
])
async def test_c004_http_denial_does_not_read_team_data(monkeypatch, mode):
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_session
    from api.errors import ApiHttpError
    import api.routes.construction as routes

    tenant_id, user_id, project_id = uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(
        user_id=user_id, tenant_id=tenant_id, permissions=(),
        status="suspended" if mode == "inactive" else "active",
    )
    role = "PROJECT_MANAGER" if mode == "other_project" else "OWNER"
    context = ConstructionAccessContext(
        user_id=uuid4() if mode == "foreign_user_context" else user_id,
        tenant_id=uuid4() if mode == "foreign_tenant_context" else tenant_id,
        roles=(role,),
        permissions=(
            ("construction.projects.read",)
            if mode == "no_permission" else ("construction.team.manage",)
        ),
        grants=(ConstructionGrantContext(
            role=role,
            scope_type="project" if mode == "other_project" else "tenant",
            scope_id=uuid4() if mode == "other_project" else tenant_id,
        ),),
    )
    reads = []

    class Reader:
        async def list_active_grants(self, **kwargs):
            reads.append("grants")
            return []

        async def list_team_identity_accounts(self, **kwargs):
            reads.append("identity")
            return []

    def build(**kwargs):
        return ConstructionTeamService(
            repository=Reader(), identity_reader=Reader(),
        )

    def current_actor():
        if mode == "anonymous":
            raise ApiHttpError(
                status_code=401, code="authentication_required",
                message="Authentication is required.",
            )
        return actor

    def disabled():
        raise ApiHttpError(
            status_code=403, code="MODULE_DISABLED",
            message="Construction is disabled.",
        )

    monkeypatch.setattr(routes, "build_construction_team_service", build)
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = current_actor
    app.dependency_overrides[routes.get_construction_access_context] = lambda: context
    construction_session, platform_session = object(), object()
    app.dependency_overrides[routes.get_construction_scoped_session] = (
        lambda: construction_session
    )
    app.dependency_overrides[get_api_session] = lambda: platform_session
    for dependency in construction_module_dependencies(routes.router):
        app.dependency_overrides[dependency.dependency] = lambda: actor
    if mode in ("suite_disabled", "module_disabled"):
        index = 0 if mode == "suite_disabled" else 1
        app.dependency_overrides[
            construction_module_dependencies(routes.router)[index].dependency
        ] = disabled

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/construction/team/members",
            params={"project_id": str(project_id)},
        )

    assert response.status_code == (401 if mode == "anonymous" else 403), response.text
    body = response.json()
    assert "error" in body
    assert "data" not in body
    assert reads == []
    if mode in ("suite_disabled", "module_disabled"):
        assert body["error"]["code"] == "MODULE_DISABLED"
