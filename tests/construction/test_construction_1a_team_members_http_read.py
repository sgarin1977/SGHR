from construction_rate_limit_fixture import with_local_construction_rate_limit, construction_module_dependencies
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    CONSTRUCTION_ROLE_PERMISSIONS,
)
from services.construction.construction_team import ConstructionTeamService


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_present", [True, False])
async def test_c004_http_returns_aggregated_members(monkeypatch, identity_present):
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_session
    import api.routes.construction as routes

    tenant_id, manager_id, member_id, project_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    actor = SimpleNamespace(
        user_id=manager_id, tenant_id=tenant_id, status="active", permissions=(),
    )
    context = ConstructionAccessContext(
        user_id=manager_id, tenant_id=tenant_id,
        roles=("OWNER",), permissions=("construction.team.manage",),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id,
        ),),
    )
    grants = [
        SimpleNamespace(
            id=uuid4(), user_id=member_id, role=role,
            scope_type="project", scope_id=project_id, expires_at=None,
            private_marker="must-not-leak",
        )
        for role in ("SURVEYOR", "ESTIMATOR")
    ]
    grant_calls, identity_calls, build_calls = [], [], []
    construction_session, platform_session = object(), object()

    class Repository:
        async def list_active_grants(self, **kwargs):
            grant_calls.append(kwargs)
            return grants

    class IdentityReader:
        async def list_team_identity_accounts(self, **kwargs):
            identity_calls.append(kwargs)
            return [{
                "user_id": member_id, "display_name": "Member",
                "verified_email": "member@example.com",
            }] if identity_present else []

    def build(**kwargs):
        build_calls.append(kwargs)
        return ConstructionTeamService(
            repository=Repository(), identity_reader=IdentityReader(),
        )

    monkeypatch.setattr(routes, "build_construction_team_service", build)
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[routes.get_construction_access_context] = lambda: context
    app.dependency_overrides[routes.get_construction_scoped_session] = (
        lambda: construction_session
    )
    app.dependency_overrides[get_api_session] = lambda: platform_session
    for dependency in construction_module_dependencies(routes.router):
        app.dependency_overrides[dependency.dependency] = lambda: actor

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/construction/team/members",
            params={"project_id": str(project_id)},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["data"]) == 1
    assert body["request_id"]
    member = body["data"][0]
    assert member["user_id"] == str(member_id)
    assert member["display_name"] == ("Member" if identity_present else None)
    assert member["email"] == ("member@example.com" if identity_present else None)
    assert set(member["roles"]) == {"SURVEYOR", "ESTIMATOR"}
    assert {grant["id"] for grant in member["grants"]} == {
        str(grant.id) for grant in grants
    }
    assert "must-not-leak" not in response.text
    assert len(member["scoped_permissions"]) == 2
    for entry in member["scoped_permissions"]:
        assert entry["scope_id"] == str(project_id)
        assert entry["permissions"] == sorted(CONSTRUCTION_ROLE_PERMISSIONS[entry["role"]])
    assert len(grant_calls) == 1
    assert grant_calls[0]["tenant_id"] == tenant_id
    assert grant_calls[0]["project_id"] == project_id
    assert identity_calls == [{"tenant_id": tenant_id, "user_ids": (member_id,)}]
    assert build_calls == [{
        "construction_session": construction_session,
        "platform_session": platform_session,
    }]
