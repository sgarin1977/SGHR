"""Registered tax-rule write HTTP + real local atomic workflow; no external services."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

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
from api.routes import construction_tax_rule_writes as routes
from api.routes import construction_tax_rules as reads
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore, ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_tax_rule_workflow import rule_workflow_case, rule_case, case, snapshot

PATH = "/api/v1/construction/settings/tax-profiles"
CREATE_BODY = {"category": "WORK", "rate_percent": "20.00"}


@pytest.fixture
async def write_http(rule_workflow_case):
    c = rule_workflow_case
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
        return await c.rule_service.resolver.resolve(actor=c.actor)

    async def workflow(scoped=Depends(get_construction_scoped_session), key=Depends(routes.require_tax_rule_key)):
        assert scoped is c.session
        return c.workflow

    async def reader(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.rule_service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_tax_rule_write_workflow] = workflow
    c.app.dependency_overrides[reads.get_tax_rule_read_service] = reader
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, method="POST", *, body=None, profile=None, rule=None, key="rule-create", query="", headers=None):
    request_headers = [("X-Request-ID", "tax-rule-write-http")]
    if key is not None:
        request_headers.append(("Idempotency-Key", key))
    if headers is not None:
        request_headers = headers
    path = PATH + "/" + str(c.profile if profile is None else profile) + "/rules"
    if rule is not None:
        path += "/" + str(rule)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            method, path + query,
            json=CREATE_BODY if method == "POST" and body is None else body,
            headers=request_headers,
        )


async def created(c, **kwargs):
    response = await call(c, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.mark.asyncio
async def test_post_patch_and_get_preserve_exact_rate_context_and_one_commit(write_http):
    c = write_http
    assert c.sync.in_transaction() is False
    response = await call(c)
    assert response.status_code == 201, response.text
    initial = response.json()["data"]
    assert initial["rate_percent"] == "20" and initial["changed"] is True and initial["row_version"] == 1
    assert initial["tenant_id"] == str(c.tenant) and initial["tax_profile_id"] == str(c.profile)
    assert initial["created_by"] == str(c.user) and c.session.commits == 1
    assert response.headers["Cache-Control"] == "private, no-store" and response.headers["Pragma"] == "no-cache"
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    response = await call(c, "PATCH", rule=initial["id"], key="edit",
                          body={"expected_row_version": 1, "rate_percent": "7.125"})
    assert response.status_code == 200, response.text
    changed = response.json()["data"]
    assert changed["rate_percent"] == "7.125" and changed["category"] == "WORK"
    assert changed["changed"] is True and changed["row_version"] == 2
    assert changed["created_at"] == initial["created_at"] and changed["created_by"] == initial["created_by"]
    assert c.session.commits == 2
    response = await call(c, "GET", rule=initial["id"], key=None)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {name: value for name, value in changed.items() if name != "changed"}
    assert [len(value) for value in snapshot(c)] == [1, 2, 2]


@pytest.mark.asyncio
async def test_long_decimal_survives_write_read_and_semantically_equal_retry(write_http):
    c = write_http
    rate = "99.123456789012345678901234567890123456789"
    initial = await created(c, body=CREATE_BODY | {"rate_percent": rate})
    assert initial["rate_percent"] == rate
    before = snapshot(c)
    response = await call(c, body=CREATE_BODY | {"rate_percent": rate + "00"})
    assert response.status_code == 201 and response.json()["data"] == initial
    assert snapshot(c) == before
    response = await call(c, "GET", rule=initial["id"], key=None)
    assert response.status_code == 200 and response.json()["data"]["rate_percent"] == rate


@pytest.mark.asyncio
async def test_original_receipts_replay_after_later_changes(write_http):
    c = write_http
    initial = await created(c)
    payload = {"expected_row_version": 1, "rate_percent": "7.125"}
    first = await call(c, "PATCH", rule=initial["id"], key="edit", body=payload)
    assert first.status_code == 200, first.text
    later = await call(c, "PATCH", rule=initial["id"], key="later",
                       body={"expected_row_version": 2, "category": "OTHER"})
    assert later.status_code == 200, later.text
    before = snapshot(c)
    replay = await call(c, "PATCH", rule=initial["id"], key="edit", body=payload)
    assert replay.status_code == 200 and replay.json()["data"] == first.json()["data"]
    retry = await call(c, headers=[("X-Request-ID", "new-request"), ("Idempotency-Key", "rule-create")])
    assert retry.status_code == 201 and retry.json()["data"] == initial
    assert retry.json()["request_id"] == "new-request" and snapshot(c) == before
    assert all(event["trace_id"] == "tax-rule-write-http" for event in before[1])


@pytest.mark.asyncio
async def test_current_noop_stale_noop_and_historical_noop_replay(write_http):
    c = write_http
    initial = await created(c)
    payload = {"expected_row_version": 1, "rate_percent": "20.000"}
    noop = await call(c, "PATCH", rule=initial["id"], key="noop", body=payload)
    assert noop.status_code == 200 and noop.json()["data"]["changed"] is False
    assert noop.json()["data"]["row_version"] == 1
    assert [len(value) for value in snapshot(c)] == [1, 1, 2]
    changed = await call(c, "PATCH", rule=initial["id"], key="change",
                         body={"expected_row_version": 1, "category": "OTHER"})
    assert changed.status_code == 200, changed.text
    before = snapshot(c)
    replay = await call(c, "PATCH", rule=initial["id"], key="noop", body=payload)
    assert replay.status_code == 200 and replay.json()["data"] == noop.json()["data"]
    stale = await call(c, "PATCH", rule=initial["id"], key="stale", body={"expected_row_version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_changed_payload_with_reused_key_is_409(write_http, method):
    c = write_http
    initial = await created(c)
    if method == "PATCH":
        first = await call(c, method, rule=initial["id"], key="edit",
                           body={"expected_row_version": 1, "rate_percent": "7"})
        assert first.status_code == 200, first.text
    before = snapshot(c)
    response = await call(
        c, method, rule=initial["id"] if method == "PATCH" else None,
        key="edit" if method == "PATCH" else "rule-create",
        body={"expected_row_version": 1, "rate_percent": "8"} if method == "PATCH"
             else CREATE_BODY | {"rate_percent": "21"},
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [], [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)], [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_required_single_nonblank_key_precedes_reservation(write_http, headers):
    response = await call(write_http, headers=headers)
    assert response.status_code == 422, response.text
    assert [len(value) for value in snapshot(write_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["category", "rate_percent"])
async def test_post_requires_both_fields_without_defaults(write_http, field):
    body = dict(CREATE_BODY)
    del body[field]
    response = await call(write_http, body=body)
    assert response.status_code == 422 and [len(value) for value in snapshot(write_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [20, 20.0, True, None, "NaN", "Infinity", "-0.001",
                                   "100.000000000000000000000001", "20,5", "2_0", " 20"])
async def test_rate_is_an_exact_valid_decimal_string(write_http, rate):
    response = await call(write_http, body=CREATE_BODY | {"rate_percent": rate})
    assert response.status_code == 422, response.text
    assert [len(value) for value in snapshot(write_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"category": None}, {"category": "works"}, {"rate_percent": None}, {"rate_percent": 20.0},
    {"expected_row_version": True}, {"expected_row_version": "1"}, {"expected_row_version": 0},
    {"expected_row_version": 2**63}, {"tenant_id": "foreign"}, {"tax_profile_id": "foreign"},
    {"created_by": "foreign"}, {"manage_transaction": False},
])
async def test_patch_rejects_nulls_versions_and_immutable_selectors(write_http, fields):
    c = write_http
    initial = await created(c)
    before = snapshot(c)
    response = await call(c, "PATCH", rule=initial["id"], key="invalid",
                          body={"expected_row_version": 1} | fields)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "project"])
async def test_replay_retains_current_tax_edit_requirement(write_http, failure):
    c = write_http
    await created(c)
    before = snapshot(c)
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
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
    assert [len(value) for value in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
async def test_foreign_missing_and_wrong_profile_have_safe_404(write_http):
    c = write_http
    initial = await created(c)
    before = snapshot(c)
    responses = []
    for index, profile in enumerate((c.foreign_profile, uuid4(), c.other_profile)):
        response = await call(c, "PATCH", profile=profile, rule=initial["id"], key="missing-" + str(index),
                              body={"expected_row_version": 1, "category": "OTHER"})
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert all(body == responses[0] for body in responses)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "private"])
async def test_http_failures_are_sanitized_and_atomic(write_http, failure):
    c = write_http
    if failure == "audit":
        c.fail_audit = True
    elif failure == "complete":
        c.fail_complete = "error"
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.workflow.create_rule = AsyncMock(side_effect=RuntimeError("PRIVATE_RULE_DATABASE_DETAILS"))
    response = await call(c)
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert "PRIVATE_RULE_DATABASE_DETAILS" not in response.text
    assert [len(value) for value in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
async def test_processing_receipt_and_caller_query_are_explicit_errors(write_http):
    c = write_http
    await created(c)
    c.sync.execute(c.receipts.update().values(
        status="processing", response_status=None, response_ciphertext=None, completed_at=None,
    ))
    c.sync.commit()
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 409 and response.json()["error"]["code"] == "idempotency_in_progress"
    response = await call(c, query="?tenant_id=foreign")
    assert response.status_code == 422 and snapshot(c) == before


@pytest.mark.asyncio
async def test_missing_configuration_is_503_without_reading_real_env(write_http, monkeypatch):
    c = write_http
    c.app.dependency_overrides.pop(routes.get_tax_rule_write_workflow)
    def missing(cls):
        raise routes.ApiConfigurationError("PRIVATE_CONFIGURATION_DETAILS")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(missing))
    response = await call(c)
    assert response.status_code == 503 and response.json()["error"]["code"] == "service_unavailable"
    assert "PRIVATE_CONFIGURATION_DETAILS" not in response.text
    assert [len(value) for value in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
async def test_openapi_has_required_keys_decimal_strings_sparse_patch_and_exact_versions(write_http):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=write_http.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    base = PATH + "/{profile_id}/rules"
    for path, verb in ((base, "post"), (base + "/{rule_id}", "patch")):
        operation = document["paths"][path][verb]
        keys = [p for p in operation["parameters"] if p["name"] == "Idempotency-Key" and p["in"] == "header"]
        assert len(keys) == 1 and keys[0]["required"] is True
        assert operation["security"] and {401, 403, 404, 409, 422, 429, 500, 503} <= {
            int(value) for value in operation["responses"]
        }
    schemas = document["components"]["schemas"]
    assert set(schemas["TaxRuleCreateRequest"]["required"]) == {"category", "rate_percent"}
    assert schemas["TaxRuleCreateRequest"]["properties"]["rate_percent"]["type"] == "string"
    assert schemas["TaxRulePatchRequest"]["required"] == ["expected_row_version"]
    for name in ("category", "rate_percent"):
        field = schemas["TaxRulePatchRequest"]["properties"][name]
        assert field["type"] == "string" and "anyOf" not in field and "default" not in field
    for schema, name in (("TaxRulePatchRequest", "expected_row_version"), ("TaxRuleWriteData", "row_version")):
        version = schemas[schema]["properties"][name]
        assert type(version["maximum"]) is int and version["maximum"] == 2**63 - 1
        assert version["minimum"] == 1 and version["format"] == "int64"
    assert schemas["TaxRuleWriteData"]["properties"]["rate_percent"]["type"] == "string"


def test_factory_preserves_session_and_patch_version_bounds(monkeypatch):
    assert routes.TaxRulePatchRequest(expected_row_version=2**63 - 1).domain_fields() == {}
    with pytest.raises(ValidationError):
        routes.TaxRulePatchRequest(expected_row_version=2**63)
    session, sentinel, received = object(), object(), []
    def build(**values):
        received.append(values)
        return sentinel
    monkeypatch.setattr(routes, "build_construction_tax_rule_workflow", build)
    assert routes.get_tax_rule_write_workflow(
        settings=SimpleNamespace(encryption_key="local-test-config"), session=session,
    ) is sentinel
    assert received == [{"session": session, "idempotency_encryption_key": "local-test-config"}]

# con016_canonical_categories_unique_v1
