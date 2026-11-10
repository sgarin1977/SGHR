"""Registered tax write HTTP with real local SQL/ORM receipts; no external services."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from pydantic import ValidationError

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_tax_profile_writes as routes
from api.routes import construction_tax_profiles as reads
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore, ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_tax_profile_workflow import workflow_case, case, snapshot

PATH = "/api/v1/construction/settings/tax-profiles"
CREATE_BODY = {"name": "HTTP tax", "client_price_tax_mode": "TAX_EXCLUSIVE", "material_cost_basis": "NET"}


@pytest.fixture
async def write_http(workflow_case):
    c = workflow_case
    c.app = create_app()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.core = SimpleNamespace(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(wraps=c.rates.ensure_authenticated_user_allowed)

    async def session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return await c.service.resolver.resolve(actor=c.actor)

    async def workflow(scoped=Depends(get_construction_scoped_session), key=Depends(routes.require_tax_profile_key)):
        assert scoped is c.session
        return c.workflow

    async def reader(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_tax_profile_write_workflow] = workflow
    c.app.dependency_overrides[reads.get_tax_profile_read_service] = reader
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, method="POST", *, body=None, profile_id=None, key="create-key", query="", headers=None):
    request_headers = [("X-Request-ID", "tax-write-http")]
    if key is not None:
        request_headers.append(("Idempotency-Key", key))
    if headers is not None:
        request_headers = headers
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="https://testserver",
    ) as client:
        path = PATH + ("/" + str(profile_id) if profile_id is not None else "") + query
        return await client.request(method, path, json=CREATE_BODY if body is None and method == "POST" else body,
                                    headers=request_headers)


async def created(c):
    response = await call(c)
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.mark.asyncio
async def test_create_patch_and_get_use_shared_boundaries_and_one_commit_per_write(write_http):
    c = write_http
    assert c.sync.in_transaction() is False
    response = await call(c, body=CREATE_BODY | {"name": " HTTP tax "})
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["name"] == "HTTP tax" and data["changed"] is True and data["row_version"] == 1
    assert data["tenant_id"] == str(c.tenant) and data["created_by"] == str(c.user)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert c.session.commits == 1
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    response = await call(c, "PATCH", profile_id=data["id"], key="edit",
                          body={"expected_row_version": 1, "material_cost_basis": "GROSS"})
    assert response.status_code == 200, response.text
    changed = response.json()["data"]
    assert changed["row_version"] == 2 and changed["changed"] is True
    assert changed["material_cost_basis"] == "GROSS" and changed["client_price_tax_mode"] == "TAX_EXCLUSIVE"
    assert changed["created_at"] == data["created_at"] and changed["created_by"] == data["created_by"]
    assert c.session.commits == 2
    c.sync.rollback()
    response = await call(c, "GET", profile_id=data["id"], key=None)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {name: value for name, value in changed.items() if name != "changed"}
    assert [len(value) for value in snapshot(c)] == [1, 2, 2]


@pytest.mark.asyncio
async def test_http_replays_historical_results_without_second_writes(write_http):
    c = write_http
    initial = await created(c)
    payload = {"expected_row_version": 1, "material_cost_basis": "GROSS"}
    first = await call(c, "PATCH", profile_id=initial["id"], key="edit", body=payload)
    assert first.status_code == 200, first.text
    later = await call(c, "PATCH", profile_id=initial["id"], key="later",
                       body={"expected_row_version": 2, "name": "Later"})
    assert later.status_code == 200, later.text
    before = snapshot(c)
    replay = await call(c, "PATCH", profile_id=initial["id"], key="edit", body=payload)
    assert replay.status_code == 200 and replay.json()["data"] == first.json()["data"]
    replay_create = await call(c, headers=[("X-Request-ID", "retry-request"), ("Idempotency-Key", "create-key")])
    assert replay_create.status_code == 201, replay_create.text
    assert replay_create.json()["data"] == initial and replay_create.json()["request_id"] == "retry-request"
    assert snapshot(c) == before
    assert before[1][0]["trace_id"] == "tax-write-http"


@pytest.mark.asyncio
async def test_current_noop_has_no_audit_and_stale_noop_is_409(write_http):
    c = write_http
    initial = await created(c)
    noop = await call(c, "PATCH", profile_id=initial["id"], key="noop", body={"expected_row_version": 1})
    assert noop.status_code == 200, noop.text
    assert noop.json()["data"]["changed"] is False
    assert [len(value) for value in snapshot(c)] == [1, 1, 2]
    real = await call(c, "PATCH", profile_id=initial["id"], key="real",
                      body={"expected_row_version": 1, "client_price_tax_mode": "TAX_INCLUSIVE"})
    assert real.status_code == 200, real.text
    before = snapshot(c)
    stale = await call(c, "PATCH", profile_id=initial["id"], key="new-stale", body={"expected_row_version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_changed_payload_under_same_key_is_409(write_http, method):
    c = write_http
    initial = await created(c)
    if method == "PATCH":
        response = await call(c, method, profile_id=initial["id"], key="edit",
                              body={"expected_row_version": 1, "name": "First"})
        assert response.status_code == 200
    before = snapshot(c)
    response = await call(c, method, profile_id=initial["id"] if method == "PATCH" else None,
                          key="edit" if method == "PATCH" else "create-key",
                          body={"expected_row_version": 1, "name": "Different"} if method == "PATCH"
                               else CREATE_BODY | {"name": "Different"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [], [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x"*256)], [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_required_single_nonblank_bounded_key(write_http, headers):
    c = write_http
    before = snapshot(c)
    response = await call(c, headers=headers)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "client_price_tax_mode", "material_cost_basis"])
async def test_create_has_no_implicit_tax_modes_or_name(write_http, field):
    c = write_http
    payload = dict(CREATE_BODY)
    payload.pop(field)
    response = await call(c, body=payload)
    assert response.status_code == 422, response.text
    assert [len(value) for value in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"name": None}, {"name": " "}, {"client_price_tax_mode": None}, {"material_cost_basis": None},
    {"client_price_tax_mode": "AUTO"}, {"material_cost_basis": "net"}, {"expected_row_version": True},
    {"expected_row_version": "1"}, {"expected_row_version": 0}, {"expected_row_version": 2**63},
    {"tenant_id": "foreign"}, {"created_by": "foreign"}, {"manage_transaction": False},
    {"tax_rate": 20}, {"is_default": True},
])
async def test_sparse_patch_rejects_nulls_invalid_modes_versions_and_selectors(write_http, fields):
    c = write_http
    initial = await created(c)
    before = snapshot(c)
    response = await call(c, "PATCH", profile_id=initial["id"], key="bad",
                          body={"expected_row_version": 1} | fields)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "project_scope"])
async def test_receipt_replay_still_requires_current_tax_edit_authority(write_http, failure):
    c = write_http
    await created(c)
    before = snapshot(c)
    if failure == "membership": c.membership = False
    elif failure == "revoked": c.grant.status = "revoked"
    else:
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "PROJECT_MANAGER", "project", uuid4()
    response = await call(c)
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_foreign_missing_and_malformed_profile_ids_are_safe(write_http):
    c = write_http
    initial = await created(c)
    c.sync.execute(c.table.update().where(c.table.c.id == UUID(initial["id"])).values(tenant_id=c.foreign))
    c.sync.commit()
    responses = []
    for profile_id in (initial["id"], str(uuid4())):
        response = await call(c, "PATCH", profile_id=profile_id, key="foreign-" + profile_id,
                              body={"expected_row_version": 1, "name": "Must not save"})
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]
    response = await call(c, "PATCH", profile_id="not-a-uuid", key="invalid",
                          body={"expected_row_version": 1})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_transport_gates_prevent_writes(write_http, gate):
    c = write_http
    status, code = 403, "MODULE_DISABLED"
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(status_code=401, code="authentication_required", message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        status, code = 401, "authentication_required"
    elif gate == "suite": c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core": c.core.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        status, code = 429, "RATE_LIMIT_EXCEEDED"
    response = await call(c)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert [len(value) for value in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "private"])
async def test_http_failures_are_sanitized_and_atomic(write_http, failure):
    c = write_http
    if failure == "audit": c.fail_audit = True
    elif failure == "complete": c.fail_complete = "error"
    elif failure == "commit": c.session.fail_commit = True
    else: c.workflow.create_profile = AsyncMock(side_effect=RuntimeError("PRIVATE_DATABASE_DETAILS"))
    response = await call(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error" and "PRIVATE_DATABASE_DETAILS" not in response.text
    assert [len(value) for value in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
async def test_pending_receipt_and_query_selectors_have_explicit_errors(write_http):
    c = write_http
    await created(c)
    c.sync.execute(c.receipts.update().values(status="processing", response_status=None, response_ciphertext=None))
    c.sync.commit()
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 409 and response.json()["error"]["code"] == "idempotency_in_progress"
    response = await call(c, query="?tenant_id=foreign")
    assert response.status_code == 422
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_missing_replay_configuration_is_503_without_environment_reads(write_http, monkeypatch):
    c = write_http
    c.app.dependency_overrides.pop(routes.get_tax_profile_write_workflow)
    def missing(cls):
        raise routes.ApiConfigurationError("PRIVATE_CONFIGURATION_DETAILS")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(missing))
    response = await call(c)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "PRIVATE_CONFIGURATION_DETAILS" not in response.text
    assert [len(value) for value in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
async def test_openapi_has_explicit_modes_required_keys_and_exact_versions(write_http):
    c = write_http
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    for path, verb in ((PATH, "post"), (PATH + "/{profile_id}", "patch")):
        operation = document["paths"][path][verb]
        keys = [p for p in operation["parameters"] if p["name"] == "Idempotency-Key" and p["in"] == "header"]
        assert len(keys) == 1 and keys[0]["required"] is True
        assert operation["security"] and {401, 403, 404, 409, 422, 429, 500, 503} <= {
            int(value) for value in operation["responses"]}
    schemas = document["components"]["schemas"]
    assert set(schemas["TaxProfileCreateRequest"]["required"]) == set(CREATE_BODY)
    for field in CREATE_BODY:
        assert "default" not in schemas["TaxProfileCreateRequest"]["properties"][field]
        patch = schemas["TaxProfilePatchRequest"]["properties"][field]
        assert patch["type"] == "string" and "anyOf" not in patch and "default" not in patch
    assert schemas["TaxProfilePatchRequest"]["required"] == ["expected_row_version"]
    for schema, field in (("TaxProfilePatchRequest", "expected_row_version"), ("TaxProfileWriteData", "row_version")):
        version = schemas[schema]["properties"][field]
        assert type(version["maximum"]) is int and version["maximum"] == 2**63 - 1
        assert version["minimum"] == 1 and version["format"] == "int64"


def test_runtime_version_bounds_and_factory_session_binding(monkeypatch):
    body = routes.TaxProfilePatchRequest(expected_row_version=2**63 - 1)
    assert body.patch_fields() == {}
    with pytest.raises(ValidationError):
        routes.TaxProfilePatchRequest(expected_row_version=2**63)
    session, sentinel, received = object(), object(), []
    def build(**values):
        received.append(values)
        return sentinel
    monkeypatch.setattr(routes, "build_construction_tax_profile_workflow", build)
    result = routes.get_tax_profile_write_workflow(
        settings=SimpleNamespace(encryption_key="local-test-config"), session=session)
    assert result is sentinel and received == [{"session": session, "idempotency_encryption_key": "local-test-config"}]


@pytest.mark.asyncio
async def test_admin_uses_existing_permission_and_patch_version_is_required(write_http):
    c = write_http
    c.grant.role = "ADMIN"
    initial = await created(c)
    before = snapshot(c)
    response = await call(c, "PATCH", profile_id=initial["id"], key="missing-version", body={"name": "Changed"})
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
