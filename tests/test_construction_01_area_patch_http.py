from construction_rate_limit_fixture import with_local_construction_rate_limit
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import api.routes.construction_area_patch as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_01_area_patch_workflow import patch_workflow_case
from test_construction_01_area_patch_service import patch_case
from test_construction_01_area_archive_transaction import archive_case
from test_construction_01_project_read_http import walk_routes


@pytest.fixture
def patch_http(patch_workflow_case):
    c = patch_workflow_case
    c.actor.permissions = ()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_area_patch_workflow] = lambda: c.workflow
    return c


async def patch(c, *, body=None, identifier=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="http://testserver",
    ) as client:
        return await client.patch(
            "/api/v1/construction/areas/" + (identifier or str(c.area.id)),
            params={"tenant_id": str(uuid4()), "project_id": str(uuid4())},
            json=body if body is not None else {"expected_row_version": 7, "name": " New room ", "sort_order": None},
            headers={"X-Request-ID": "area-patch-http"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant", "project"])
async def test_http_changes_confirmed_area_using_canonical_scope(patch_http, scope):
    c = patch_http
    if scope == "project":
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",), ("construction.projects.edit",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
    response = await patch(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == dict(
        id=str(c.area.id), tenant_id=str(c.actor.tenant_id), project_id=str(c.project_id),
        name="New room", sort_order=None, row_version=8,
    )
    assert body["meta"] == {}
    assert body["request_id"] == response.headers["X-Request-ID"] == "area-patch-http"
    assert c.area.status == "confirmed"
    c.repo.find_active_area_project_id.assert_awaited_once_with(
        tenant_id=c.actor.tenant_id, area_id=c.area.id,
        allowed_project_ids=None if scope == "tenant" else (c.project_id,),
    )
    c.events.create_event.assert_awaited_once()
    assert c.events.create_event.await_args.kwargs["trace_id"] == "area-patch-http"
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields,name,order,version", [
    ({"name": "New"}, "New", 4, 8),
    ({"sort_order": None}, "Original room", None, 8),
    ({"sort_order": -3}, "Original room", -3, 8),
    ({}, "Original room", 4, 7),
    ({"name": " Original room ", "sort_order": 4}, "Original room", 4, 7),
])
async def test_omission_null_and_no_op_have_distinct_meanings(patch_http, fields, name, order, version):
    c = patch_http
    response = await patch(c, body={"expected_row_version": 7, **fields})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["name"], data["sort_order"], data["row_version"]) == (name, order, version)
    assert c.events.create_event.await_count == (0 if version == 7 else 1)
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["anonymous", "inactive", "permission", "suite", "module", "tenant", "user", "document"])
async def test_denial_prevents_queries_and_audit(patch_http, failure):
    c = patch_http
    if failure == "anonymous":
        c.app.dependency_overrides.pop(get_current_actor)
    elif failure == "inactive":
        c.actor.status = "blocked"
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        grants = c.context.grants if failure != "document" else (ConstructionGrantContext("OWNER", "document", uuid4()),)
        c.context = ConstructionAccessContext(
            uuid4() if failure == "user" else c.actor.user_id,
            uuid4() if failure == "tenant" else c.actor.tenant_id,
            ("OWNER",), () if failure == "permission" else ("construction.projects.edit",), grants,
        )
    response = await patch(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    if failure in ("suite", "module"):
        assert response.json()["error"]["code"] == "MODULE_DISABLED"
    c.repo.find_active_area_project_id.assert_not_called()
    c.repo.update_area_fields.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"expected_row_version": True}, {"expected_row_version": "7"},
    {"expected_row_version": 0}, {"expected_row_version": 7.0},
    {"expected_row_version": 7, "name": None}, {"expected_row_version": 7, "name": "  "},
    {"expected_row_version": 7, "name": 3},
    {"expected_row_version": 7, "sort_order": True}, {"expected_row_version": 7, "sort_order": 4.0},
    {"expected_row_version": 7, "sort_order": "4"}, {"expected_row_version": 7, "sort_order": 2147483648},
    {"expected_row_version": 7, "tenant_id": str(uuid4())},
    {"expected_row_version": 7, "project_id": str(uuid4())},
    {"expected_row_version": 7, "status": "draft"}, {"expected_row_version": 7, "area_type": "OTHER"},
    {"expected_row_version": 7, "manage_transaction": False},
])
async def test_invalid_or_server_owned_input_is_rejected_before_workflow(patch_http, body):
    c = patch_http
    response = await patch(c, body=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    c.repo.find_active_area_project_id.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_uuid_is_not_echoed(patch_http):
    c = patch_http
    response = await patch(c, identifier="private-invalid-uuid")
    assert response.status_code == 422 and "private-invalid-uuid" not in response.text
    c.repo.find_active_area_project_id.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status,code", [
    ("stale", 409, "ROW_VERSION_CONFLICT"), ("terminal", 409, "conflict"),
    ("missing", 404, "not_found"), ("foreign_scope", 404, "not_found"),
    ("audit", 500, "internal_error"), ("commit", 500, "internal_error"),
])
async def test_domain_errors_and_rollback_survive_http(patch_http, failure, status, code):
    c = patch_http
    if failure == "stale":
        c.area.row_version = 8
    elif failure == "terminal":
        c.projects.get_active_project_for_update.side_effect = None
        c.projects.get_active_project_for_update.return_value.status = "completed"
    elif failure == "missing":
        c.repo.find_active_area_project_id.return_value = None
    elif failure == "foreign_scope":
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",), ("construction.projects.edit",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", uuid4()),),
        )
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-audit-detail")
    else:
        c.session.commit.side_effect = RuntimeError("private-commit-detail")
    response = await patch(c)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"] == "area-patch-http"
    assert "private-" not in response.text
    c.session.rollback.assert_awaited_once()
    if failure != "commit":
        c.session.commit.assert_not_called()
    if failure not in ("audit", "commit"):
        c.repo.update_area_fields.assert_not_called()
        c.events.create_event.assert_not_called()


def test_factory_openapi_and_unique_route_registration(patch_http):
    c = patch_http
    workflow = routes.get_area_patch_workflow(c.session)
    assert all(item.session is c.session for item in (
        workflow.service, workflow.service.repository, workflow.service.project_repository,
        workflow.service.audit_logger.event_repository,
    ))
    matches = [route for path, route in walk_routes(c.app)
               if path == "/api/v1/construction/areas/{id}" and "PATCH" in getattr(route, "methods", ())]
    assert len(matches) == 1 and matches[0].endpoint is routes.patch_area
    schema = c.app.openapi()
    operation = schema["paths"]["/api/v1/construction/areas/{id}"]["patch"]
    assert {"200", "401", "403", "404", "409", "422", "500"} <= set(operation["responses"])
    assert not any(p["name"].lower() == "idempotency-key" for p in operation["parameters"])
    request = schema["components"]["schemas"]["AreaPatchRequest"]
    assert request["required"] == ["expected_row_version"]
    assert set(request["properties"]) == {"expected_row_version", "name", "sort_order"}
    assert request["properties"]["name"]["type"] == "string"
    assert request["additionalProperties"] is False
