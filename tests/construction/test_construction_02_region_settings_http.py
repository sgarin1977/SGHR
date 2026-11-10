"""Registered region HTTP with real local repository/service; no deployed acceptance."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import update

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_session, get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_region_settings as routes
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
    ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_organization_region import (
    OrganizationRegionState, OrganizationRegionResult,
)
from test_construction_02_organization_region import region, snapshot

PATH = "/api/v1/construction/settings/region"


@pytest.fixture
def region_http(region):
    c = region
    c.app = create_app()
    c.suite = NS(require_enabled=AsyncMock())
    c.core = NS(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(wraps=c.rates.ensure_authenticated_user_allowed)
    c.service.read_for_actor = AsyncMock(wraps=c.service.read_for_actor)
    c.service.update_for_actor = AsyncMock(wraps=c.service.update_for_actor)

    async def platform_session():
        # Snapshot reads must not supply the request transaction.
        if c.sync.in_transaction():
            c.sync.rollback()
        c.sync.begin()
        try:
            yield c.session
        finally:
            if c.sync.in_transaction():
                await c.session.rollback()

    async def service(session=Depends(get_api_session)):
        assert session is c.session
        return c.service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_api_session] = platform_session
    c.app.dependency_overrides[routes.get_region_settings_service] = service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, method="GET", payload=None, suffix=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        headers = {"X-Request-ID": "region-http"}
        if method == "GET":
            return await client.get(PATH + suffix, headers=headers)
        return await client.patch(
            PATH + suffix, headers=headers,
            json=dict(country_code="UA", timezone="Europe/Kyiv", expected_row_version=1)
                 if payload is None else payload)


@pytest.mark.asyncio
async def test_registered_get_returns_nullable_whitelist_and_shared_gates(region_http):
    c = region_http
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"] == {} and body["request_id"] == "region-http"
    assert set(body["data"]) == set(routes.RegionSettingsReadData.model_fields)
    assert body["data"]["tenant_id"] == str(c.tenant)
    assert body["data"]["country_code"] is None and body["data"]["timezone"] is None
    assert body["data"]["configured"] is False and body["data"]["row_version"] == 1
    assert body["data"]["organization_updated_at"].endswith("Z")
    assert "metadata" not in body["data"] and "billing" not in response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert snapshot(c) == before and c.session.commits == 0
    c.serialization.lock_member.assert_not_awaited()
    c.events.create_event.assert_not_awaited()
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_patch_uses_existing_authority_and_commits_correlated_audit(region_http, role):
    c = region_http
    c.grant.role = role
    before = snapshot(c)
    response = await call(c, "PATCH")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == set(routes.RegionSettingsWriteData.model_fields)
    assert data["configured"] is True and data["changed"] is True and data["row_version"] == 2
    assert data["country_code"] == "UA" and data["timezone"] == "Europe/Kyiv"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert c.session.commits == 1
    after = snapshot(c)
    foreign = lambda rows: next(row for row in rows if row["id"] == c.foreign)
    assert foreign(after[0]) == foreign(before[0]) and after[1] == before[1]
    own = next(row for row in after[0] if row["id"] == c.tenant)
    assert own["metadata"]["billing"] == "preserve" and own["metadata"]["keep"] == {"nested": [1, 2]}
    assert len(after[2]) == 1 and after[2][0]["trace_id"] == "region-http"
    assert after[2][0]["entity_id"] == c.tenant
    read = await call(c)
    assert read.status_code == 200 and read.json()["data"]["row_version"] == 2
    assert read.json()["data"]["timezone"] == data["timezone"]


@pytest.mark.asyncio
async def test_same_version_retry_is_conflict_and_current_noop_has_no_new_audit(region_http):
    c = region_http
    assert (await call(c, "PATCH")).status_code == 200
    before = snapshot(c)
    stale = await call(c, "PATCH")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before
    noop = await call(c, "PATCH", {"country_code": "UA", "timezone": "Europe/Kyiv", "expected_row_version": 2})
    assert noop.status_code == 200, noop.text
    assert noop.json()["data"]["changed"] is False and noop.json()["data"]["row_version"] == 2
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PATCH"])
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_transport_gates_precede_domain_access(region_http, method, gate):
    c = region_http
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
    before = snapshot(c)
    response = await call(c, method)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    c.service.read_for_actor.assert_not_awaited()
    c.service.update_for_actor.assert_not_awaited()
    c.events.create_event.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PATCH"])
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "observer", "estimator"])
async def test_fresh_business_authority_remains_mandatory(region_http, method, failure):
    c = region_http
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    elif failure == "expired":
        c.grant.expires_at = c.now
    else:
        c.grant.role = "OBSERVER" if failure == "observer" else "ESTIMATOR"
        c.grant.scope_type, c.grant.scope_id = "project", uuid4()
    before = snapshot(c)
    response = await call(c, method)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PATCH"])
@pytest.mark.parametrize("suffix", ["?tenant_id=foreign", "?user_id=foreign", "?scope=tenant"])
async def test_query_selectors_are_rejected(region_http, method, suffix):
    c = region_http
    before = snapshot(c)
    response = await call(c, method, suffix=suffix)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    c.service.read_for_actor.assert_not_awaited()
    c.service.update_for_actor.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"country_code": None}, {"country_code": "ua"}, {"country_code": "UA "},
    {"timezone": None}, {"timezone": "Invalid/Zone"}, {"timezone": " Europe/Kyiv"},
    {"expected_row_version": None}, {"expected_row_version": True},
    {"expected_row_version": 1.0}, {"expected_row_version": "1"},
    {"expected_row_version": 0}, {"expected_row_version": 2**63},
    {"tenant_id": "foreign"}, {"user_id": "foreign"}, {"metadata": {}},
])
async def test_body_is_strict_and_accepts_no_identity_or_storage_fields(region_http, changes):
    c = region_http
    payload = {"country_code": "UA", "timezone": "Europe/Kyiv", "expected_row_version": 1}
    payload.update(changes)
    response = await call(c, "PATCH", payload)
    assert response.status_code == 422, response.text
    c.service.update_for_actor.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["country_code", "timezone", "expected_row_version"])
async def test_all_update_fields_are_explicitly_required(region_http, missing):
    c = region_http
    payload = {"country_code": "UA", "timezone": "Europe/Kyiv", "expected_row_version": 1}
    del payload[missing]
    assert (await call(c, "PATCH", payload)).status_code == 422
    c.service.update_for_actor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("country", ["XX", "ZZ"])
async def test_format_valid_country_still_requires_active_platform_catalog(region_http, country):
    c = region_http
    before = snapshot(c)
    response = await call(c, "PATCH", {"country_code": country, "timezone": "Europe/Kyiv", "expected_row_version": 1})
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "authority"])
async def test_http_failure_rolls_back_local_region_and_audit_together(region_http, failure):
    c = region_http
    if failure == "commit":
        c.session.fail_commit = True
    else:
        c.fail = failure
    before = snapshot(c)
    response = await call(c, "PATCH")
    assert response.status_code == (403 if failure == "authority" else 500), response.text
    assert "private" not in response.text.lower()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PATCH"])
async def test_incompatible_stored_region_returns_safe_configuration_error(region_http, method):
    c = region_http
    c.sync.execute(update(c.tenants).where(c.tenants.c.id == c.tenant).values(
        metadata={"regional_settings": None, "billing": "PRIVATE_METADATA"}))
    c.sync.commit()
    before = snapshot(c)
    response = await call(c, method)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "REGION_CONFIGURATION_REQUIRED"
    assert "PRIVATE_METADATA" not in response.text and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "version", "pair", "time", "exception"])
async def test_bad_read_projections_and_private_errors_are_sanitized(region_http, failure):
    c = region_http
    state = OrganizationRegionState(c.tenant, None, None, 1, c.now)
    if failure == "exception":
        c.service.read_for_actor.side_effect = RuntimeError("PRIVATE_DETAILS")
    else:
        values = {
            "tenant": {"tenant_id": c.foreign}, "version": {"row_version": True},
            "pair": {"timezone": "Europe/Kyiv"}, "time": {"updated_at": c.now.replace(tzinfo=None)},
        }[failure]
        c.service.read_for_actor = AsyncMock(return_value=replace(state, **values))
    response = await call(c)
    assert response.status_code == 500, response.text
    assert "PRIVATE_DETAILS" not in response.text and str(c.foreign) not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["changed", "version", "region"])
async def test_inconsistent_write_result_is_not_returned_as_success(region_http, failure):
    c = region_http
    state = OrganizationRegionState(c.tenant, "UA", "Europe/Kyiv", 2, c.now)
    changed = True
    if failure == "changed":
        changed = "true"
    elif failure == "version":
        state = replace(state, row_version=1)
    else:
        state = replace(state, country_code="PT")
    c.service.update_for_actor = AsyncMock(return_value=OrganizationRegionResult(state, changed))
    response = await call(c, "PATCH")
    assert response.status_code == 500, response.text


def test_registered_openapi_keeps_exact_bigint_and_no_replay_key_requirement(region_http):
    document = region_http.app.openapi()
    assert set(document["paths"][PATH]) == {"get", "patch"}
    schemas = document["components"]["schemas"]
    request = schemas["RegionSettingsUpdate"]
    assert set(request["required"]) == {"country_code", "timezone", "expected_row_version"}
    assert request["additionalProperties"] is False
    for name, field in (
        ("RegionSettingsUpdate", "expected_row_version"),
        ("RegionSettingsReadData", "row_version"),
        ("RegionSettingsWriteData", "row_version"),
    ):
        bound = schemas[name]["properties"][field]["maximum"]
        assert type(bound) is int and bound == 2**63 - 1
    for operation in document["paths"][PATH].values():
        assert not any(p.get("name") == "Idempotency-Key" for p in operation.get("parameters", []))
        assert operation.get("security")
