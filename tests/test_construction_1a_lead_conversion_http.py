from construction_rate_limit_fixture import with_local_construction_rate_limit
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

import api.routes.construction_lead_conversion as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.routes.construction import get_construction_access_context
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_lead_conversion_service import conversion_case
from test_construction_1a_lead_conversion_workflow import workflow_case
from test_construction_01_project_read_http import walk_routes


@pytest.fixture
def convert_http(workflow_case):
    c = workflow_case
    c.foreign = uuid4()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_lead_conversion_workflow] = lambda: c.workflow
    return c


def body(c, mode="new"):
    data = dict(expected_row_version=4)
    if mode == "new":
        data.update(client_type=" PERSON ", project_name=" Project name ")
    elif mode == "client":
        data.update(client_id=str(c.client.id), project_name=" Project name ")
    else:
        data.update(client_id=str(c.client.id), project_id=str(c.project.id))
    return data


async def post(c, *, data=None, mode="new", key="convert-http-key", identifier=None, extra_headers=None):
    headers = [("X-Request-ID", "lead-convert-http"), ("X-Tenant-ID", str(c.foreign))]
    if key is not None:
        headers.append(("Idempotency-Key", key))
    headers.extend(extra_headers or [])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver") as client:
        return await client.post("/api/v1/construction/crm/leads/" + (identifier or str(c.lead.id)) + "/convert",
            json=body(c, mode) if data is None else data, params={"tenant_id": str(c.foreign)}, headers=headers)


def no_mutation(c):
    c.repo.complete_conversion.assert_not_called()
    c.clients.create_client.assert_not_called()
    c.projects.create_project.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "client", "existing"])
async def test_http_uses_real_workflow_actor_tenant_one_audit_and_safe_response(convert_http, mode):
    c = convert_http
    response = await post(c, mode=mode)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result == dict(data=dict(lead_id=str(c.lead.id), tenant_id=str(c.tenant),
        client_id=str(c.client.id), project_id=str(c.project.id), status="won", row_version=5),
        meta={}, request_id="lead-convert-http")
    assert response.headers["X-Request-ID"] == "lead-convert-http"
    assert not any(secret in response.text for secret in (
        "key_hash", "binding", "ciphertext", "convert-http-key", "private@example.com", "Private comment"))
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    assert c.events.create_event.await_args.kwargs["trace_id"] == "lead-convert-http"
    c.events.create_event.assert_awaited_once()
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_replay_is_stable_and_changed_raw_command_conflicts(convert_http):
    c = convert_http
    first = await post(c)
    c.lead.row_version = 9
    replay = await post(c)
    assert first.status_code == replay.status_code == 200
    assert first.json()["data"] == replay.json()["data"]
    changed = body(c)
    changed["project_name"] = "Project name"
    conflict = await post(c, data=changed)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    c.repo.complete_conversion.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", " ", "x"*256])
async def test_key_is_required_before_reservation_or_mutation(convert_http, key):
    c = convert_http
    response = await post(c, key=key)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    assert not c.records.rows
    no_mutation(c)


@pytest.mark.asyncio
async def test_duplicate_headers_cannot_select_an_ambiguous_key(convert_http):
    c = convert_http
    response = await post(c, extra_headers=[("Idempotency-Key", "other")])
    assert response.status_code == 422
    assert not c.records.rows
    no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["anonymous", "inactive", "suite", "module", "permission", "foreign_context",
    "wrong_user", "project_scope", "document_scope", "illegal_tenant"])
async def test_auth_entitlements_and_common_role_scope_deny_before_mutation(convert_http, failure):
    c = convert_http
    if failure == "anonymous":
        def anonymous():
            raise ApiHttpError(status_code=401, code="authentication_required", message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = anonymous
    elif failure == "inactive":
        c.actor.status = "blocked"
    elif failure in ("suite", "module"):
        (c.suite if failure == "suite" else c.module).require_enabled.side_effect = (
            SuiteDisabledError() if failure == "suite" else ModuleDisabledError())
    else:
        tenant = c.foreign if failure == "foreign_context" else c.tenant
        user = uuid4() if failure == "wrong_user" else c.user
        role = "PROJECT_MANAGER" if failure == "illegal_tenant" else "OWNER"
        scope = "project" if failure == "project_scope" else "document" if failure == "document_scope" else "tenant"
        c.context = ConstructionAccessContext(user, tenant, (role,),
            () if failure == "permission" else ("construction.projects.edit",),
            (ConstructionGrantContext(role, scope, c.tenant),))
    response = await post(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    if failure in ("suite", "module"):
        assert response.json()["error"]["code"] == "MODULE_DISABLED"
    assert not c.records.rows
    no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"expected_row_version": None}, {"expected_row_version": True}, {"expected_row_version": "4"},
    {"expected_row_version": 4.0}, {"expected_row_version": 0}, {"expected_row_version": 2**63},
    {"client_type": None}, {"client_type": "other"}, {"client_type": 3},
    {"project_name": None}, {"project_name": " "}, {"project_name": False},
    {"project_address_raw": " "}, {"project_address_raw": []},
    {"client_id": "private-invalid-uuid"}, {"project_id": "private-invalid-uuid"},
    {"tenant_id": str(uuid4())}, {"user_id": str(uuid4())}, {"lead_id": str(uuid4())},
    {"status": "won"}, {"row_version": 5}, {"converted_client_id": str(uuid4())},
    {"converted_project_id": str(uuid4())}, {"created_at": "2026-10-12"}, {"is_replay": True},
])
async def test_invalid_or_server_owned_body_fields_are_rejected(convert_http, changes):
    c = convert_http
    data = body(c)
    data.update(changes)
    response = await post(c, data=data)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert "private-invalid-uuid" not in response.text
    assert not c.records.rows
    no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {}, {"expected_row_version": 4},
    {"expected_row_version": 4, "project_id": str(uuid4())},
    {"expected_row_version": 4, "client_id": str(uuid4()), "project_id": str(uuid4()), "project_name": "Overwrite"},
    {"expected_row_version": 4, "client_id": str(uuid4()), "client_type": "person", "project_name": "New"},
])
async def test_three_scenarios_preserve_normal_client_and_project_requirements(convert_http, data):
    c = convert_http
    assert (await post(c, data=data)).status_code == 422
    assert not c.records.rows
    no_mutation(c)


@pytest.mark.asyncio
async def test_invalid_lead_path_is_validation_error_before_business_mutation(convert_http):
    c = convert_http
    response = await post(c, identifier="private-invalid-lead")
    assert response.status_code == 422 and "private-invalid-lead" not in response.text
    assert not c.records.rows
    no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stale", "lost", "membership", "revoked", "missing", "foreign", "deleted",
    "ownership", "address", "audit", "complete", "commit", "processing", "conditional"])
async def test_http_maps_domain_and_workflow_errors_without_private_details(convert_http, failure):
    c = convert_http
    mode = "existing" if failure == "ownership" else "new"
    if failure == "stale":
        c.lead.row_version = 5
    elif failure == "lost":
        c.lead.status = "lost"
    elif failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "revoked":
        c.access.list_active_grants.return_value[0].status = "revoked"
    elif failure == "missing":
        c.repo.get_active_lead_for_update.return_value = None
    elif failure == "foreign":
        c.lead.tenant_id = c.foreign
    elif failure == "deleted":
        c.lead.deleted_at = c.now
    elif failure == "ownership":
        c.project.client_id = uuid4()
    elif failure == "address":
        c.lead.address_raw = None
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-audit-error")
    elif failure == "complete":
        c.records.fail_complete = True
    elif failure == "commit":
        c.session.commit.side_effect = RuntimeError("private-commit-error")
    elif failure == "processing":
        assert (await post(c)).status_code == 200
        record = next(iter(c.records.rows.values()))
        record.status, record.response_status, record.response_ciphertext = "processing", None, None
    else:
        c.repo.complete_conversion.side_effect = None
        c.repo.complete_conversion.return_value = None
    expected, code = (
        (409, "ROW_VERSION_CONFLICT") if failure == "stale" else
        (409, "conflict") if failure == "lost" else
        (409, "idempotency_in_progress") if failure == "processing" else
        (403, "forbidden") if failure in ("membership", "revoked") else
        (404, "not_found") if failure in ("missing", "foreign", "deleted") else
        (422, "validation_error") if failure in ("ownership", "address") else (500, "internal_error"))
    response = await post(c, mode=mode)
    assert response.status_code == expected, response.text
    assert response.json()["error"]["code"] == code and "private-" not in response.text
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "target_deleted", "ownership"])
async def test_completed_replay_still_checks_current_access_and_targets(convert_http, failure):
    c = convert_http
    assert (await post(c)).status_code == 200
    if failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "revoked":
        c.access.list_active_grants.return_value[0].status = "revoked"
    elif failure == "target_deleted":
        c.client.deleted_at = c.now
    else:
        c.project.client_id = uuid4()
    response = await post(c)
    assert response.status_code == (403 if failure in ("membership", "revoked") else 404 if failure == "target_deleted" else 422)
    c.repo.complete_conversion.assert_awaited_once()
    c.events.create_event.assert_awaited_once()
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extra", "tenant", "lead", "version", "status", "client", "project"])
async def test_route_defends_public_result_binding(convert_http, failure):
    c = convert_http
    data = (await post(c)).json()["data"]
    mode = "existing" if failure in ("client", "project") else "new"
    if failure == "extra":
        data["secret"] = "private-response"
    elif failure in ("tenant", "lead", "client", "project"):
        data[{"tenant": "tenant_id", "lead": "lead_id", "client": "client_id", "project": "project_id"}[failure]] = str(uuid4())
    elif failure == "version":
        data["row_version"] = True
    else:
        data["status"] = "lost"
    fake = SimpleNamespace(convert_lead=AsyncMock(return_value=data))
    c.app.dependency_overrides[routes.get_lead_conversion_workflow] = lambda: fake
    response = await post(c, mode=mode)
    assert response.status_code == 500 and "private-response" not in response.text


def test_factory_reuses_scoped_session_and_existing_encryption_settings(convert_http, monkeypatch):
    c = convert_http
    c.session.execute, c.session.flush, c.session.add = AsyncMock(), AsyncMock(), Mock()
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", lambda: SimpleNamespace(encryption_key=key))
    workflow = routes.get_lead_conversion_workflow(session=c.session, idempotency_key="key")
    assert workflow.session is workflow.service.session is workflow.idempotency.repository.session is c.session
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", lambda: SimpleNamespace(encryption_key=""))
    with pytest.raises(ValueError):
        routes.get_lead_conversion_workflow(session=c.session, idempotency_key="key")


def test_openapi_has_one_required_conversion_route_and_strict_contract(convert_http):
    c = convert_http
    matches = [route for path, route in walk_routes(c.app)
        if path == "/api/v1/construction/crm/leads/{id}/convert" and "POST" in getattr(route, "methods", ())]
    assert len(matches) == 1 and matches[0].endpoint is routes.convert_lead
    assert routes.convert_permission.construction_permission_code == "construction.projects.edit"
    schema = c.app.openapi()
    op = schema["paths"]["/api/v1/construction/crm/leads/{id}/convert"]["post"]
    key = next(item for item in op["parameters"] if item["name"] == "Idempotency-Key")
    assert key["in"] == "header" and key["required"] is True
    assert {"200", "401", "403", "404", "409", "422", "500"} <= set(op["responses"])
    model = schema["components"]["schemas"]["LeadConversionRequest"]
    assert set(model["properties"]) == {"expected_row_version", "client_id", "project_id",
        "client_type", "project_name", "project_address_raw"}
    assert model["required"] == ["expected_row_version"] and model["additionalProperties"] is False
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/LeadConversionEnvelope")


def test_http_has_no_repository_or_transaction_operations():
    assert routes.get_lead_conversion_workflow.__defaults__[0].dependency is routes.get_construction_scoped_session
    assert routes.get_lead_conversion_workflow.__defaults__[1].dependency is routes.require_lead_conversion_key
    tree = ast.parse(Path(routes.__file__).read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        modules = [item.name for item in node.names] if isinstance(node, ast.Import) else (
            [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        assert not any(module.startswith(("database.repositories", "aiogram", "handlers")) for module in modules)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"commit", "rollback", "flush"}
