"""Registered pricing writes with real local workflow, audit and encrypted receipts."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from pydantic import ValidationError

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.routes import construction_pricing_profile_writes as routes
from api.routes import construction_pricing_profiles as reads
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore, ApiRequestRateLimitExceededError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_pricing_profile_workflow import workflow_case, pricing, case, snapshot

PATH = "/api/v1/construction/settings/pricing-profiles"
CREATE_BODY = {"code": "STANDARD", "work_coefficient": "1.00", "material_markup_percent": "10.00"}


@pytest.fixture
async def write_http(workflow_case):
    c = workflow_case
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
        return await c.price_service.resolver.resolve(actor=c.actor)

    async def workflow(scoped=Depends(get_construction_scoped_session), key=Depends(routes.pricing_profile_key)):
        assert scoped is c.session
        return c.workflow

    async def reader(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.price_service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_pricing_profile_write_workflow] = workflow
    c.app.dependency_overrides[reads.get_pricing_profile_read_service] = reader
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, method="POST", *, body=None, profile=None, key="pricing-create", query="", headers=None):
    request_headers = [("X-Request-ID", "pricing-write-http")]
    if key is not None:
        request_headers.append(("Idempotency-Key", key))
    if headers is not None:
        request_headers = headers
    path = PATH + ("/" + str(profile) if profile is not None else "")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="https://testserver",
    ) as client:
        return await client.request(method, path + query, headers=request_headers,
                                    json=CREATE_BODY if method == "POST" and body is None else body)


async def created(c, **values):
    response = await call(c, **values)
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.mark.asyncio
async def test_post_patch_get_and_audit_receipt_are_correlated(write_http):
    c = write_http
    assert not c.sync.in_transaction()
    response = await call(c)
    assert response.status_code == 201, response.text
    original = response.json()["data"]
    assert original["code"] == "STANDARD" and original["work_coefficient"] == "1"
    assert original["material_markup_percent"] == "1E+1" and original["row_version"] == 1 and original["changed"] is True
    assert original["tenant_id"] == str(c.tenant) and original["created_by"] == str(c.user)
    assert c.session.commits == 1
    assert response.headers["Cache-Control"] == "private, no-store" and response.headers["Pragma"] == "no-cache"
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    changed = await call(c, "PATCH", profile=original["id"], key="edit",
                         body={"expected_row_version": 1, "work_coefficient": "1.125"})
    assert changed.status_code == 200, changed.text
    data = changed.json()["data"]
    assert data["work_coefficient"] == "1.125" and data["material_markup_percent"] == original["material_markup_percent"]
    assert data["code"] == "STANDARD" and data["changed"] is True and data["row_version"] == 2
    assert data["created_by"] == original["created_by"] and data["created_at"] == original["created_at"]
    assert c.session.commits == 2
    read = await call(c, "GET", profile=original["id"], key=None)
    assert read.status_code == 200, read.text
    assert read.json()["data"] == {name: value for name, value in data.items() if name != "changed"}
    assert [len(rows) for rows in snapshot(c)] == [1, 2, 2]
    assert all(event["entity_id"] == UUID(original["id"])
               and event["trace_id"] == "pricing-write-http" for event in snapshot(c)[1])


@pytest.mark.asyncio
async def test_long_decimals_and_normalized_retry_preserve_exact_result(write_http):
    c = write_http
    value = "1.234567890123456789012345678901234567890123456789"
    first = await created(c, body=CREATE_BODY | {"work_coefficient": value})
    before = snapshot(c)
    retry = await call(c, body=CREATE_BODY | {"work_coefficient": value + "00"})
    assert retry.status_code == 201, retry.text
    assert retry.json()["data"] == first and first["work_coefficient"] == value
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_optional_key_and_unkeyed_versioned_updates_need_no_receipt(write_http):
    c = write_http
    original = await created(c, key=None)
    noop = await call(c, "PATCH", profile=original["id"], key=None, body={"expected_row_version": 1})
    assert noop.status_code == 200 and noop.json()["data"]["changed"] is False
    assert [len(rows) for rows in snapshot(c)] == [1, 1, 0]
    changed = await call(c, "PATCH", profile=original["id"], key=None,
                         body={"expected_row_version": 1, "material_markup_percent": "11"})
    assert changed.status_code == 200 and changed.json()["data"]["row_version"] == 2
    before = snapshot(c)
    stale = await call(c, "PATCH", profile=original["id"], key=None, body={"expected_row_version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    duplicate = await call(c, key=None)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "PRICING_PROFILE_EXISTS"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_original_receipts_survive_later_edits_without_second_audit(write_http):
    c = write_http
    original = await created(c)
    fields = {"expected_row_version": 1, "work_coefficient": "1.125"}
    edited = await call(c, "PATCH", profile=original["id"], key="edit", body=fields)
    assert edited.status_code == 200, edited.text
    later = await call(c, "PATCH", profile=original["id"], key="later",
                       body={"expected_row_version": 2, "material_markup_percent": "12"})
    assert later.status_code == 200, later.text
    before = snapshot(c)
    replay = await call(c, "PATCH", profile=original["id"], key="edit", body=fields)
    assert replay.status_code == 200 and replay.json()["data"] == edited.json()["data"]
    replay = await call(c, headers=[("X-Request-ID", "new-request"), ("Idempotency-Key", "pricing-create")])
    assert replay.status_code == 201 and replay.json()["data"] == original
    assert replay.json()["request_id"] == "new-request" and snapshot(c) == before


@pytest.mark.asyncio
async def test_current_noop_and_keyed_historical_noop_replay(write_http):
    c = write_http
    original = await created(c)
    body = {"expected_row_version": 1, "work_coefficient": "1.000"}
    noop = await call(c, "PATCH", profile=original["id"], key="noop", body=body)
    assert noop.status_code == 200 and noop.json()["data"]["changed"] is False
    edit = await call(c, "PATCH", profile=original["id"], key="change",
                      body={"expected_row_version": 1, "material_markup_percent": "11"})
    assert edit.status_code == 200, edit.text
    before = snapshot(c)
    replay = await call(c, "PATCH", profile=original["id"], key="noop", body=body)
    assert replay.status_code == 200 and replay.json()["data"] == noop.json()["data"]
    assert snapshot(c) == before and len(before[1]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_changed_request_with_reused_key_conflicts(write_http, method):
    c = write_http
    original = await created(c)
    if method == "PATCH":
        initial = await call(c, method, profile=original["id"], key="edit",
                             body={"expected_row_version": 1, "work_coefficient": "2"})
        assert initial.status_code == 200, initial.text
    before = snapshot(c)
    response = await call(c, method, profile=original["id"] if method == "PATCH" else None,
                          key="edit" if method == "PATCH" else "pricing-create",
                          body={"expected_row_version": 1, "work_coefficient": "3"} if method == "PATCH"
                               else CREATE_BODY | {"code": "PREMIUM"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)], [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_present_key_must_be_single_nonblank_and_bounded(write_http, headers):
    response = await call(write_http, headers=headers)
    assert response.status_code == 422, response.text
    assert [len(rows) for rows in snapshot(write_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["code", "work_coefficient", "material_markup_percent"])
async def test_post_requires_explicit_level_and_both_values(write_http, field):
    body = dict(CREATE_BODY)
    del body[field]
    response = await call(write_http, body=body)
    assert response.status_code == 422 and [len(rows) for rows in snapshot(write_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value", [
    ("code", "standard"), ("code", "CUSTOM"),
    ("work_coefficient", 1), ("work_coefficient", 1.0), ("work_coefficient", True),
    ("work_coefficient", None), ("work_coefficient", "0"), ("work_coefficient", "-1"),
    ("work_coefficient", "NaN"), ("work_coefficient", "Infinity"), ("work_coefficient", " 1"),
    ("work_coefficient", "1,25"), ("work_coefficient", "1_0"),
    ("material_markup_percent", 10), ("material_markup_percent", 10.0), ("material_markup_percent", None),
    ("material_markup_percent", "-0.001"), ("material_markup_percent", "100.000000000000000000000001"),
    ("material_markup_percent", "Infinity"),
])
async def test_exact_values_are_validated_before_business_writes(write_http, name, value):
    response = await call(write_http, body=CREATE_BODY | {name: value})
    assert response.status_code == 422, response.text
    assert [len(rows) for rows in snapshot(write_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"work_coefficient": None}, {"material_markup_percent": None}, {"work_coefficient": 1.0},
    {"expected_row_version": True}, {"expected_row_version": "1"}, {"expected_row_version": 0},
    {"expected_row_version": 2**63}, {"code": "PREMIUM"}, {"tenant_id": "foreign"},
    {"created_by": "foreign"}, {"manage_transaction": False},
])
async def test_sparse_patch_rejects_nulls_bad_versions_and_immutable_fields(write_http, fields):
    c = write_http
    original = await created(c)
    before = snapshot(c)
    response = await call(c, "PATCH", profile=original["id"], key="invalid",
                          body={"expected_row_version": 1} | fields)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "project"])
async def test_keyed_replay_still_requires_fresh_pricing_edit_authority(write_http, failure):
    c = write_http
    await created(c)
    before = snapshot(c)
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    elif failure == "expired":
        c.grant.expires_at = c.now
    else:
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "PROJECT_MANAGER", "project", uuid4()
    response = await call(c)
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


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
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.core.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        status, code = 429, "RATE_LIMIT_EXCEEDED"
    response = await call(c)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert [len(rows) for rows in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
async def test_foreign_and_missing_profiles_have_identical_safe_404(write_http):
    c = write_http
    original = await created(c)
    c.sync.execute(c.prices.update().values(tenant_id=c.foreign))
    c.sync.commit()
    before = snapshot(c)
    responses = []
    for index, profile in enumerate((original["id"], str(uuid4()))):
        response = await call(c, "PATCH", profile=profile, key="missing-" + str(index),
                              body={"expected_row_version": 1, "work_coefficient": "2"})
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert responses[0] == responses[1] and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "authority"])
async def test_mutation_audit_and_receipt_failures_are_atomic(write_http, method, failure):
    c = write_http
    original = await created(c) if method == "PATCH" else None
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.fail_complete = "membership" if failure == "authority" else "error"
    response = await call(c, method, profile=original["id"] if original else None,
                          key="change" if original else "pricing-create",
                          body={"expected_row_version": 1, "work_coefficient": "2"} if original else None)
    assert response.status_code == (403 if failure == "authority" else 500), response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_private_exception_processing_and_caller_context_are_safe(write_http):
    c = write_http
    real = c.workflow.create_profile
    c.workflow.create_profile = AsyncMock(side_effect=RuntimeError("PRIVATE_PRICING_DATABASE_DETAILS"))
    response = await call(c)
    assert response.status_code == 500 and "PRIVATE_PRICING_DATABASE_DETAILS" not in response.text
    assert [len(rows) for rows in snapshot(c)] == [0, 0, 0]
    c.workflow.create_profile = real
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
async def test_real_dependency_needs_config_only_when_key_is_present(write_http, monkeypatch):
    c = write_http
    c.app.dependency_overrides.pop(routes.get_pricing_profile_write_workflow)
    def missing(cls):
        raise routes.ApiConfigurationError("PRIVATE_CONFIGURATION_DETAILS")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(missing))
    def build(*, session, idempotency_encryption_key):
        assert session is c.session and idempotency_encryption_key is None
        return c.workflow
    monkeypatch.setattr(routes, "build_construction_pricing_profile_workflow", build)
    original = await created(c, key=None)
    before = snapshot(c)
    response = await call(c, "PATCH", profile=original["id"], key="configured-key",
                          body={"expected_row_version": 1})
    assert response.status_code == 503 and response.json()["error"]["code"] == "service_unavailable"
    assert "PRIVATE_CONFIGURATION_DETAILS" not in response.text and snapshot(c) == before


@pytest.mark.asyncio
async def test_openapi_has_optional_key_exact_strings_sparse_patch_and_bigint(write_http):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=write_http.app, raise_app_exceptions=False), base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    for path, verb in ((PATH, "post"), (PATH + "/{profile_id}", "patch")):
        operation = document["paths"][path][verb]
        keys = [parameter for parameter in operation["parameters"]
                if parameter["name"] == "Idempotency-Key" and parameter["in"] == "header"]
        assert len(keys) == 1 and keys[0]["required"] is False
        assert operation["security"] and {401, 403, 404, 409, 422, 429, 500, 503} <= {
            int(code) for code in operation["responses"]}
    schemas = document["components"]["schemas"]
    assert set(schemas["PricingProfileCreateRequest"]["required"]) == set(CREATE_BODY)
    assert schemas["PricingProfilePatchRequest"]["required"] == ["expected_row_version"]
    for name in ("work_coefficient", "material_markup_percent"):
        assert schemas["PricingProfileCreateRequest"]["properties"][name]["type"] == "string"
        field = schemas["PricingProfilePatchRequest"]["properties"][name]
        assert field["type"] == "string" and "anyOf" not in field and "default" not in field
    for model, name in (("PricingProfileWriteData", "row_version"),
                        ("PricingProfilePatchRequest", "expected_row_version")):
        version = schemas[model]["properties"][name]
        assert type(version["maximum"]) is int and version["maximum"] == 2**63 - 1
        assert version["minimum"] == 1 and version["format"] == "int64"


def test_factory_and_version_contract_do_not_read_environment(monkeypatch):
    assert routes.PricingProfilePatchRequest(expected_row_version=2**63 - 1).domain_fields() == {}
    with pytest.raises(ValidationError):
        routes.PricingProfilePatchRequest(expected_row_version=2**63)
    def forbidden(cls):
        raise AssertionError("Unkeyed calls must not read encryption settings.")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(forbidden))
    assert routes.get_pricing_profile_write_settings(key=None) is None
    session, sentinel, received = object(), object(), []
    def build(**values):
        received.append(values)
        return sentinel
    monkeypatch.setattr(routes, "build_construction_pricing_profile_workflow", build)
    assert routes.get_pricing_profile_write_workflow(settings=None, session=session) is sentinel
    assert received == [{"session": session, "idempotency_encryption_key": None}]
