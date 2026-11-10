"""Registered CON-018 writes with local SQL, audit and encrypted receipts."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import update

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service)
from api.errors import ApiHttpError
from api.routes import construction_project_pricing_adjustment_writes as routes
from api.routes import construction_project_pricing_adjustments as reads
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore, ApiRequestRateLimitExceededError)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_tax_profiles import case
from test_construction_02_project_pricing_adjustments import adjustment_case
from test_construction_02_project_pricing_adjustment_workflow import adjustment_workflow_case, snapshot

PATH = "/api/v1/construction/projects/"
CREATE = {"name": "Urgency", "coefficient": "1.10", "enabled": True}


@pytest.fixture
def write_http(adjustment_workflow_case):
    c = adjustment_workflow_case
    c.sync.rollback()
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
        return await c.adjustment_service.resolver.resolve(actor=c.actor)

    async def workflow(scoped=Depends(get_construction_scoped_session), key=Depends(routes.adjustment_key)):
        assert scoped is c.session
        return c.workflow

    async def reader(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.adjustment_service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_adjustment_write_workflow] = workflow
    c.app.dependency_overrides[reads.get_adjustment_read_service] = reader
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, method="POST", *, body=None, project=None, adjustment=None,
               key="adjustment-http-create", query="", headers=None):
    request_headers = [("X-Request-ID", "adjustment-write-http")]
    if key is not None:
        request_headers.append(("Idempotency-Key", key))
    if headers is not None:
        request_headers = headers
    path = PATH + str(c.project if project is None else project) + "/pricing-adjustments"
    if adjustment is not None:
        path += "/" + str(adjustment)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(method, path + query, headers=request_headers,
                                    json=CREATE if method == "POST" and body is None else body)


async def created(c, **values):
    response = await call(c, **values)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def counts(c):
    rows = snapshot(c)
    return [len(rows[i]) for i in (0, 1, 3)]


@pytest.mark.asyncio
async def test_post_sparse_patch_get_and_mandatory_audit_are_correlated(write_http):
    c = write_http
    response = await call(c)
    assert response.status_code == 201, response.text
    original = response.json()["data"]
    assert original["name"] == "Urgency" and Decimal(original["coefficient"]) == Decimal("1.10")
    assert original["enabled"] is True and original["changed"] is True and original["row_version"] == 1
    assert original["tenant_id"] == str(c.tenant) and original["project_id"] == str(c.project)
    assert original["created_by"] == str(c.user) and c.session.commits == 1
    assert response.headers["Cache-Control"] == "private, no-store" and response.headers["Pragma"] == "no-cache"
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    changed = await call(c, "PATCH", adjustment=original["id"], key="disable",
                         body={"expected_row_version": 1, "enabled": False})
    assert changed.status_code == 200, changed.text
    data = changed.json()["data"]
    assert data["enabled"] is False and data["row_version"] == 2 and data["changed"] is True
    assert all(data[name] == original[name] for name in (
        "id", "tenant_id", "project_id", "name", "coefficient", "created_by", "created_at"))
    assert c.session.commits == 2
    read = await call(c, "GET", adjustment=original["id"], key=None)
    assert read.status_code == 200, read.text
    assert read.json()["data"] == {k: v for k, v in data.items() if k != "changed"}
    assert counts(c) == [1, 2, 2]
    assert all(event["entity_id"] == UUID(original["id"]) and event["trace_id"] == "adjustment-write-http"
               for event in snapshot(c)[1])
    assert [row["row_version"] for row in snapshot(c)[2]] == [1, 1, 1]


@pytest.mark.asyncio
async def test_exact_decimal_and_trimmed_name_retry_return_original_receipt(write_http):
    c = write_http
    value = "1.234567890123456789012345678901234567890123456789"
    first = await created(c, body=CREATE | {"name": " Urgency ", "coefficient": value, "enabled": False})
    before = snapshot(c)
    replay = await call(c, body=CREATE | {"coefficient": value + "00", "enabled": False})
    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == first and first["coefficient"] == value and first["name"] == "Urgency"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_optional_key_current_noop_and_stale_noop_keep_versions(write_http):
    c = write_http
    original = await created(c, key=None)
    for fields in ({}, {"name": " Urgency "}, {"coefficient": "1.1000"}, {"enabled": True}):
        response = await call(c, "PATCH", adjustment=original["id"], key=None,
                              body={"expected_row_version": 1} | fields)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["changed"] is False and response.json()["data"]["row_version"] == 1
    assert counts(c) == [1, 1, 0]
    response = await call(c, "PATCH", adjustment=original["id"], key=None,
                          body={"expected_row_version": 1, "name": "Logistics"})
    assert response.status_code == 200 and response.json()["data"]["row_version"] == 2
    before = snapshot(c)
    response = await call(c, "PATCH", adjustment=original["id"], key=None, body={"expected_row_version": 1})
    assert response.status_code == 409 and response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_create_update_and_noop_receipts_survive_later_edit(write_http):
    c = write_http
    original = await created(c)
    noop_body = {"expected_row_version": 1, "coefficient": "1.100"}
    noop = await call(c, "PATCH", adjustment=original["id"], key="noop", body=noop_body)
    assert noop.status_code == 200 and noop.json()["data"]["changed"] is False
    edit_body = {"expected_row_version": 1, "coefficient": "1.25"}
    edited = await call(c, "PATCH", adjustment=original["id"], key="edit", body=edit_body)
    assert edited.status_code == 200, edited.text
    later = await call(c, "PATCH", adjustment=original["id"], key="later",
                       body={"expected_row_version": 2, "enabled": False})
    assert later.status_code == 200, later.text
    before = snapshot(c)
    for key, body, expected in (("noop", noop_body, noop.json()["data"]), ("edit", edit_body, edited.json()["data"])):
        replay = await call(c, "PATCH", adjustment=original["id"], key=key, body=body)
        assert replay.status_code == 200 and replay.json()["data"] == expected
    replay = await call(c, headers=[("X-Request-ID", "new-request"), ("Idempotency-Key", "adjustment-http-create")])
    assert replay.status_code == 201 and replay.json()["data"] == original
    assert replay.json()["request_id"] == "new-request" and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["name", "coefficient", "enabled", "project", "version", "adjustment"])
async def test_active_key_binds_normalized_payload_and_exact_resource(write_http, change):
    c = write_http
    original = await created(c)
    fields = {"expected_row_version": 1, "coefficient": "1.25"}
    initial = await call(c, "PATCH", adjustment=original["id"], key="edit", body=fields)
    assert initial.status_code == 200, initial.text
    before = snapshot(c)
    if change in {"name", "enabled"}:
        response = await call(c, body=CREATE | {change: "Changed" if change == "name" else False})
    else:
        response = await call(c, "PATCH", key="edit",
            project=c.other_project if change == "project" else c.project,
            adjustment=str(uuid4()) if change == "adjustment" else original["id"],
            body=fields | ({"expected_row_version": 2} if change == "version"
                           else {"coefficient": "1.5"} if change == "coefficient" else {}))
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "coefficient", "enabled"])
async def test_create_requires_explicit_values_and_never_assigns_defaults(write_http, field):
    body = dict(CREATE)
    del body[field]
    response = await call(write_http, body=body)
    assert response.status_code == 422, response.text
    assert counts(write_http) == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"name": ""}, {"name": " "}, {"name": "x" * 256}, {"name": "bad\u0000name"}, {"name": 123},
    {"coefficient": 1}, {"coefficient": 1.1}, {"coefficient": True}, {"coefficient": "0"},
    {"coefficient": "-1"}, {"coefficient": "NaN"}, {"coefficient": "Infinity"},
    {"coefficient": "1,25"}, {"coefficient": "1_0"}, {"coefficient": " 1.2"},
    {"coefficient": None}, {"enabled": "false"}, {"enabled": 0}, {"enabled": None},
    {"tenant_id": "foreign"}, {"project_id": "foreign"}, {"created_by": "foreign"},
    {"geographic_coefficient": "1.2"},
])
async def test_invalid_create_values_or_caller_context_cannot_write(write_http, fields):
    response = await call(write_http, body=CREATE | fields)
    assert response.status_code == 422, response.text
    assert counts(write_http) == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {}, {"expected_row_version": 0}, {"expected_row_version": True},
    {"expected_row_version": 1.0}, {"expected_row_version": "1"}, {"expected_row_version": 2**63},
    {"expected_row_version": 1, "name": None}, {"expected_row_version": 1, "coefficient": None},
    {"expected_row_version": 1, "enabled": None}, {"expected_row_version": 1, "enabled": 1},
    {"expected_row_version": 1, "project_id": "foreign"},
])
async def test_patch_requires_strict_version_and_rejects_null_or_extra_fields(write_http, fields):
    c = write_http
    original = await created(c)
    before = snapshot(c)
    response = await call(c, "PATCH", adjustment=original["id"], key="invalid", body=fields)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)], [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_present_key_is_single_nonblank_and_bounded(write_http, headers):
    response = await call(write_http, headers=headers)
    assert response.status_code == 422, response.text
    assert counts(write_http) == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["PROJECT_MANAGER", "ESTIMATOR", "OBSERVER", "EXECUTOR"])
async def test_project_read_roles_do_not_gain_pricing_edit(write_http, role):
    c = write_http
    c.grant.role, c.grant.scope_type, c.grant.scope_id = role, "project", c.project
    response = await call(c)
    assert response.status_code == 403, response.text
    assert counts(c) == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired"])
async def test_replay_requires_fresh_current_authority(write_http, failure):
    c = write_http
    await created(c)
    before = snapshot(c)
    if failure == "membership": c.membership = False
    elif failure == "revoked": c.grant.status = "revoked"
    else: c.grant.expires_at = c.now
    response = await call(c)
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_gates_prevent_business_writes(write_http, gate):
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
    assert response.status_code == status and response.json()["error"]["code"] == code, response.text
    c.serialization.lock_member.assert_not_awaited()
    assert counts(c) == [0, 0, 0]


@pytest.mark.asyncio
async def test_missing_foreign_wrong_project_and_archived_resources_are_hidden(write_http):
    c = write_http
    original = await created(c)
    before = snapshot(c)
    responses = [
        await call(c, project=c.foreign_project, key="foreign"),
        await call(c, project=uuid4(), key="missing"),
        await call(c, "PATCH", project=c.other_project, adjustment=original["id"], key="wrong-project",
                   body={"expected_row_version": 1, "enabled": False}),
        await call(c, "PATCH", adjustment=uuid4(), key="missing-child",
                   body={"expected_row_version": 1, "enabled": False}),
    ]
    assert all(r.status_code == 404 for r in responses), [r.text for r in responses]
    assert all(r.json() == responses[0].json() for r in responses)
    assert snapshot(c) == before
    c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(deleted_at=c.now))
    c.sync.commit()
    before = snapshot(c)
    response = await call(c, key="archived")
    assert response.status_code == 404 and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "cancelled"])
async def test_terminal_project_denies_new_change_and_preserves_original_receipt(write_http, status):
    c = write_http
    original = await created(c)
    c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(status=status))
    c.sync.commit()
    before = snapshot(c)
    for response in (
        await call(c, key="new-create"),
        await call(c, "PATCH", adjustment=original["id"], key="new-update",
                   body={"expected_row_version": 1, "enabled": False}),
    ):
        assert response.status_code == 409 and response.json()["error"]["code"] == "conflict", response.text
    replay = await call(c)
    assert replay.status_code == 201 and replay.json()["data"] == original
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "authority"])
async def test_domain_audit_and_receipt_roll_back_together(write_http, method, failure):
    c = write_http
    original = await created(c) if method == "PATCH" else None
    before = snapshot(c)
    if failure == "audit": c.fail_audit = True
    elif failure == "commit": c.session.fail_commit = True
    else: c.fail_complete = "membership" if failure == "authority" else "error"
    response = await call(c, method, adjustment=None if original is None else original["id"], key="atomic",
        body=None if original is None else {"expected_row_version": 1, "coefficient": "1.25"})
    assert response.status_code == (403 if failure == "authority" else 500), response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_processing_private_error_and_query_selectors_are_safe(write_http):
    c = write_http
    real = c.workflow.create_adjustment
    c.workflow.create_adjustment = AsyncMock(side_effect=RuntimeError("PRIVATE_ADJUSTMENT_DATABASE_DETAILS"))
    response = await call(c)
    assert response.status_code == 500 and "PRIVATE_ADJUSTMENT_DATABASE_DETAILS" not in response.text
    assert counts(c) == [0, 0, 0]
    c.workflow.create_adjustment = real
    await created(c)
    c.sync.execute(c.receipts.update().values(
        status="processing", response_status=None, response_ciphertext=None, completed_at=None))
    c.sync.commit()
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 409 and response.json()["error"]["code"] == "idempotency_in_progress"
    response = await call(c, query="?tenant_id=foreign")
    assert response.status_code == 422 and snapshot(c) == before


@pytest.mark.asyncio
async def test_real_factory_reads_replay_config_only_for_a_present_key(write_http, monkeypatch):
    c = write_http
    c.app.dependency_overrides.pop(routes.get_adjustment_write_workflow)
    def missing(cls):
        raise routes.ApiConfigurationError("PRIVATE_CONFIGURATION_DETAILS")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(missing))
    def build(*, session, idempotency_encryption_key):
        assert session is c.session and idempotency_encryption_key is None
        return c.workflow
    monkeypatch.setattr(routes, "build_construction_project_pricing_adjustment_workflow", build)
    original = await created(c, key=None)
    before = snapshot(c)
    response = await call(c, "PATCH", adjustment=original["id"], key="requires-config",
                          body={"expected_row_version": 1})
    assert response.status_code == 503 and response.json()["error"]["code"] == "service_unavailable"
    assert "PRIVATE_CONFIGURATION_DETAILS" not in response.text and snapshot(c) == before


@pytest.mark.asyncio
async def test_openapi_has_explicit_values_sparse_patch_and_exact_bigint(write_http):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=write_http.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    doc = response.json()
    path = PATH + "{project_id}/pricing-adjustments"
    for suffix, method in (("", "post"), ("/{adjustment_id}", "patch")):
        operation = doc["paths"][path + suffix][method]
        key = [p for p in operation["parameters"] if p["name"] == "Idempotency-Key" and p["in"] == "header"]
        assert len(key) == 1 and key[0]["required"] is False
        assert operation["security"] and {401, 403, 404, 409, 422, 429, 500, 503} <= {
            int(code) for code in operation["responses"]}
    schemas = doc["components"]["schemas"]
    assert set(schemas["ProjectPricingAdjustmentCreateRequest"]["required"]) == set(CREATE)
    patch = schemas["ProjectPricingAdjustmentPatchRequest"]
    assert patch["required"] == ["expected_row_version"]
    for name, kind in (("name", "string"), ("coefficient", "string"), ("enabled", "boolean")):
        assert schemas["ProjectPricingAdjustmentCreateRequest"]["properties"][name]["type"] == kind
        field = patch["properties"][name]
        assert field["type"] == kind and "anyOf" not in field and "default" not in field
    for model, name in (("ProjectPricingAdjustmentWriteData", "row_version"),
                        ("ProjectPricingAdjustmentPatchRequest", "expected_row_version")):
        field = schemas[model]["properties"][name]
        assert type(field["maximum"]) is int and field["maximum"] == 2**63 - 1
        assert field["minimum"] == 1 and field["format"] == "int64"


def test_unkeyed_factory_and_maximum_version_need_no_environment(monkeypatch):
    assert routes.ProjectPricingAdjustmentPatchRequest(expected_row_version=2**63 - 1).domain_fields() == {}
    with pytest.raises(PydanticValidationError):
        routes.ProjectPricingAdjustmentPatchRequest(expected_row_version=2**63)
    def forbidden(cls):
        raise AssertionError("Unkeyed operation must not read encryption configuration.")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(forbidden))
    assert routes.get_adjustment_write_settings(key=None) is None
    session, sentinel, seen = object(), object(), []
    def build(**values):
        seen.append(values)
        return sentinel
    monkeypatch.setattr(routes, "build_construction_project_pricing_adjustment_workflow", build)
    assert routes.get_adjustment_write_workflow(settings=None, session=session) is sentinel
    assert seen == [{"session": session, "idempotency_encryption_key": None}]
