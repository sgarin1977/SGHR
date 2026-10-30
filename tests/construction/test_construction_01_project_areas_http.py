"""Local authorized project-area reads; no deployed/native acceptance."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
import api.routes.construction_project_areas as routes
from construction_rate_limit_fixture import with_local_construction_rate_limit
from services.construction.construction_area_read import ConstructionProjectAreaReadService
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError


@pytest.fixture
def area_http():
    c = NS(user=uuid4(), tenant=uuid4(), project_id=uuid4())
    c.actor = NS(user_id=c.user, tenant_id=c.tenant, status="active", permissions=())
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OWNER",), ("construction.projects.read",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),),
    )
    c.project = NS(
        id=c.project_id, tenant_id=c.tenant, deleted_at=None, status="active",
    )
    c.area = NS(
        id=uuid4(), tenant_id=c.tenant, project_id=c.project_id,
        area_type="ROOM", name="Public room", sort_order=None,
        status="draft", row_version=1, deleted_at=None,
        created_by=c.user, private_marker="private-field",
    )
    c.session = NS(commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock())
    c.projects = NS(
        session=c.session, get_active_project=AsyncMock(return_value=c.project),
    )
    c.areas = NS(
        session=c.session, list_active_areas=AsyncMock(return_value=[c.area]),
    )
    c.access = NS(
        session=c.session,
        has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[NS(
            role="OWNER", scope_type="tenant", scope_id=c.tenant,
            status="active", expires_at=None,
        )]),
    )
    c.service = ConstructionProjectAreaReadService(
        repository=c.areas, project_repository=c.projects,
        access_repository=c.access,
    )
    c.suite = NS(require_enabled=AsyncMock())
    c.module = NS(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_project_area_read_service] = lambda: c.service
    return c


async def get(c, *, identifier=None, params=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get(
            "/api/v1/construction/projects/" + (identifier or str(c.project_id)) + "/areas",
            params=params, headers={"X-Request-ID": "project-areas-test"},
        )


def no_write(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
    c.session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_project_area_read_returns_only_public_fields_in_repository_order(area_http):
    c = area_http
    second = NS(**{**vars(c.area), "id": uuid4(), "name": "Second room"})
    c.areas.list_active_areas.return_value = [second, c.area]
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body["data"]] == [str(second.id), str(c.area.id)]
    assert all(row["sort_order"] is None for row in body["data"])
    assert body["meta"] == {"capabilities": {
        "can_create_area": True, "can_edit_area": True,
    }}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "private-field" not in response.text and "created_by" not in response.text
    c.areas.list_active_areas.assert_awaited_once_with(
        tenant_id=c.tenant, project_id=c.project_id,
    )
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "planning", "active", "completed", "cancelled"])
async def test_project_area_read_does_not_require_mutable_project(area_http, status):
    c = area_http
    c.project.status = status
    response = await get(c)
    assert response.status_code == 200, response.text
    no_write(c)


@pytest.mark.asyncio
async def test_project_area_read_empty_existing_project_is_success(area_http):
    c = area_http
    c.areas.list_active_areas.return_value = []
    response = await get(c)
    assert response.status_code == 200 and response.json()["data"] == []
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "foreign", "deleted", "wrong_id"])
async def test_project_area_read_hides_unavailable_parent_before_area_query(area_http, failure):
    c = area_http
    if failure == "missing":
        c.projects.get_active_project.return_value = None
    elif failure == "foreign":
        c.project.tenant_id = uuid4()
    elif failure == "deleted":
        c.project.deleted_at = object()
    else:
        c.project.id = uuid4()
    response = await get(c)
    assert response.status_code == 404
    assert "Public room" not in response.text
    c.areas.list_active_areas.assert_not_awaited()
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["anonymous", "suite", "module", "membership", "permission", "scope"])
async def test_project_area_read_requires_normal_api_gates(area_http, failure):
    c = area_http
    if failure == "anonymous":
        async def denied():
            raise ApiHttpError(status_code=401, code="invalid_token", message="Unavailable.")
        c.app.dependency_overrides[get_current_actor] = denied
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "permission":
        c.context = ConstructionAccessContext(
            c.user, c.tenant, ("OWNER",), (), c.context.grants,
        )
    else:
        c.context = ConstructionAccessContext(
            c.user, c.tenant, ("PROJECT_MANAGER",), ("construction.projects.read",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", uuid4()),),
        )
    response = await get(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    c.areas.list_active_areas.assert_not_awaited()
    no_write(c)


@pytest.mark.asyncio
async def test_project_area_read_rechecks_grants_after_parent_lookup(area_http):
    c = area_http
    async def after_lookup(**kwargs):
        c.access.list_active_grants.return_value = []
        return c.project
    c.projects.get_active_project.side_effect = after_lookup
    response = await get(c)
    assert response.status_code == 403
    c.areas.list_active_areas.assert_not_awaited()
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "foreign_tenant", "foreign_project", "deleted", "duplicate",
    "version", "boolean_order", "blank_name", "unknown_type",
])
async def test_project_area_read_rejects_invalid_or_unbound_response(area_http, failure):
    c = area_http
    if failure == "foreign_tenant":
        c.area.tenant_id = uuid4()
    elif failure == "foreign_project":
        c.area.project_id = uuid4()
    elif failure == "deleted":
        c.area.deleted_at = object()
    elif failure == "duplicate":
        c.areas.list_active_areas.return_value = [c.area, c.area]
    elif failure == "version":
        c.area.row_version = True
    elif failure == "boolean_order":
        c.area.sort_order = False
    elif failure == "blank_name":
        c.area.name = " "
    else:
        c.area.area_type = "private-invalid-type"
    response = await get(c)
    assert response.status_code == 500
    assert "private-invalid-type" not in response.text
    assert "Public room" not in response.text
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"tenant_id": "foreign"}, {"limit": "10"}, {"measurement_id": "private"},
])
async def test_project_area_read_does_not_accept_caller_context_or_unsupported_query(area_http, params):
    c = area_http
    response = await get(c, params=params)
    assert response.status_code == 422
    c.areas.list_active_areas.assert_not_awaited()
    no_write(c)


@pytest.mark.asyncio
async def test_project_area_read_bad_uuid_has_no_repository_calls(area_http):
    c = area_http
    response = await get(c, identifier="private-invalid-id")
    assert response.status_code == 422
    assert "private-invalid-id" not in response.text
    c.projects.get_active_project.assert_not_awaited()
    c.areas.list_active_areas.assert_not_awaited()
    no_write(c)

# universal_area_editor_v1
