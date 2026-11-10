from construction_rate_limit_fixture import with_local_construction_rate_limit
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import api.routes.construction_project_read as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.construction.construction_project_read import ConstructionProjectReadService
from services.construction.construction_projects import ConstructionProjectValidationError
from services.module_access import ModuleDisabledError, SuiteDisabledError


@pytest.fixture
def project_http():
    c = SimpleNamespace(tenant=uuid4(), foreign=uuid4(), user=uuid4(), project_id=uuid4())
    c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status="active", permissions=())
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OWNER",), ("construction.projects.read",),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),),
    )
    now = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
    c.project = SimpleNamespace(
        id=c.project_id, tenant_id=c.tenant, client_id=None, name="Read project", status="draft",
        responsible_user_id=None, comment=None, address_raw="Read address",
        address_formatted=None, country_code=None, region=None, city=None, postal_code=None,
        latitude=Decimal("38.123456789012345678"), longitude=Decimal("-9.123456789012345678"),
        address_provider=None, provider_place_id=None, address_verification_status="pending",
        created_by=c.user, created_at=now, updated_at=now, deleted_at=None, row_version=7,
        currency=None, private_marker="private-orm-field", token_hash="private-token-hash",
    )
    c.session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock())
    c.repo = SimpleNamespace(session=c.session, get_active_project=AsyncMock(return_value=c.project))
    c.service = ConstructionProjectReadService(repository=c.repo)
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_project_read_service] = lambda: c.service
    return c


async def get(c, *, identifier=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get(
            "/api/v1/construction/projects/" + (identifier or str(c.project_id)),
            params={"tenant_id": str(c.foreign)},
            headers={"X-Request-ID": "project-read-http"},
        )


def no_write(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
    c.session.flush.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant", "project"])
async def test_get_project_uses_real_service_and_actor_tenant(project_http, scope):
    c = project_http
    if scope == "project":
        c.context = ConstructionAccessContext(
            c.user, c.tenant, ("PROJECT_MANAGER",), ("construction.projects.read",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"] == {"capabilities": {
        "can_edit_project": False,
        "can_plan_activity": False,
        "can_open_team": False,
    }}
    assert body["request_id"] == response.headers["X-Request-ID"] == "project-read-http"
    data = body["data"]
    assert data["id"] == str(c.project_id) and data["tenant_id"] == str(c.tenant)
    assert data["client_id"] is None and data["responsible_user_id"] is None
    assert data["row_version"] == 7 and data["status"] == "draft"
    assert data["latitude"] == "38.123456789012345678"
    assert data["longitude"] == "-9.123456789012345678"
    assert "private-orm-field" not in response.text and "private-token-hash" not in response.text
    assert "deleted_at" not in data and "private_marker" not in data and "token_hash" not in data
    c.repo.get_active_project.assert_awaited_once_with(tenant_id=c.tenant, project_id=c.project_id)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["planning", "active", "completed", "cancelled"])
async def test_project_lifecycle_state_does_not_hide_undeleted_project(project_http, status):
    c = project_http
    c.project.status = status
    response = await get(c)
    assert response.status_code == 200 and response.json()["data"]["status"] == status
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "anonymous", "inactive", "permission", "foreign_context", "wrong_user",
    "suite", "module", "wrong_project", "document_only",
])
async def test_http_denies_before_project_query(project_http, failure):
    c = project_http
    if failure == "anonymous": c.app.dependency_overrides.pop(get_current_actor)
    elif failure == "inactive": c.actor.status = "blocked"
    elif failure == "suite": c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module": c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        tenant = c.foreign if failure == "foreign_context" else c.tenant
        user = uuid4() if failure == "wrong_user" else c.user
        permissions = () if failure == "permission" else ("construction.projects.read",)
        grants = c.context.grants
        if failure == "wrong_project": grants = (ConstructionGrantContext("OWNER", "project", uuid4()),)
        elif failure == "document_only": grants = (ConstructionGrantContext("OWNER", "document", uuid4()),)
        c.context = ConstructionAccessContext(user, tenant, ("OWNER",), permissions, grants)
    response = await get(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    if failure in ("suite", "module"):
        assert response.json()["error"]["code"] == "MODULE_DISABLED"
    if failure == "anonymous":
        assert response.headers["WWW-Authenticate"] == "Bearer"
    c.repo.get_active_project.assert_not_called()
    no_write(c)


@pytest.mark.asyncio
async def test_bad_uuid_is_422_without_query_or_echoing_input(project_http):
    c = project_http
    response = await get(c, identifier="private-invalid-uuid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "private-invalid-uuid" not in response.text
    c.repo.get_active_project.assert_not_called()
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "foreign", "deleted", "wrong_id"])
async def test_missing_foreign_and_deleted_projects_are_not_found(project_http, failure):
    c = project_http
    if failure == "missing": c.repo.get_active_project.return_value = None
    elif failure == "foreign": c.project.tenant_id = c.foreign
    elif failure == "deleted": c.project.deleted_at = c.project.updated_at
    else: c.project.id = uuid4()
    response = await get(c)
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    assert "Read project" not in response.text
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"] == "project-read-http"
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("row_version", 0), ("row_version", True), ("status", "private-invalid-status"),
    ("created_at", datetime(2026, 10, 8)), ("latitude", Decimal("NaN")), ("name", None),
])
async def test_invalid_repository_projection_is_generic_500(project_http, field, value):
    c = project_http
    setattr(c.project, field, value)
    response = await get(c)
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert "private-invalid-status" not in response.text and "Read project" not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_database_failure_is_generic_500(project_http):
    c = project_http
    c.repo.get_active_project.side_effect = RuntimeError("private-database-detail")
    response = await get(c)
    assert response.status_code == 500
    assert "private-database-detail" not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_service_validation_error_is_422_without_detail(project_http):
    c = project_http
    c.service = SimpleNamespace(get_project=AsyncMock(
        side_effect=ConstructionProjectValidationError("private-validation-detail")
    ))
    response = await get(c)
    assert response.status_code == 422 and "private-validation-detail" not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_projection_rechecks_context_after_service(project_http):
    c = project_http
    c.project.tenant_id = c.foreign
    c.service = SimpleNamespace(get_project=AsyncMock(return_value=c.project))
    response = await get(c)
    assert response.status_code == 500 and "Read project" not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_response_dates_are_utc_and_reads_do_not_change_version(project_http):
    c = project_http
    c.project.created_at = c.project.created_at.astimezone(timezone(timedelta(hours=3)))
    original = (c.project.row_version, c.project.updated_at, c.project.name)
    first, second = await get(c), await get(c)
    assert first.status_code == second.status_code == 200
    data = first.json()["data"]
    assert data["created_at"] == data["updated_at"] == "2026-10-08T10:00:00Z"
    assert (c.project.row_version, c.project.updated_at, c.project.name) == original
    no_write(c)


def test_project_read_factory_and_openapi_registration(project_http):
    c = project_http
    service = routes.get_project_read_service(c.session)
    assert service.repository.session is c.session
    paths = c.app.openapi()["paths"]
    path = "/api/v1/construction/projects/{id}"
    assert path in paths and "get" in paths[path]
    operation = paths[path]["get"]
    parameter = next(p for p in operation["parameters"] if p["name"] == "id")
    assert parameter["in"] == "path" and parameter["schema"]["format"] == "uuid"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProjectDetailsEnvelope")
    assert {"401", "403", "404", "422", "500"} <= set(operation["responses"])
    assert not any(p["name"].lower() == "idempotency-key" for p in operation.get("parameters", []))
    fields = c.app.openapi()["components"]["schemas"]["ProjectDetailsResponse"]["properties"]
    assert "row_version" in fields and "deleted_at" not in fields and "token_hash" not in fields


def walk_routes(router, prefix=""):
    for route in router.routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            yield from walk_routes(original, prefix + getattr(context, "prefix", ""))
        elif hasattr(route, "path"):
            yield prefix + route.path, route


def test_project_route_is_registered_once(project_http):
    matches = [
        route for path, route in walk_routes(project_http.app)
        if path == "/api/v1/construction/projects/{id}"
        and "GET" in getattr(route, "methods", ())
    ]
    assert len(matches) == 1 and matches[0].endpoint is routes.read_project

# project_action_visibility_v1
@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope,expected", [
    ("OWNER", "tenant", (True, True, True)),
    ("ADMIN", "tenant", (True, True, True)),
    ("PROJECT_MANAGER", "project", (True, True, True)),
    ("SURVEYOR", "project", (False, False, False)),
    ("ESTIMATOR", "project", (False, False, False)),
    ("EXECUTOR", "project", (False, False, False)),
    ("OBSERVER", "project", (False, False, False)),
])
async def test_project_actions_follow_existing_scoped_policy(
    project_http, role, scope, expected,
):
    from services.construction.construction_permissions import CONSTRUCTION_ROLE_PERMISSIONS
    c = project_http
    c.context = ConstructionAccessContext(
        c.user, c.tenant, (role,), tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        (ConstructionGrantContext(
            role, scope, c.tenant if scope == "tenant" else c.project_id,
        ),),
    )
    response = await get(c)
    assert response.status_code == 200, response.text
    caps = response.json()["meta"]["capabilities"]
    assert caps == dict(zip(
        ("can_edit_project", "can_plan_activity", "can_open_team"), expected,
    ))
    no_write(c)


@pytest.mark.asyncio
async def test_other_project_authority_does_not_enable_current_project_actions(project_http):
    from services.construction.construction_permissions import CONSTRUCTION_ROLE_PERMISSIONS
    c = project_http
    roles = ("OBSERVER", "PROJECT_MANAGER")
    permissions = tuple(sorted(set().union(*(
        CONSTRUCTION_ROLE_PERMISSIONS[role] for role in roles
    ))))
    c.context = ConstructionAccessContext(
        c.user, c.tenant, roles, permissions,
        (
            ConstructionGrantContext("OBSERVER", "project", c.project_id),
            ConstructionGrantContext("PROJECT_MANAGER", "project", uuid4()),
        ),
    )
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["capabilities"] == {
        "can_edit_project": False,
        "can_plan_activity": False,
        "can_open_team": False,
    }
    no_write(c)
