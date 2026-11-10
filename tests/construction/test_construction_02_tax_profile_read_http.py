"""Registered HTTP + real local tax repository/service; no native/deployed acceptance."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_tax_profiles as routes
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import (
    ApiRequestRateLimitService, ApiRequestRateLimitExceededError, InMemoryApiRequestRateLimitStore,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_tax_profiles import case, create, rows

PATH = "/api/v1/construction/settings/tax-profiles/"


@pytest.fixture
async def tax_http(case):
    c = case
    c.profile = (await create(c)).profile
    c.barrier_taken = False
    c.serialization.lock_member.reset_mock()
    c.events.create_event.reset_mock()
    c.repository.get_profile = AsyncMock(wraps=c.repository.get_profile)
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
        return await c.service.resolver.resolve(actor=c.actor)

    async def read_service(session=Depends(get_construction_scoped_session)):
        assert session is c.session
        return c.service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = scoped_session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_tax_profile_read_service] = read_service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, suffix=None, query=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.get(PATH + (str(c.profile.id) if suffix is None else suffix) + query,
                                headers={"X-Request-ID": "tax-read-http"})


@pytest.mark.asyncio
async def test_registered_read_is_typed_private_and_creates_no_writes(tax_http):
    c = tax_http
    before = list(rows(c, c.table)), list(rows(c, c.audits)), c.session.commits
    c.sync.rollback()  # Snapshot SELECTs must not supply the HTTP transaction.
    response = await call(c)
    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert body["meta"] == {} and body["request_id"] == "tax-read-http"
    assert set(data) == set(routes.TaxProfileReadData.model_fields)
    assert data["id"] == str(c.profile.id) and data["tenant_id"] == str(c.tenant)
    assert data["created_by"] == str(c.user) and data["row_version"] == 1
    assert data["client_price_tax_mode"] == "TAX_EXCLUSIVE" and data["material_cost_basis"] == "NET"
    assert data["created_at"].endswith("Z") and data["updated_at"].endswith("Z")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert (list(rows(c, c.table)), list(rows(c, c.audits)), c.session.commits) == before
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_transport_gates_run_before_profile_read(tax_http, gate):
    c = tax_http
    expected_status, expected_code = 403, "MODULE_DISABLED"
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(status_code=401, code="authentication_required", message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        expected_status, expected_code = 401, "authentication_required"
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.core.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected_status, expected_code = 429, "RATE_LIMIT_EXCEEDED"
    response = await call(c)
    assert response.status_code == expected_status, response.text
    assert response.json()["error"]["code"] == expected_code
    c.repository.get_profile.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_allowed_tenant_roles_use_existing_tax_read_policy(tax_http, role):
    c = tax_http
    c.grant.role = role
    response = await call(c)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "observer_project", "manager_project", "executor"])
async def test_fresh_membership_permission_and_scope_are_required(tax_http, failure):
    c = tax_http
    if failure == "membership": c.membership = False
    elif failure == "revoked": c.grant.status = "revoked"
    elif failure == "expired": c.grant.expires_at = c.now
    else:
        c.grant.role = {"observer_project": "OBSERVER", "manager_project": "PROJECT_MANAGER", "executor": "EXECUTOR"}[failure]
        c.grant.scope_type, c.grant.scope_id = "project", uuid4()
    response = await call(c)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] in {"forbidden", "permission_required"}
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_and_missing_profiles_have_identical_safe_not_found(tax_http):
    c = tax_http
    c.sync.execute(c.table.update().where(c.table.c.id == c.profile.id).values(tenant_id=c.foreign))
    c.sync.commit()
    foreign = await call(c)
    missing = await call(c, suffix=str(uuid4()))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert c.profile.name not in foreign.text and str(c.foreign) not in foreign.text


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["?tenant_id=foreign", "?user_id=foreign", "?scope=tenant", "?limit=10", "?anything=1"])
async def test_no_caller_tenant_user_or_query_selectors(tax_http, query):
    c = tax_http
    response = await call(c, query=query)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    c.repository.get_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_uuid_is_validation_error(tax_http):
    response = await call(tax_http, suffix="not-a-uuid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "tenant", "id", "version", "time"])
async def test_private_failures_and_invalid_service_projections_are_sanitized(tax_http, failure):
    c = tax_http
    if failure == "exception":
        c.service.get_profile = AsyncMock(side_effect=RuntimeError("PRIVATE_TAX_DATABASE_DETAILS"))
    else:
        changes = {
            "tenant": {"tenant_id": c.foreign}, "id": {"id": uuid4()},
            "version": {"row_version": True}, "time": {"updated_at": c.now.replace(tzinfo=None)},
        }[failure]
        c.service.get_profile = AsyncMock(return_value=replace(c.profile, **changes))
    response = await call(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert "PRIVATE_TAX_DATABASE_DETAILS" not in response.text
    assert c.profile.name not in response.text


@pytest.mark.asyncio
async def test_published_openapi_keeps_exact_bigint_and_shared_security(tax_http):
    c = tax_http
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    operation = document["paths"][PATH + "{profile_id}"]["get"]
    assert operation["security"] and "requestBody" not in operation
    assert {401, 403, 404, 422, 429, 500} <= {int(key) for key in operation["responses"]}
    version = document["components"]["schemas"]["TaxProfileReadData"]["properties"]["row_version"]
    assert type(version["minimum"]) is int and version["minimum"] == 1
    assert type(version["maximum"]) is int and version["maximum"] == 2**63 - 1
    assert version["format"] == "int64"


def test_factory_passes_the_scoped_session_without_creating_a_second_session(monkeypatch):
    session, sentinel = object(), object()
    received = []
    def factory(*, session):
        received.append(session)
        return sentinel
    monkeypatch.setattr(routes, "build_construction_tax_profile_service", factory)
    assert routes.get_tax_profile_read_service(session=session) is sentinel
    assert received == [session]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["membership", "grant"])
async def test_authority_is_rechecked_after_profile_query(tax_http, change):
    c = tax_http
    original = c.repository.get_profile
    async def read(**kwargs):
        row = await original(**kwargs)
        if change == "membership":
            c.membership = False
        else:
            c.grant.status = "revoked"
        return row
    c.repository.get_profile = AsyncMock(side_effect=read)
    response = await call(c)
    assert response.status_code == 403, response.text
    assert c.profile.name not in response.text
    c.events.create_event.assert_not_awaited()
