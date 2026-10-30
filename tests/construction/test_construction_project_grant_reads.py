"""Read boundaries and role options for project-scoped team managers."""
from uuid import uuid4

import pytest
from fastapi import Depends

from api.routes.construction import (
    ConstructionAPIRouter, require_construction_grant_management,
)
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from test_construction_1a_invitation_acceptance_http import http_case
from test_construction_1a_invitation_management_http import management_case
from test_construction_1a_role_options import role_case, get as role_get, no_writes as role_no_writes
from test_construction_1a_access_read_http import (
    access_read_case, get as access_get, no_writes as access_no_writes,
)


def project_manager(c, project):
    c.access = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",),
        tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
        (ConstructionGrantContext("PROJECT_MANAGER", "project", project),),
    )


@pytest.mark.asyncio
async def test_project_manager_role_options_are_limited_by_own_permissions(role_case):
    c = role_case
    project_manager(c, c.project_id)
    response = await role_get(c, scope_type="project", scope_id=c.project_id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["permissions_read_only"] is True
    assert {item["role"] for item in data["roles"]} == {
        "PROJECT_MANAGER", "SURVEYOR", "ESTIMATOR", "EXECUTOR", "OBSERVER",
    }
    assert data["scope_id"] == str(c.project_id)
    assert len(c.statements) == 1
    role_no_writes(c)


@pytest.mark.asyncio
async def test_project_team_manager_cannot_request_tenant_role_options(role_case):
    c = role_case
    project_manager(c, c.project_id)
    response = await role_get(c, scope_type="tenant", scope_id=c.actor.tenant_id)
    assert response.status_code == 403, response.text
    assert c.statements == []
    role_no_writes(c)


@pytest.mark.asyncio
async def test_project_team_manager_reads_only_direct_grants_in_own_project(access_read_case):
    c = access_read_case
    project = uuid4()
    project_manager(c, project)
    c.grants[0].role = "ESTIMATOR"
    c.grants[0].scope_type = "project"
    c.grants[0].scope_id = project
    response = await access_get(c, query="?project_id=" + str(project))
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["scope_id"] == str(project)
    assert len(c.statements) == 1
    parameters = c.statements[0].compile().params.values()
    assert project in parameters and c.actor.tenant_id in parameters
    access_no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("query_kind", ["unfiltered", "other_project"])
async def test_project_team_permission_cannot_read_all_tenant_grants(access_read_case, query_kind):
    c = access_read_case
    project_manager(c, uuid4())
    query = "" if query_kind == "unfiltered" else "?project_id=" + str(uuid4())
    response = await access_get(c, query=query)
    assert response.status_code == 403, response.text
    assert c.statements == []
    access_no_writes(c)


def test_router_requires_registered_permission_markers_for_combined_guard():
    guard = require_construction_grant_management()
    assert guard.construction_permission_codes == (
        "construction.access.manage", "construction.team.manage",
    )
    router = ConstructionAPIRouter()
    async def endpoint():
        return {}
    router.add_api_route(
        "/grant-management-probe", endpoint, methods=["GET"],
        dependencies=[Depends(guard)],
    )
    async def unsafe():
        return None
    unsafe.construction_permission_codes = ("construction.unregistered",)
    with pytest.raises(ValueError, match="permission"):
        router.add_api_route(
            "/invalid-marker", endpoint, methods=["GET"],
            dependencies=[Depends(unsafe)],
        )
