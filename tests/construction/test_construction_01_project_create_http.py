from construction_rate_limit_fixture import with_local_construction_rate_limit
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

import api.routes.construction_project_create as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_01_project_create_transaction import create_case
from test_construction_01_project_create_workflow import workflow_case
from test_construction_01_project_read_http import walk_routes


@pytest.fixture
def create_http(workflow_case):
    c = workflow_case
    c.foreign = uuid4()
    c.actor.permissions = ()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_project_create_workflow] = lambda: c.workflow
    return c


async def post(c, *, body=None, key=None, extra_headers=None):
    headers = {"X-Request-ID": "project-create-http"}
    if key is not None:
        headers["Idempotency-Key"] = key
    headers.update(extra_headers or {})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.post(
            "/api/v1/construction/projects",
            params={"tenant_id": str(c.foreign)},
            headers=headers,
            json=body if body is not None else {
                "name": "  Transaction project  ", "address_raw": "  Project address  ",
                "comment": "Project comment",
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "project-key"])
async def test_http_creates_using_real_service_workflow_and_canonical_actor(create_http, key):
    c = create_http
    response = await post(c, key=key)
    assert response.status_code == 201, response.text
    envelope = response.json()
    data = envelope["data"]
    assert data["tenant_id"] == str(c.tenant) and data["created_by"] == str(c.user)
    assert data["name"] == "Transaction project" and data["address_raw"] == "Project address"
    assert data["row_version"] == 1 and data["status"] == "draft"
    assert envelope["meta"] == {}
    assert envelope["request_id"] == response.headers["X-Request-ID"] == "project-create-http"
    assert not any(secret in response.text for secret in ("private-orm-field", "key_hash", "ciphertext"))
    c.events.create_event.assert_awaited_once()
    assert c.events.create_event.await_args.kwargs["trace_id"] == "project-create-http"
    assert len(c.records.records) == (0 if key is None else 1)
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_same_key_replays_and_changed_body_is_409(create_http):
    c = create_http
    first = await post(c, key="project-key")
    c.project.name, c.project.row_version = "Changed later", 2
    replay = await post(c, key="project-key")
    assert first.status_code == replay.status_code == 201
    assert first.json()["data"] == replay.json()["data"]
    conflict = await post(c, key="project-key", body={"name": "Different", "address_raw": "Address"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    c.repo.create_project.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "anonymous", "inactive", "permission", "foreign_context", "wrong_user",
    "suite", "module", "project_scope", "document_scope",
])
async def test_http_denies_before_business_writes(create_http, failure):
    c = create_http
    if failure == "anonymous":
        c.app.dependency_overrides.pop(get_current_actor)
    elif failure == "inactive":
        c.actor.status = "blocked"
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        tenant = c.foreign if failure == "foreign_context" else c.tenant
        user = uuid4() if failure == "wrong_user" else c.user
        permissions = () if failure == "permission" else ("construction.projects.create",)
        grants = c.context.grants
        if failure in ("project_scope", "document_scope"):
            grants = (ConstructionGrantContext(
                "OWNER", "project" if failure == "project_scope" else "document", uuid4(),
            ),)
        c.context = ConstructionAccessContext(user, tenant, ("OWNER",), permissions, grants)
    response = await post(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    if failure in ("suite", "module"):
        assert response.json()["error"]["code"] == "MODULE_DISABLED"
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()
    assert not c.records.records


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"name": " ", "address_raw": "Address"}, {"name": "P", "address_raw": ""},
    {"name": 42, "address_raw": "Address"}, {"name": "P", "address_raw": "A", "comment": 42},
    {"name": "P", "address_raw": "A", "client_id": "private-invalid-uuid"},
    {"name": "P", "address_raw": "A", "responsible_user_id": "private-invalid-uuid"},
    {"name": "P", "address_raw": "A", "tenant_id": str(uuid4())},
    {"name": "P", "address_raw": "A", "created_by": str(uuid4())},
    {"name": "P", "address_raw": "A", "status": "active"},
    {"name": "P", "address_raw": "A", "row_version": 2},
])
async def test_invalid_or_server_owned_body_fields_are_rejected(create_http, body):
    c = create_http
    response = await post(c, body=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "private-invalid-uuid" not in response.text
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", " ", "x" * 256])
async def test_supplied_invalid_key_is_422_before_writes(create_http, key):
    c = create_http
    response = await post(c, key=key)
    assert response.status_code == 422
    c.repo.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["client", "responsible", "audit", "projection", "processing", "deleted"])
async def test_http_maps_workflow_failures_and_preserves_atomicity(create_http, failure):
    c = create_http
    body = {"name": "Transaction project", "address_raw": "Project address", "comment": "Project comment"}
    key = None
    if failure == "client":
        body["client_id"] = str(uuid4())
        c.clients.get_active_client.return_value = None
    elif failure == "responsible":
        body["responsible_user_id"] = str(uuid4())
        c.access.has_active_platform_membership.return_value = False
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-audit-detail")
    elif failure == "projection":
        c.project.row_version = 0
    else:
        assert (await post(c, key="project-key")).status_code == 201
        key = "project-key"
        if failure == "processing":
            record = next(iter(c.records.records.values()))
            record.status, record.response_status = "processing", None
        else:
            c.project.deleted_at = c.project.updated_at
    response = await post(c, body=body if key is None else None, key=key)
    expected = 422 if failure in ("client", "responsible") else (
        409 if failure == "processing" else 404 if failure == "deleted" else 500
    )
    assert response.status_code == expected, response.text
    assert "private-audit-detail" not in response.text
    c.session.rollback.assert_awaited_once()
    if key is None:
        c.session.commit.assert_not_called()


def test_factory_requires_encryption_only_for_a_supplied_key(create_http, monkeypatch):
    c = create_http
    def forbidden():
        raise AssertionError("Unkeyed factory loaded encryption settings")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", forbidden)
    workflow = routes.get_project_create_workflow(session=c.session, idempotency_key=None)
    assert workflow.idempotency is None
    assert workflow.service.repository.session is c.session
    monkeypatch.setattr(
        routes.ApiIdempotencySettings, "from_env",
        lambda: SimpleNamespace(encryption_key=Fernet.generate_key().decode()),
    )
    keyed = routes.get_project_create_workflow(session=c.session, idempotency_key="project-key")
    assert keyed.idempotency.repository.session is c.session


def test_openapi_optional_key_and_unique_registration(create_http):
    c = create_http
    matches = [route for path, route in walk_routes(c.app)
               if path == "/api/v1/construction/projects" and "POST" in getattr(route, "methods", ())]
    assert len(matches) == 1 and matches[0].endpoint is routes.create_project
    schema = c.app.openapi()
    operation = schema["paths"]["/api/v1/construction/projects"]["post"]
    header = next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")
    assert header["in"] == "header" and header["required"] is False
    assert {"201", "401", "403", "404", "409", "422", "500"} <= set(operation["responses"])
    properties = schema["components"]["schemas"]["ProjectCreateRequest"]["properties"]
    assert set(properties) == {"name", "address_raw", "client_id", "responsible_user_id", "comment"}
    assert schema["components"]["schemas"]["ProjectCreateRequest"]["required"] == ["name", "address_raw"]
    assert schema["components"]["schemas"]["ProjectCreateRequest"]["additionalProperties"] is False
