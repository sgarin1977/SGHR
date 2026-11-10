"""Registered onboarding HTTP; real local services and audit, no external operations."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

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
from api.routes import construction_onboarding as routes
from api.routes.construction import (
    get_construction_access_context, get_construction_scoped_session,
)
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
    ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_onboarding_tax_selection import (
    selected_tax, onboarding, case, everything,
)
from test_construction_02_onboarding_progress import seed

PATH = "/api/v1/construction/settings/onboarding"


@pytest.fixture
async def onboarding_http(selected_tax):
    c = selected_tax
    c.sync.rollback()
    c.session.get_bind = c.sync.get_bind
    c.app = create_app()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.core = SimpleNamespace(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(
        wraps=c.rates.ensure_authenticated_user_allowed)

    async def session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return await c.progress.resolver.resolve(actor=c.actor)

    async def progress_service(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.progress

    async def tax_service(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.selection

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_onboarding_service] = progress_service
    c.app.dependency_overrides[routes.get_onboarding_tax_service] = tax_service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, method="GET", suffix="", *, body=None, query=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            method, PATH + suffix + query, json=body,
            headers={"X-Request-ID": "onboarding-http"})


async def write(c, suffix, version, *, method="POST", **fields):
    response = await call(
        c, method, suffix, body={"expected_row_version": version} | fields)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def open_taxes(c):
    started = await write(c, "/start", None)
    return await write(c, "/steps/TAXES/open", started["row_version"])


@pytest.mark.asyncio
async def test_reads_do_not_start_guide_or_select_existing_profile(onboarding_http):
    c = onboarding_http
    before = everything(c)
    response = await call(c)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tenant_id"] == str(c.tenant)
    assert data["row_version"] is None and data["current_step"] is None
    assert data["step_states"] is None
    assert data["started"] is False and data["is_complete"] is False
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, module_code="construction.core")
    selection = await call(c, suffix="/tax-profile")
    assert selection.status_code == 200 and selection.json()["data"] is None
    assert everything(c) == before and c.session.commits == 0
    c.service.get_profile.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_selection_and_completion_without_tax_rules(onboarding_http):
    c = onboarding_http
    state = await open_taxes(c)
    before = everything(c)
    not_ready = await call(c, "POST", "/steps/TAXES/complete", body={
        "expected_row_version": state["row_version"]})
    assert not_ready.status_code == 409, not_ready.text
    assert not_ready.json()["error"]["code"] == "ONBOARDING_STEP_NOT_READY"
    assert everything(c) == before

    selected = await write(
        c, "/tax-profile", state["row_version"], method="PATCH",
        tax_profile_id=str(c.profile))
    assert selected["onboarding_tax_profile_id"] == str(c.profile)
    assert selected["changed"] is True
    stale = await call(c, "POST", "/steps/TAXES/complete", body={
        "expected_row_version": state["row_version"]})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"

    current = (await call(c)).json()["data"]
    completed = await write(c, "/steps/TAXES/complete", current["row_version"])
    assert completed["step_states"]["TAXES"] == "COMPLETED"
    assert completed["current_step"] == "PRICING_PROFILES"
    assert completed["is_complete"] is False
    assert "construction_tax_rules" not in c.table.metadata.tables
    assert everything(c)[1] == before[1]
    assert all(
        event["entity_id"] == c.tenant and event["trace_id"] == "onboarding-http"
        for event in everything(c)[0][1])
    read = await call(c, suffix="/tax-profile")
    assert read.status_code == 200
    assert read.json()["data"]["row_version"] == completed["row_version"]
    assert read.json()["data"]["onboarding_tax_profile_id"] == str(c.profile)


@pytest.mark.asyncio
async def test_current_noops_and_stale_retries(onboarding_http):
    c = onboarding_http
    started = await write(c, "/start", None)
    before = everything(c)
    noop = await write(c, "/start", started["row_version"])
    assert noop["changed"] is False and everything(c) == before
    stale = await call(c, "POST", "/start", body={"expected_row_version": None})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert everything(c) == before


@pytest.mark.asyncio
async def test_existing_branding_requires_current_shared_version(onboarding_http):
    c = onboarding_http
    seed(c, version=7)
    before = everything(c)
    read = (await call(c)).json()["data"]
    assert read["row_version"] == 7 and read["started"] is False
    stale = await call(c, "POST", "/start", body={"expected_row_version": None})
    assert stale.status_code == 409 and everything(c) == before
    started = await write(c, "/start", 7)
    assert started["row_version"] == 8 and started["settings_created"] is False
    saved = next(row for row in everything(c)[0][0] if row["tenant_id"] == c.tenant)
    assert saved["display_name_override"] == "Existing company"
    assert saved["phone_override"] == "+123"


@pytest.mark.asyncio
async def test_only_optional_steps_can_be_skipped_and_progress_can_be_reopened(onboarding_http):
    c = onboarding_http
    state = await write(c, "/start", None)
    invalid = await call(c, "POST", "/steps/TAXES/skip", body={
        "expected_row_version": state["row_version"]})
    assert invalid.status_code == 422
    opened = await write(c, "/steps/WORK_PRICES/open", state["row_version"])
    work = await write(c, "/steps/WORK_PRICES/skip", opened["row_version"])
    materials = await write(c, "/steps/MATERIALS_SUPPLIERS/skip", work["row_version"])
    assert materials["current_step"] == "MEASUREMENT_TEMPLATES"
    assert materials["step_states"]["WORK_PRICES"] == "SKIPPED"
    assert materials["step_states"]["MATERIALS_SUPPLIERS"] == "SKIPPED"
    reopened = await write(c, "/steps/COMPANY/open", materials["row_version"])
    assert reopened["current_step"] == "COMPANY"
    assert reopened["step_states"] == materials["step_states"]


@pytest.mark.asyncio
async def test_unconfigured_completion_fails_closed(onboarding_http):
    c = onboarding_http
    state = await write(c, "/start", None)
    opened = await write(c, "/steps/MEASUREMENT_TEMPLATES/open", state["row_version"])
    before = everything(c)
    response = await call(c, "POST", "/steps/MEASUREMENT_TEMPLATES/complete", body={
        "expected_row_version": opened["row_version"]})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ONBOARDING_STEP_UNAVAILABLE"
    assert everything(c) == before


@pytest.mark.asyncio
async def test_foreign_and_missing_profile_have_same_safe_result(onboarding_http):
    c = onboarding_http
    state = await open_taxes(c)
    before = everything(c)
    responses = [
        await call(c, "PATCH", "/tax-profile", body={
            "expected_row_version": state["row_version"],
            "tax_profile_id": str(profile),
        })
        for profile in (c.foreign_profile, uuid4())
    ]
    assert all(response.status_code == 404 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"expected_row_version": True}, {"expected_row_version": "1"},
    {"expected_row_version": 0}, {"expected_row_version": 2**63},
    {"expected_row_version": None, "tenant_id": "foreign"},
    {"expected_row_version": None, "completed": True},
    {"expected_row_version": None, "manage_transaction": False},
])
async def test_start_requires_explicit_valid_version_and_no_caller_flags(onboarding_http, body):
    c = onboarding_http
    before = everything(c)
    response = await call(c, "POST", "/start", body=body)
    assert response.status_code == 422, response.text
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"expected_row_version": None}, {"expected_row_version": True},
    {"expected_row_version": "1"}, {"expected_row_version": 2**63},
    {"expected_row_version": 1, "step_states": {"TAXES": "COMPLETED"}},
    {"expected_row_version": 1, "completed": True},
])
async def test_step_commands_reject_invalid_versions_and_completion_flags(onboarding_http, body):
    c = onboarding_http
    before = everything(c)
    response = await call(c, "POST", "/steps/TAXES/complete", body=body)
    assert response.status_code == 422, response.text
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None),
    ("GET", "/tax-profile", None),
    ("POST", "/start", {"expected_row_version": None}),
    ("POST", "/steps/TAXES/open", {"expected_row_version": 1}),
    ("POST", "/steps/WORK_PRICES/skip", {"expected_row_version": 1}),
    ("POST", "/steps/TAXES/complete", {"expected_row_version": 1}),
    ("PATCH", "/tax-profile", {"expected_row_version": 1, "tax_profile_id": str(uuid4())}),
])
async def test_all_operations_reject_query_selectors(onboarding_http, method, suffix, body):
    c = onboarding_http
    before = everything(c)
    response = await call(c, method, suffix, body=body, query="?tenant_id=foreign")
    assert response.status_code == 422, response.text
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate", "membership", "scope"])
@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None),
    ("POST", "/start", {"expected_row_version": None}),
    ("PATCH", "/tax-profile", {"expected_row_version": 1, "tax_profile_id": str(uuid4())}),
])
async def test_shared_and_fresh_authority_gates(onboarding_http, gate, method, suffix, body):
    c = onboarding_http
    before = everything(c)
    expected = 403
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(
                status_code=401, code="authentication_required",
                message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        expected = 401
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.core.require_enabled.side_effect = ModuleDisabledError()
    elif gate == "rate":
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected = 429
    elif gate == "membership":
        c.membership = False
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    response = await call(c, method, suffix, body=body)
    assert response.status_code == expected, response.text
    assert everything(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "select", "complete"])
@pytest.mark.parametrize("failure", ["audit", "commit", "authority"])
async def test_http_failures_roll_back_metadata_version_and_audit(
    onboarding_http, operation, failure,
):
    c = onboarding_http
    version = None
    if operation != "start":
        state = await open_taxes(c)
        version = state["row_version"]
    if operation == "complete":
        state = await write(
            c, "/tax-profile", version, method="PATCH",
            tax_profile_id=str(c.profile))
        version = state["row_version"]
    before = everything(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.after_audit = lambda: setattr(c, "membership", False)
    suffix = {
        "start": "/start", "select": "/tax-profile",
        "complete": "/steps/TAXES/complete",
    }[operation]
    body = {"expected_row_version": version}
    if operation == "select":
        body["tax_profile_id"] = str(c.profile)
    response = await call(
        c, "PATCH" if operation == "select" else "POST",
        suffix, body=body)
    assert response.status_code == (403 if failure == "authority" else 500), response.text
    assert "private" not in response.text
    assert everything(c) == before


def test_openapi_has_versioned_operations_and_exact_nullable_bigint(onboarding_http):
    document = onboarding_http.app.openapi()
    expected = {
        "": {"get"},
        "/start": {"post"},
        "/steps/{step}/open": {"post"},
        "/steps/{step}/skip": {"post"},
        "/steps/{step}/complete": {"post"},
        "/tax-profile": {"get", "patch"},
    }
    for suffix, methods in expected.items():
        operations = document["paths"][PATH + suffix]
        assert methods.issubset(operations)
        for method in methods:
            assert operations[method]["security"]
            assert all(
                parameter["name"] != "Idempotency-Key"
                for parameter in operations[method].get("parameters", []))
    schemas = document["components"]["schemas"]
    targets = {
        "OnboardingProgressData": "row_version",
        "OnboardingProgressWriteData": "row_version",
        "OnboardingStartRequest": "expected_row_version",
        "OnboardingVersionRequest": "expected_row_version",
        "OnboardingTaxSelectionData": "row_version",
        "OnboardingTaxSelectionWriteData": "row_version",
        "OnboardingTaxSelectionUpdate": "expected_row_version",
    }
    found = set()
    for schema in schemas.values():
        title = schema.get("title")
        if title not in targets:
            continue
        field = schema["properties"][targets[title]]
        if "anyOf" in field:
            assert title in ("OnboardingProgressData", "OnboardingStartRequest")
            assert {part.get("type") for part in field["anyOf"]} == {"integer", "null"}
            field = next(part for part in field["anyOf"] if part.get("type") == "integer")
        assert field["minimum"] == 1
        assert type(field["maximum"]) is int
        assert field["maximum"] == 2**63 - 1 and field["format"] == "int64"
        found.add(title)
    assert found == set(targets)
    start_schema = schemas["OnboardingStartRequest"]
    assert start_schema["required"] == ["expected_row_version"]
    assert "default" not in start_schema["properties"]["expected_row_version"]
