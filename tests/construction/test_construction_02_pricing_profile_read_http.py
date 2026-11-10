"""Registered CON-017 reads with real local pricing service/repository."""
from dataclasses import replace
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service)
from api.errors import ApiHttpError
from api.routes import construction_pricing_profiles as routes
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import (
    ApiRequestRateLimitService, ApiRequestRateLimitExceededError, InMemoryApiRequestRateLimitStore)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_tax_profiles import case
from test_construction_02_pricing_profiles import pricing, create, snapshot

PATH = "/api/v1/construction/settings/pricing-profiles"


@pytest.fixture
async def pricing_http(pricing):
    c = pricing
    c.profile = (await create(c)).profile
    c.serialization.lock_member.reset_mock()
    c.events.create_event.reset_mock()
    c.price_repository.get_profile = AsyncMock(wraps=c.price_repository.get_profile)
    c.price_repository.list_profiles = AsyncMock(wraps=c.price_repository.list_profiles)
    c.app = create_app()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.core = SimpleNamespace(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(wraps=c.rates.ensure_authenticated_user_allowed)

    async def scoped_session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(session=Depends(get_construction_scoped_session)):
        assert session is c.session
        return await c.price_service.resolver.resolve(actor=c.actor)

    async def read_service(session=Depends(get_construction_scoped_session)):
        assert session is c.session
        return c.price_service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = scoped_session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_pricing_profile_read_service] = read_service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, *, collection=False, profile_id=None, query=""):
    url = PATH if collection else PATH + "/" + str(c.profile.id if profile_id is None else profile_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="https://testserver",
    ) as client:
        return await client.get(url + query, headers={"X-Request-ID": "pricing-read-http"})


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", [False, True])
async def test_registered_private_read_uses_shared_gates_and_has_no_writes(pricing_http, collection):
    c = pricing_http
    before, commits = snapshot(c), c.session.commits
    c.sync.rollback()
    response = await call(c, collection=collection)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_id"] == "pricing-read-http"
    assert body["meta"] == ({"pagination": {"next_cursor": None}} if collection else {})
    data = body["data"][0] if collection else body["data"]
    assert set(data) == set(routes.PricingProfileReadData.model_fields)
    assert data["id"] == str(c.profile.id) and data["tenant_id"] == str(c.tenant)
    assert data["code"] == "STANDARD" and data["created_by"] == str(c.user) and data["row_version"] == 1
    assert data["work_coefficient"] == "1.00" and data["material_markup_percent"] == "10.00"
    assert data["created_at"].endswith("Z") and data["updated_at"].endswith("Z")
    assert response.headers["Cache-Control"] == "private, no-store" and response.headers["Pragma"] == "no-cache"
    assert snapshot(c) == before and c.session.commits == commits
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")


@pytest.mark.asyncio
async def test_all_three_levels_are_returned_once_in_price_order(pricing_http):
    c = pricing_http
    await create(c, code="PREMIUM")
    await create(c, code="ECONOMY")
    response = await call(c, collection=True)
    assert response.status_code == 200, response.text
    items = response.json()["data"]
    assert [item["code"] for item in items] == ["ECONOMY", "STANDARD", "PREMIUM"]
    assert len({item["id"] for item in items}) == 3
    assert response.json()["meta"] == {"pagination": {"next_cursor": None}}


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", [False, True])
async def test_long_decimal_values_are_serialized_without_rounding(pricing_http, collection):
    c = pricing_http
    coefficient = Decimal("1.234567890123456789012345678901234567890123456789")
    markup = Decimal("12.3456789012345678901234567890123456789")
    c.profile = (await create(c, code="ECONOMY", work_coefficient=coefficient, material_markup_percent=markup)).profile
    with localcontext() as context:
        context.prec = 2
        response = await call(c, collection=collection)
    assert response.status_code == 200, response.text
    data = response.json()["data"][0] if collection else response.json()["data"]
    assert data["work_coefficient"] == str(coefficient) and data["material_markup_percent"] == str(markup)


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", [False, True])
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_transport_gates_precede_pricing_reads(pricing_http, collection, gate):
    c = pricing_http
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
    response = await call(c, collection=collection)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    c.price_repository.get_profile.assert_not_awaited()
    c.price_repository.list_profiles.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("collection", [False, True])
async def test_existing_tenant_pricing_read_authority_is_accepted(pricing_http, role, collection):
    c = pricing_http
    c.grant.role = role
    response = await call(c, collection=collection)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", [False, True])
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "observer_project", "manager_project", "executor"])
async def test_fresh_membership_permission_and_organization_scope_are_required(pricing_http, collection, failure):
    c = pricing_http
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    elif failure == "expired":
        c.grant.expires_at = c.now
    else:
        c.grant.role = {"observer_project": "OBSERVER", "manager_project": "PROJECT_MANAGER", "executor": "EXECUTOR"}[failure]
        c.grant.scope_type, c.grant.scope_id = "project", uuid4()
    response = await call(c, collection=collection)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] in {"permission_required", "forbidden"}
    assert "data" not in response.json()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_and_missing_detail_are_identical_and_list_stays_empty(pricing_http):
    c = pricing_http
    c.sync.execute(c.prices.update().where(c.prices.c.id == c.profile.id).values(tenant_id=c.foreign))
    c.sync.commit()
    foreign = await call(c)
    missing = await call(c, profile_id=uuid4())
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert str(c.foreign) not in foreign.text
    response = await call(c, collection=True)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == [] and response.json()["meta"]["pagination"]["next_cursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", [False, True])
@pytest.mark.parametrize("query", ["?tenant_id=other", "?user_id=other", "?scope=tenant",
                                  "?limit=1", "?cursor=anything", "?tenant_id=a&tenant_id=b"])
async def test_no_caller_context_or_pagination_selectors(pricing_http, collection, query):
    c = pricing_http
    response = await call(c, collection=collection, query=query)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    c.price_repository.get_profile.assert_not_awaited()
    c.price_repository.list_profiles.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_profile_uuid_is_validation_error(pricing_http):
    response = await call(pricing_http, profile_id="not-a-uuid")
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "exception", "tenant", "id", "creator_string", "version", "time", "time_string",
    "code", "coefficient_float", "coefficient_nan", "coefficient_zero", "markup_inf", "markup_range"
])
async def test_invalid_detail_or_private_failure_is_sanitized(pricing_http, failure):
    c = pricing_http
    if failure == "exception":
        c.price_service.get_profile = AsyncMock(side_effect=RuntimeError("PRIVATE_PRICING_DATABASE_DETAILS"))
    else:
        changes = {
            "tenant": {"tenant_id": c.foreign}, "id": {"id": uuid4()},
            "creator_string": {"created_by": str(c.user)}, "version": {"row_version": True},
            "time": {"updated_at": c.now.replace(tzinfo=None)}, "time_string": {"updated_at": c.now.isoformat()},
            "code": {"code": "CUSTOM"}, "coefficient_float": {"work_coefficient": 1.0},
            "coefficient_nan": {"work_coefficient": Decimal("NaN")}, "coefficient_zero": {"work_coefficient": Decimal(0)},
            "markup_inf": {"material_markup_percent": Decimal("Infinity")},
            "markup_range": {"material_markup_percent": Decimal("100.01")},
        }[failure]
        c.price_service.get_profile = AsyncMock(return_value=replace(c.profile, **changes))
    response = await call(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert "PRIVATE_PRICING_DATABASE_DETAILS" not in response.text and "data" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["type", "duplicate", "order", "overflow", "foreign", "late_float", "same_id"])
async def test_invalid_collection_never_returns_partial_data(pricing_http, failure):
    c = pricing_http
    economy = replace(c.profile, code="ECONOMY", id=uuid4())
    states = {
        "type": [c.profile], "duplicate": (c.profile, c.profile),
        "order": (replace(c.profile, code="PREMIUM", id=uuid4()), c.profile),
        "overflow": (c.profile,) * 4,
        "foreign": (replace(economy, tenant_id=c.foreign), c.profile),
        "late_float": (economy, replace(c.profile, work_coefficient=1.0)),
        "same_id": (replace(economy, id=c.profile.id), c.profile),
    }[failure]
    c.price_service.list_profiles = AsyncMock(return_value=states)
    response = await call(c, collection=True)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error" and "data" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", [False, True])
@pytest.mark.parametrize("change", ["membership", "grant"])
async def test_authority_is_refreshed_after_storage_query(pricing_http, collection, change):
    c = pricing_http
    name = "list_profiles" if collection else "get_profile"
    original = getattr(c.price_repository, name)
    async def read(**kwargs):
        result = await original(**kwargs)
        if change == "membership":
            c.membership = False
        else:
            c.grant.status = "revoked"
        return result
    setattr(c.price_repository, name, AsyncMock(side_effect=read))
    response = await call(c, collection=collection)
    assert response.status_code == 403, response.text
    assert "data" not in response.json()


@pytest.mark.asyncio
async def test_openapi_documents_shared_security_exact_bigint_and_decimal_strings(pricing_http):
    c = pricing_http
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    doc = response.json()
    for path in (PATH, PATH + "/{profile_id}"):
        operation = doc["paths"][path]["get"]
        assert operation["security"] and "requestBody" not in operation
        assert {401, 403, 422, 429, 500} <= {int(code) for code in operation["responses"]}
        assert all(parameter["name"].lower() != "idempotency-key"
                   for parameter in operation.get("parameters", []))
    properties = doc["components"]["schemas"]["PricingProfileReadData"]["properties"]
    version = properties["row_version"]
    assert type(version["maximum"]) is int and version["maximum"] == 2**63 - 1
    assert version["minimum"] == 1 and version["format"] == "int64"
    assert properties["work_coefficient"]["type"] == "string"
    assert properties["material_markup_percent"]["type"] == "string"


def test_factory_reuses_the_scoped_construction_session(monkeypatch):
    session, sentinel = object(), object()
    received = []
    def factory(*, session):
        received.append(session)
        return sentinel
    monkeypatch.setattr(routes, "build_construction_pricing_profile_service", factory)
    assert routes.get_pricing_profile_read_service(session=session) is sentinel
    assert received == [session]
