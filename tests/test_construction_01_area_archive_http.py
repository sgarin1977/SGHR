from construction_rate_limit_fixture import with_local_construction_rate_limit
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import api.routes.construction_area_archive as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_01_area_archive_workflow import workflow_case
from test_construction_01_area_archive_transaction import archive_case
from test_construction_01_project_read_http import walk_routes


@pytest.fixture
def archive_http(workflow_case):
    c = workflow_case
    c.actor.permissions = ()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_area_archive_workflow] = lambda: c.workflow
    return c


async def post(c, *, body=None, identifier=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="http://testserver",
    ) as client:
        return await client.post(
            "/api/v1/construction/areas/" + (identifier or str(c.area.id)) + "/archive",
            params={"tenant_id": str(uuid4()), "project_id": str(uuid4())},
            json=body if body is not None else {"expected_row_version": 7},
            headers={"X-Request-ID": "area-archive-http"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant", "project"])
async def test_http_archives_with_real_workflow_and_canonical_scope(archive_http, scope):
    c = archive_http
    if scope == "project":
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",), ("construction.projects.edit",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
    response = await post(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == {
        "id": str(c.area.id), "tenant_id": str(c.actor.tenant_id), "project_id": str(c.project_id),
        "deleted_at": "2026-10-09T00:00:00Z", "row_version": 8,
    }
    assert body["meta"] == {}
    assert body["request_id"] == response.headers["X-Request-ID"] == "area-archive-http"
    c.repo.find_active_area_project_id.assert_awaited_once_with(
        tenant_id=c.actor.tenant_id, area_id=c.area.id,
        allowed_project_ids=None if scope == "tenant" else (c.project_id,),
    )
    c.events.create_event.assert_awaited_once()
    assert c.events.create_event.await_args.kwargs["trace_id"] == "area-archive-http"
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["anonymous", "inactive", "permission", "suite", "module", "foreign_context", "wrong_user", "document"])
async def test_denial_prevents_archive_queries_and_audit(archive_http, failure):
    c = archive_http
    if failure == "anonymous":
        c.app.dependency_overrides.pop(get_current_actor)
    elif failure == "inactive":
        c.actor.status = "blocked"
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        tenant = uuid4() if failure == "foreign_context" else c.actor.tenant_id
        user = uuid4() if failure == "wrong_user" else c.actor.user_id
        permissions = () if failure == "permission" else ("construction.projects.edit",)
        grants = c.context.grants if failure != "document" else (ConstructionGrantContext("OWNER", "document", uuid4()),)
        c.context = ConstructionAccessContext(user, tenant, ("OWNER",), permissions, grants)
    response = await post(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    if failure in ("suite", "module"):
        assert response.json()["error"]["code"] == "MODULE_DISABLED"
    c.repo.find_active_area_project_id.assert_not_called()
    c.repo.soft_delete_area.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"expected_row_version": 0}, {"expected_row_version": True},
    {"expected_row_version": "7"}, {"expected_row_version": 7.0}, {"expected_row_version": None},
    {"expected_row_version": 7, "tenant_id": str(uuid4())},
    {"expected_row_version": 7, "project_id": str(uuid4())},
    {"expected_row_version": 7, "manage_transaction": False},
])
async def test_invalid_or_server_owned_input_is_rejected_before_workflow(archive_http, body):
    c = archive_http
    response = await post(c, body=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    c.repo.find_active_area_project_id.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_area_uuid_is_422_without_echo(archive_http):
    c = archive_http
    response = await post(c, identifier="private-invalid-uuid")
    assert response.status_code == 422 and "private-invalid-uuid" not in response.text
    c.repo.find_active_area_project_id.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status,code", [
    ("stale", 409, "ROW_VERSION_CONFLICT"), ("terminal", 409, "conflict"),
    ("confirmed", 409, "conflict"), ("elements", 409, "conflict"),
    ("missing", 404, "not_found"), ("audit", 500, "internal_error"),
])
async def test_http_preserves_domain_restrictions_and_rollback(archive_http, failure, status, code):
    c = archive_http
    if failure == "stale":
        c.area.row_version = 8
    elif failure == "terminal":
        c.projects.get_active_project_for_update.return_value.status = "completed"
    elif failure == "confirmed":
        c.area.status = "confirmed"
    elif failure == "elements":
        c.repo.has_active_elements.return_value = True
    elif failure == "missing":
        c.repo.find_active_area_project_id.return_value = None
    else:
        c.events.create_event.side_effect = RuntimeError("private-audit-detail")
    response = await post(c)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"] == "area-archive-http"
    assert "private-audit-detail" not in response.text
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()
    if failure != "audit":
        c.repo.soft_delete_area.assert_not_called()
        c.events.create_event.assert_not_called()


def test_factory_openapi_and_unique_route_registration(archive_http):
    c = archive_http
    workflow = routes.get_area_archive_workflow(c.session)
    assert workflow.service.repository.session is c.session
    assert workflow.service.project_repository.session is c.session
    assert workflow.service.audit_logger.event_repository.session is c.session
    matches = [route for path, route in walk_routes(c.app)
               if path == "/api/v1/construction/areas/{id}/archive" and "POST" in getattr(route, "methods", ())]
    assert len(matches) == 1 and matches[0].endpoint is routes.archive_area
    schema = c.app.openapi()
    operation = schema["paths"]["/api/v1/construction/areas/{id}/archive"]["post"]
    assert {"200", "401", "403", "404", "409", "422", "500"} <= set(operation["responses"])
    assert not any(p["name"].lower() == "idempotency-key" for p in operation["parameters"])
    request = schema["components"]["schemas"]["AreaArchiveRequest"]
    assert request["required"] == ["expected_row_version"]
    assert set(request["properties"]) == {"expected_row_version"}
    assert request["additionalProperties"] is False
