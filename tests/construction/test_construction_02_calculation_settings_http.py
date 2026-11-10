"""Registered CON-019 HTTP with real local workflow and encrypted receipts."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_calculation_settings as routes
from api.routes.construction import (
    get_construction_access_context, get_construction_scoped_session,
)
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
    ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_calculation_settings_workflow import (
    workflow_case, calculation, case, snapshot,
)

PATH = "/api/v1/construction/settings/calculation"
BODY = {"money_input_precision": 4, "supplier_stale_price_days": 0}


@pytest.fixture
async def calculation_http(workflow_case):
    c = workflow_case
    c.sync.rollback()
    c.app = create_app()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.core = SimpleNamespace(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(
        wraps=c.rates.ensure_authenticated_user_allowed)

    async def scoped_session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(session=Depends(get_construction_scoped_session)):
        assert session is c.session
        return await c.calc.resolver.resolve(actor=c.actor)

    async def workflow(
        session=Depends(get_construction_scoped_session),
        key=Depends(routes.calculation_settings_key),
    ):
        assert session is c.session
        return c.workflow

    async def reader(session=Depends(get_construction_scoped_session)):
        assert session is c.session
        return c.calc

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = scoped_session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_calculation_settings_service] = reader
    c.app.dependency_overrides[routes.get_calculation_settings_workflow] = workflow
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, method="POST", *, body=None, key="calculation-create", query="", headers=None):
    request_headers = [("X-Request-ID", "calculation-http")]
    if key is not None:
        request_headers.append(("Idempotency-Key", key))
    if headers is not None:
        request_headers = headers
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            method, PATH + query, headers=request_headers,
            json=BODY if body is None and method == "POST" else body)


@pytest.mark.asyncio
async def test_absent_read_has_no_write_and_uses_shared_gates(calculation_http):
    c = calculation_http
    response = await call(c, "GET", key=None)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "data": None, "meta": {}, "request_id": "calculation-http"}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert [len(rows) for rows in snapshot(c)] == [0, 0, 0]
    assert c.session.commits == 0
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, module_code="construction.core")


@pytest.mark.asyncio
async def test_create_sparse_patch_read_and_atomic_receipts(calculation_http):
    c = calculation_http
    original = await call(c)
    assert original.status_code == 201, original.text
    data = original.json()["data"]
    assert data["quantity_precision"] == 4 and data["row_version"] == 1
    assert data["minimum_job_amount"] is None and data["changed"] is True
    assert data["tenant_id"] == str(c.tenant)
    changed = await call(c, "PATCH", key="edit", body={
        "expected_row_version": 1, "minimum_job_amount": "123.456789",
        "minimum_job_currency": "EUR",
    })
    assert changed.status_code == 200, changed.text
    edited = changed.json()["data"]
    assert edited["minimum_job_amount"] == "123.456789"
    assert edited["row_version"] == 2 and edited["quantity_precision"] == 4
    read = await call(c, "GET", key=None)
    assert read.status_code == 200, read.text
    assert read.json()["data"] == {
        key: value for key, value in edited.items() if key != "changed"}
    assert [len(rows) for rows in snapshot(c)] == [1, 2, 2]
    assert c.session.commits == 2
    assert all(row["entity_id"] == c.tenant
               and row["trace_id"] == "calculation-http" for row in snapshot(c)[1])


@pytest.mark.asyncio
async def test_historical_replay_and_changed_key_conflict(calculation_http):
    c = calculation_http
    original = await call(c)
    assert original.status_code == 201, original.text
    body = {"expected_row_version": 1, "quantity_precision": 5}
    first = await call(c, "PATCH", key="edit", body=body)
    assert first.status_code == 200, first.text
    later = await call(c, "PATCH", key="later", body={
        "expected_row_version": 2, "money_input_precision": 6})
    assert later.status_code == 200, later.text
    before = snapshot(c)
    assert (await call(c)).json()["data"] == original.json()["data"]
    assert (await call(c, "PATCH", key="edit", body=body)).json()["data"] == first.json()["data"]
    conflict = await call(c, "PATCH", key="edit", body=body | {"quantity_precision": 6})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_no_key_noop_stale_conflict_and_duplicate(calculation_http):
    c = calculation_http
    assert routes.get_calculation_write_settings(None) is None
    original = await call(c, key=None)
    assert original.status_code == 201, original.text
    noop = await call(c, "PATCH", key=None, body={"expected_row_version": 1})
    assert noop.status_code == 200 and noop.json()["data"]["changed"] is False
    changed = await call(c, "PATCH", key=None, body={
        "expected_row_version": 1, "quantity_precision": 0})
    assert changed.status_code == 200, changed.text
    before = snapshot(c)
    stale = await call(c, "PATCH", key=None, body={"expected_row_version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    duplicate = await call(c, key=None)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CALCULATION_SETTINGS_EXISTS"
    assert snapshot(c) == before and len(before[2]) == 0


@pytest.mark.asyncio
async def test_minimum_pair_and_explicit_nullable_clear(calculation_http):
    c = calculation_http
    created = await call(c, body=BODY | {
        "minimum_job_amount": "0", "minimum_job_currency": "EUR",
        "default_local_repair_percent": "10.25",
    })
    assert created.status_code == 201, created.text
    before = snapshot(c)
    incomplete = await call(c, "PATCH", key="bad", body={
        "expected_row_version": 1, "minimum_job_currency": None})
    assert incomplete.status_code == 422, incomplete.text
    assert snapshot(c) == before
    cleared = await call(c, "PATCH", key="clear", body={
        "expected_row_version": 1, "minimum_job_amount": None,
        "minimum_job_currency": None, "default_local_repair_percent": None,
    })
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["minimum_job_amount"] is None
    assert cleared.json()["data"]["default_local_repair_percent"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"money_input_precision": True}, {"money_input_precision": "4"},
    {"money_input_precision": 7}, {"quantity_precision": -1},
    {"quantity_precision": None}, {"supplier_stale_price_days": None},
    {"supplier_stale_price_days": 3651}, {"supplier_stale_price_days": 1.5},
    {"minimum_job_amount": 1.25}, {"minimum_job_amount": "NaN"},
    {"minimum_job_amount": "Infinity"}, {"minimum_job_amount": "1_000"},
    {"default_local_repair_percent": "-0.1"},
    {"default_technological_waste_percent": "100.01"},
    {"minimum_job_currency": "eur"}, {"minimum_job_currency": "ZZZ"},
    {"tenant_id": "foreign"}, {"created_by": "foreign"},
    {"manage_transaction": False},
])
async def test_invalid_values_fail_without_business_writes(calculation_http, fields):
    c = calculation_http
    response = await call(c, body=BODY | fields)
    assert response.status_code == 422, response.text
    assert [len(rows) for rows in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"money_input_precision": 4}, {"supplier_stale_price_days": 0},
])
async def test_creation_requires_explicit_money_precision_and_lifetime(calculation_http, body):
    response = await call(calculation_http, body=body)
    assert response.status_code == 422, response.text
    assert [len(rows) for rows in snapshot(calculation_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, True, "1", 0, 2**63])
async def test_patch_version_is_strict(calculation_http, version):
    response = await call(
        calculation_http, "PATCH", body={"expected_row_version": version})
    assert response.status_code == 422, response.text
    assert [len(rows) for rows in snapshot(calculation_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)],
    [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_key_is_single_bounded_and_nonblank(calculation_http, headers):
    response = await call(calculation_http, headers=headers)
    assert response.status_code == 422, response.text
    assert [len(rows) for rows in snapshot(calculation_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
async def test_caller_query_selectors_are_rejected(calculation_http, method):
    response = await call(
        calculation_http, method, query="?tenant_id=foreign",
        body={"expected_row_version": 1} if method == "PATCH" else None)
    assert response.status_code == 422, response.text
    assert [len(rows) for rows in snapshot(calculation_http)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_gates_block_operations(calculation_http, method, gate):
    c = calculation_http
    status, code = 403, "MODULE_DISABLED"
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(
                status_code=401, code="authentication_required",
                message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        status, code = 401, "authentication_required"
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.core.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        status, code = 429, "RATE_LIMIT_EXCEEDED"
    response = await call(
        c, method, body={"expected_row_version": 1} if method == "PATCH" else None)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert [len(rows) for rows in snapshot(c)] == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "authority"])
async def test_http_failure_rolls_back_settings_audit_and_receipt(calculation_http, failure):
    c = calculation_http
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.fail_complete = {"complete": "error", "authority": "membership"}[failure]
    response = await call(c)
    assert response.status_code == (403 if failure == "authority" else 500), response.text
    assert "private" not in response.text
    assert [len(rows) for rows in snapshot(c)] == [0, 0, 0]


def test_openapi_has_three_operations_and_exact_bigint_bounds(calculation_http):
    document = calculation_http.app.openapi()
    operations = document["paths"][PATH]
    assert {"get", "post", "patch"}.issubset(operations)
    schemas = document["components"]["schemas"]
    expected = {
        "CalculationSettingsReadData": "row_version",
        "CalculationSettingsWriteData": "row_version",
        "CalculationSettingsPatchRequest": "expected_row_version",
    }
    found = set()
    for schema in schemas.values():
        title = schema.get("title")
        if title in expected:
            field = schema["properties"][expected[title]]
            assert field["minimum"] == 1
            assert field["maximum"] == 2**63 - 1
            assert type(field["maximum"]) is int and field["format"] == "int64"
            found.add(title)
    assert found == set(expected)
    create = schemas["CalculationSettingsCreateRequest"]
    assert set(create["required"]) == {"money_input_precision", "supplier_stale_price_days"}
    assert create["properties"]["quantity_precision"]["default"] == 4
    patch = schemas["CalculationSettingsPatchRequest"]
    assert patch["required"] == ["expected_row_version"]
    assert patch["properties"]["quantity_precision"]["type"] == "integer"
    for method in ("post", "patch"):
        header = next(
            item for item in operations[method]["parameters"]
            if item["name"] == "Idempotency-Key")
        assert header["required"] is False
