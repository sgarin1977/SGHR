from construction_rate_limit_fixture import with_local_construction_rate_limit
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import api.routes.construction_client_read as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from test_construction_1a_client_card_read_service import card_case
from services.construction.construction_clients import ConstructionClientValidationError
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError


@pytest.fixture
def client_http(card_case):
    card = card_case
    c = SimpleNamespace(tenant=card.tenant, foreign=uuid4(), user=card.user, client_id=card.client, card=card)
    c.actor, c.context = card.actor, card.context
    c.client = card.client_row
    c.client.display_name = "Read client"
    c.client.phone = c.client.email = c.client.notes = None
    c.client.created_at = c.client.updated_at = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    c.client.private_marker, c.client.token_hash = "private-orm-field", "private-token-hash"
    c.session, c.repo, c.service = card.session, card.clients, card.service
    for name, method in [('projects', 'list_projects_for_client'), ('leads', 'list_active_leads'),
        ('activities', 'list_planned_activities_for_client'), ('history', 'list_history_for_client')]:
        getattr(card.repositories[name], method).return_value = []
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_client_read_service] = lambda: c.service
    return c


async def get(c, *, identifier=None, params=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get(
            "/api/v1/construction/clients/" + (identifier or str(c.client_id)),
            params={"tenant_id": str(c.foreign), **(params or {})},
            headers={"X-Request-ID": "client-read-http"},
        )


def no_write(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
    c.session.flush.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", ["person", "company"])
async def test_get_client_uses_real_service_and_explicit_projection(client_http, client_type):
    c = client_http
    c.client.client_type = client_type
    c.client.phone, c.client.email, c.client.notes = "+12025550100", "client@example.com", "Client note"
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"] == {"pagination": {name+"_next_cursor": None
        for name in ("projects", "leads", "activities", "history")}}
    assert body["request_id"] == response.headers["X-Request-ID"] == "client-read-http"
    assert body["data"] == {
        "id": str(c.client_id), "tenant_id": str(c.tenant),
        "display_name": "Read client", "client_type": client_type,
        "phone": "+12025550100", "email": "client@example.com", "notes": "Client note",
        "created_at": "2026-10-08T10:00:00Z", "updated_at": "2026-10-08T10:00:00Z",
        "linked_user_id": None, "row_version": 1,
        "projects": [], "leads": [], "activities": [], "history": [],
    }
    assert "private-orm-field" not in response.text and "private-token-hash" not in response.text
    assert c.repo.get_active_client.await_count >= 2
    assert all(call.kwargs == dict(tenant_id=c.tenant, client_id=c.client_id)
        for call in c.repo.get_active_client.await_args_list)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "anonymous", "inactive", "permission", "foreign_context", "wrong_user",
    "suite", "module", "project_only", "document_only",
])
async def test_http_denies_before_client_query(client_http, failure):
    c = client_http
    if failure == "anonymous": c.app.dependency_overrides.pop(get_current_actor)
    elif failure == "inactive": c.actor.status = "blocked"
    elif failure == "suite": c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module": c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        tenant = c.foreign if failure == "foreign_context" else c.tenant
        user = uuid4() if failure == "wrong_user" else c.user
        permissions = () if failure == "permission" else ("construction.projects.read",)
        grants = c.context.grants
        if failure == "project_only": grants = (ConstructionGrantContext("OWNER", "project", uuid4()),)
        elif failure == "document_only": grants = (ConstructionGrantContext("OWNER", "document", uuid4()),)
        c.context = ConstructionAccessContext(user, tenant, ("OWNER",), permissions, grants)
    response = await get(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    if failure in ("suite", "module"):
        assert response.json()["error"]["code"] == "MODULE_DISABLED"
    if failure == "anonymous":
        assert response.headers["WWW-Authenticate"] == "Bearer"
    c.repo.get_active_client.assert_not_called()
    no_write(c)


@pytest.mark.asyncio
async def test_bad_uuid_is_422_without_query_or_echoing_input(client_http):
    c = client_http
    response = await get(c, identifier="private-invalid-uuid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "private-invalid-uuid" not in response.text
    c.repo.get_active_client.assert_not_called()
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "foreign", "deleted", "wrong_id"])
async def test_unavailable_clients_are_not_found(client_http, failure):
    c = client_http
    if failure == "missing": c.repo.get_active_client.return_value = None
    elif failure == "foreign": c.client.tenant_id = c.foreign
    elif failure == "deleted": c.client.deleted_at = c.client.updated_at
    else: c.client.id = uuid4()
    response = await get(c)
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    assert "Read client" not in response.text
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"] == "client-read-http"
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("client_type", "private-invalid-type"), ("display_name", None),
    ("created_at", datetime(2026, 10, 8)), ("updated_at", datetime(2026, 10, 8)),
    ("phone", 123), ("email", {"private": "detail"}),
])
async def test_invalid_repository_projection_is_generic_500(client_http, field, value):
    c = client_http
    setattr(c.client, field, value)
    response = await get(c)
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert "private-invalid-type" not in response.text and "Read client" not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_database_failure_is_generic_500(client_http):
    c = client_http
    c.repo.get_active_client.side_effect = RuntimeError("private-database-detail")
    response = await get(c)
    assert response.status_code == 500 and "private-database-detail" not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_service_validation_error_is_422_without_detail(client_http):
    c = client_http
    c.service = SimpleNamespace(get_client_card=AsyncMock(
        side_effect=ConstructionClientValidationError("private-validation-detail")
    ))
    response = await get(c)
    assert response.status_code == 422 and "private-validation-detail" not in response.text
    no_write(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign", "wrong_id", "extra_private_field", "missing_field"])
async def test_projection_rechecks_context_after_service(client_http, failure):
    c = client_http
    result = await c.service.get_client_card(actor=c.actor, access_context=c.context, client_id=c.client_id)
    client = dict(result.client)
    if failure == "foreign": client["tenant_id"] = str(c.foreign)
    elif failure == "wrong_id": client["id"] = str(uuid4())
    elif failure == "extra_private_field": client["token_hash"] = "private-token-hash"
    else: del client["notes"]
    c.service = SimpleNamespace(get_client_card=AsyncMock(return_value=replace(result, client=client)))
    response = await get(c)
    assert response.status_code == 500 and "Read client" not in response.text
    assert "private-token-hash" not in response.text
    no_write(c)


@pytest.mark.asyncio
async def test_nullable_contacts_utc_dates_and_repeated_reads(client_http):
    c = client_http
    c.client.created_at = c.client.created_at.astimezone(timezone(timedelta(hours=3)))
    original = dict(vars(c.client))
    first, second = await get(c), await get(c)
    assert first.status_code == second.status_code == 200
    data = first.json()["data"]
    assert data["created_at"] == data["updated_at"] == "2026-10-08T10:00:00Z"
    assert data["phone"] is data["email"] is data["notes"] is None
    assert dict(vars(c.client)) == original
    no_write(c)


def walk_routes(router, prefix=""):
    for route in router.routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            yield from walk_routes(original, prefix + getattr(context, "prefix", ""))
        elif hasattr(route, "path"):
            yield prefix + route.path, route


def test_client_read_registration_openapi_and_permission_dependencies(client_http):
    c = client_http
    service = routes.get_client_read_service(c.session)
    assert service.session is service.client_service.repository.session is c.session
    assert all(reader.session is c.session for reader in service.sections.values())
    matches = [
        route for path, route in walk_routes(c.app)
        if path == "/api/v1/construction/clients/{id}" and "GET" in getattr(route, "methods", ())
    ]
    assert len(matches) == 1 and matches[0].endpoint is routes.read_client
    target = next(r for r in routes.router.routes if getattr(r, "path", None) == "/construction/clients/{id}")
    codes = [
        getattr(d.dependency, "construction_permission_code", None)
        for d in target.dependencies
    ]
    assert [code for code in codes if code] == ["construction.projects.read"]
    assert get_current_actor in [d.dependency for d in routes.router.dependencies]
    assert routes.get_client_read_service.__defaults__[0].dependency is get_construction_scoped_session
    schema = c.app.openapi()
    operation = schema["paths"]["/api/v1/construction/clients/{id}"]["get"]
    parameter = next(p for p in operation["parameters"] if p["name"] == "id")
    assert parameter["in"] == "path" and parameter["schema"]["format"] == "uuid"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ClientReadEnvelope")
    assert {"401", "403", "404", "422", "500"} <= set(operation["responses"])
    assert not any(p["name"].lower() == "idempotency-key" for p in operation.get("parameters", []))
    properties = schema["components"]["schemas"]["ClientCardResponse"]["properties"]
    assert set(properties) == {
        "id", "tenant_id", "display_name", "client_type", "phone", "email", "notes",
        "created_at", "updated_at", "projects", "leads", "activities", "history", "linked_user_id", "row_version",
    }
