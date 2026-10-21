from construction_rate_limit_fixture import with_local_construction_rate_limit
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

import api.routes.construction_lead_create as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_lead_create_service import lead_create_case
from test_construction_1a_lead_create_workflow import workflow_case
from test_construction_01_project_read_http import walk_routes


@pytest.fixture
def lead_http(workflow_case):
    c = workflow_case
    c.foreign = uuid4()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_lead_create_workflow] = lambda: c.workflow
    return c


def request_body():
    return dict(source=" Partner_Portal ", name=" Private client ",
        work_type_description=" Private work description ", email=" private@example.com ", comment="private-note")


async def post(c, *, body=None, key="lead-http-key", extra_headers=None):
    headers = [("X-Request-ID", "lead-create-http"), ("X-Tenant-ID", str(c.foreign))]
    if key is not None:
        headers.append(("Idempotency-Key", key))
    headers.extend(extra_headers or [])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="http://testserver",
    ) as client:
        return await client.post("/api/v1/construction/crm/leads", json=request_body() if body is None else body,
            params={"tenant_id": str(c.foreign)}, headers=headers)


@pytest.mark.asyncio
async def test_http_uses_real_workflow_actor_tenant_shared_access_and_audit(lead_http):
    c = lead_http
    response = await post(c)
    assert response.status_code == 201, response.text
    envelope = response.json()
    data = envelope["data"]
    assert data["tenant_id"] == str(c.tenant) and data["id"] == str(c.row.id)
    assert (data["source"], data["name"], data["work_type_description"]) == (
        "partner_portal", "Private client", "Private work description")
    assert data["email"] == "private@example.com" and data["phone"] is None
    assert data["status"] == "new" and data["row_version"] == 1
    assert data["converted_client_id"] is None and data["converted_project_id"] is None
    assert envelope["meta"] == {}
    assert envelope["request_id"] == response.headers["X-Request-ID"] == "lead-create-http"
    assert not any(secret in response.text for secret in ("private-internal", "key_hash", "binding", "ciphertext", "lead-http-key"))
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    assert c.events.create_event.await_args.kwargs["trace_id"] == "lead-create-http"
    c.events.create_event.assert_awaited_once()
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_http_replays_original_and_conflicts_on_changed_raw_body(lead_http):
    c = lead_http
    first = await post(c)
    c.row.name, c.row.status, c.row.row_version = "Later", "contacted", 2
    replay = await post(c)
    assert first.status_code == replay.status_code == 201
    assert first.json()["data"] == replay.json()["data"]
    body = request_body()
    body["name"] = "Private client"
    conflict = await post(c, body=body)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    c.repo.create_lead.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", " ", "x"*256])
async def test_http_requires_valid_key_before_reservation_or_writes(lead_http, key):
    c = lead_http
    response = await post(c, key=key)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert not c.records.rows
    c.repo.create_lead.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_idempotency_headers_are_rejected(lead_http):
    c = lead_http
    response = await post(c, extra_headers=[("Idempotency-Key", "second")])
    assert response.status_code == 422
    assert not c.records.rows
    c.repo.create_lead.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "anonymous", "inactive", "permission", "foreign_context", "wrong_user",
    "suite", "module", "project_scope", "document_scope", "illegal_tenant",
])
async def test_denial_prevents_business_writes(lead_http, failure):
    c = lead_http
    if failure == "anonymous":
        c.app.dependency_overrides.pop(get_current_actor)
    elif failure == "inactive":
        c.actor.status = "blocked"
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        user = uuid4() if failure == "wrong_user" else c.user
        tenant = c.foreign if failure == "foreign_context" else c.tenant
        role = "PROJECT_MANAGER" if failure == "illegal_tenant" else "OWNER"
        scope = "project" if failure == "project_scope" else "document" if failure == "document_scope" else "tenant"
        c.context = ConstructionAccessContext(user, tenant, (role,),
            () if failure == "permission" else ("construction.projects.create",),
            (ConstructionGrantContext(role, scope, c.tenant),))
    response = await post(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    if failure in ("suite", "module"):
        assert response.json()["error"]["code"] == "MODULE_DISABLED"
    assert not c.records.rows
    c.repo.create_lead.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"source": None}, {"source": 17}, {"source": " "}, {"name": None}, {"name": 17}, {"name": " "},
    {"work_type_description": None}, {"work_type_description": 17}, {"work_type_description": " "},
    {"phone": 17}, {"email": False}, {"email": " ", "phone": None}, {"comment": {}}, {"address_raw": []},
    {"responsible_user_id": "private-invalid-uuid"}, {"tenant_id": str(uuid4())},
    {"id": str(uuid4())}, {"status": "won"}, {"row_version": 3}, {"created_at": "2026-10-12"},
    {"converted_client_id": str(uuid4())}, {"master_work_type_id": str(uuid4())},
    {"organization_work_type_id": str(uuid4())}, {"work_type": "Old"},
])
async def test_invalid_and_server_owned_fields_are_rejected(lead_http, changes):
    c = lead_http
    body = request_body()
    body.update(changes)
    response = await post(c, body=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert "private-invalid-uuid" not in response.text
    assert not c.records.rows
    c.repo.create_lead.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source", "name", "work_type_description"])
async def test_missing_required_body_fields_are_rejected(lead_http, field):
    c = lead_http
    body = request_body()
    body.pop(field)
    assert (await post(c, body=body)).status_code == 422
    c.repo.create_lead.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "expired", "responsible", "audit", "complete", "commit", "processing", "deleted"])
async def test_http_maps_current_access_and_workflow_errors_without_private_details(lead_http, failure):
    c = lead_http
    body = request_body()
    if failure in ("expired", "processing", "deleted"):
        assert (await post(c)).status_code == 201
    if failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "expired":
        c.grant.expires_at = c.now
    elif failure == "responsible":
        body["responsible_user_id"] = str(uuid4())
        c.access.has_active_platform_membership.side_effect = [True, True, False]
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-audit-detail")
    elif failure == "complete":
        c.records.fail_complete = True
    elif failure == "commit":
        c.session.commit.side_effect = RuntimeError("private-commit-detail")
    elif failure == "processing":
        record = next(iter(c.records.rows.values()))
        record.status, record.response_status, record.response_ciphertext = "processing", None, None
    else:
        c.row.deleted_at = c.now
    response = await post(c, body=body)
    expected = 403 if failure in ("membership", "expired") else 422 if failure == "responsible" else (
        409 if failure == "processing" else 404 if failure == "deleted" else 500)
    assert response.status_code == expected, response.text
    assert "private-" not in response.text
    c.session.rollback.assert_awaited_once()
    if failure not in ("expired", "processing", "deleted", "commit"):
        c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_http_accepts_phone_contact_extensible_source_and_nullable_optional_fields(lead_http):
    c = lead_http
    response = await post(c, body=dict(source="new_marketing_channel", name="Client",
        work_type_description="Ремонт", phone=" +351 123 ", email=None,
        comment=" ", address_raw=None, responsible_user_id=None))
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["source"] == "new_marketing_channel" and data["phone"] == "+351 123"
    assert data["email"] is None and data["comment"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extra", "tenant", "version"])
async def test_route_rejects_invalid_workflow_public_response(lead_http, failure):
    c = lead_http
    response = (await post(c)).json()["data"]
    if failure == "extra":
        response["private_internal"] = "private-response"
    elif failure == "tenant":
        response["tenant_id"] = str(c.foreign)
    else:
        response["row_version"] = True
    fake = SimpleNamespace(create_lead=AsyncMock(return_value=response))
    c.app.dependency_overrides[routes.get_lead_create_workflow] = lambda: fake
    result = await post(c)
    assert result.status_code == 500 and "private-response" not in result.text


def test_factory_uses_existing_crypto_configuration_and_scoped_session(lead_http, monkeypatch):
    c = lead_http
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", lambda: SimpleNamespace(encryption_key=key))
    result = routes.get_lead_create_workflow(session=c.session, idempotency_key="key")
    assert result.service.session is c.session and result.idempotency.repository.session is c.session
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", lambda: SimpleNamespace(encryption_key=""))
    with pytest.raises(ValueError):
        routes.get_lead_create_workflow(session=c.session, idempotency_key="key")


def test_openapi_required_key_strict_body_permission_and_unique_registration(lead_http):
    c = lead_http
    found = [route for path, route in walk_routes(c.app)
        if path == "/api/v1/construction/crm/leads" and "POST" in getattr(route, "methods", ())]
    assert len(found) == 1 and found[0].endpoint is routes.create_lead
    assert routes.create_permission.construction_permission_code == "construction.projects.create"
    schema = c.app.openapi()
    operation = schema["paths"]["/api/v1/construction/crm/leads"]["post"]
    key = next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")
    assert key["in"] == "header" and key["required"] is True
    assert {"201", "401", "403", "404", "409", "422", "500"} <= set(operation["responses"])
    model = schema["components"]["schemas"]["LeadCreateRequest"]
    assert set(model["properties"]) == {
        "source", "name", "work_type_description", "phone", "email", "address_raw", "comment", "responsible_user_id"}
    assert model["required"] == ["source", "name", "work_type_description"]
    assert model["additionalProperties"] is False
    assert operation["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/LeadCreateEnvelope")


def test_http_boundary_uses_scoped_session_key_dependency_and_service_layer_only():
    import ast
    from pathlib import Path
    assert routes.get_lead_create_workflow.__defaults__[0].dependency is routes.get_construction_scoped_session
    assert routes.get_lead_create_workflow.__defaults__[1].dependency is routes.require_lead_create_key
    tree = ast.parse(Path(routes.__file__).read_text(encoding="utf-8-sig"))
    forbidden = ("database.repositories", "aiogram", "handlers")
    for node in ast.walk(tree):
        modules = [item.name for item in node.names] if isinstance(node, ast.Import) else (
            [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        assert not any(module.startswith(forbidden) for module in modules)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"commit", "rollback", "flush"}
