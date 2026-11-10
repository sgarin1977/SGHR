"""Registered shared API with a real local tax-rule service/repository."""
from dataclasses import replace
from decimal import Decimal
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
from api.routes import construction_tax_rules as routes
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import (
    ApiRequestRateLimitService, ApiRequestRateLimitExceededError, InMemoryApiRequestRateLimitStore,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_tax_profiles import case
from test_construction_02_tax_rules import rule_case, create, snapshot

PATH = "/api/v1/construction/settings/tax-profiles/"


@pytest.fixture
async def tax_rule_http(rule_case):
    c = rule_case
    c.rule = (await create(c)).rule
    c.barrier_taken = False
    c.serialization.lock_member.reset_mock()
    c.events.create_event.reset_mock()
    c.rule_repo.get_profile_id = AsyncMock(wraps=c.rule_repo.get_profile_id)
    c.rule_repo.get_rule = AsyncMock(wraps=c.rule_repo.get_rule)
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
        return await c.rule_service.resolver.resolve(actor=c.actor)

    async def read_service(session=Depends(get_construction_scoped_session)):
        assert session is c.session
        return c.rule_service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = scoped_session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_tax_rule_read_service] = read_service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, *, profile=None, rule=None, query=""):
    url = PATH + str(c.profile if profile is None else profile) + "/rules/"
    url += str(c.rule.id if rule is None else rule) + query
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.get(url, headers={"X-Request-ID": "tax-rule-read-http"})


@pytest.mark.asyncio
async def test_read_is_typed_private_exact_and_has_no_write(tax_rule_http):
    c = tax_rule_http
    before, commits = snapshot(c), c.session.commits
    c.sync.rollback()
    response = await call(c)
    assert response.status_code == 200, response.text
    body, data = response.json(), response.json()["data"]
    assert body["meta"] == {} and body["request_id"] == "tax-rule-read-http"
    assert set(data) == set(routes.TaxRuleReadData.model_fields)
    assert data["id"] == str(c.rule.id) and data["tenant_id"] == str(c.tenant)
    assert data["tax_profile_id"] == str(c.profile) and data["created_by"] == str(c.user)
    assert data["category"] == "WORK" and data["row_version"] == 1
    assert data["rate_percent"] == "20.00" and type(data["rate_percent"]) is str
    assert data["created_at"].endswith("Z") and data["updated_at"].endswith("Z")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert snapshot(c) == before and c.session.commits == commits
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")


@pytest.mark.asyncio
async def test_long_decimal_rate_reaches_json_without_rounding(tax_rule_http):
    c = tax_rule_http
    rate = Decimal("99.123456789012345678901234567890123456789")
    c.rule = (await create(c, category="MATERIAL", rate_percent=rate)).rule
    response = await call(c)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["rate_percent"] == str(rate)


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_gates_precede_repository_read(tax_rule_http, gate):
    c = tax_rule_http
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
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    c.rule_repo.get_profile_id.assert_not_awaited()
    c.rule_repo.get_rule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_allowed_tenant_roles_use_existing_tax_permission(tax_rule_http, role):
    tax_rule_http.grant.role = role
    response = await call(tax_rule_http)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "observer", "manager", "executor"])
async def test_fresh_membership_permission_and_tenant_scope(tax_rule_http, failure):
    c = tax_rule_http
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    elif failure == "expired":
        c.grant.expires_at = c.now
    else:
        c.grant.role = {"observer": "OBSERVER", "manager": "PROJECT_MANAGER", "executor": "EXECUTOR"}[failure]
        c.grant.scope_type, c.grant.scope_id = "project", uuid4()
    response = await call(c)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] in {"forbidden", "permission_required"}
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_missing_and_wrong_profile_have_same_safe_not_found(tax_rule_http):
    c = tax_rule_http
    responses = [
        await call(c, profile=c.foreign_profile),
        await call(c, profile=uuid4()),
        await call(c, profile=c.other_profile),
        await call(c, rule=uuid4()),
    ]
    assert all(response.status_code == 404 for response in responses)
    assert all(response.json() == responses[0].json() for response in responses)
    assert str(c.foreign) not in responses[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["?tenant_id=foreign", "?user_id=foreign", "?scope=tenant", "?limit=10"])
async def test_caller_selectors_are_rejected(tax_rule_http, query):
    c = tax_rule_http
    response = await call(c, query=query)
    assert response.status_code == 422, response.text
    c.rule_repo.get_profile_id.assert_not_awaited()
    c.rule_repo.get_rule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["profile", "rule"])
async def test_invalid_uuid_is_validation_error(tax_rule_http, part):
    response = await call(tax_rule_http, **{part: "not-a-uuid"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "tenant", "profile", "id", "version", "time", "float", "nan", "category"])
async def test_invalid_projection_and_private_errors_are_sanitized(tax_rule_http, failure):
    c = tax_rule_http
    if failure == "exception":
        c.rule_service.get_rule = AsyncMock(side_effect=RuntimeError("PRIVATE_RULE_DATABASE_DETAILS"))
    else:
        changes = {
            "tenant": {"tenant_id": c.foreign}, "profile": {"tax_profile_id": c.other_profile},
            "id": {"id": uuid4()}, "version": {"row_version": True},
            "time": {"updated_at": c.now.replace(tzinfo=None)},
            "float": {"rate_percent": 20.0}, "nan": {"rate_percent": Decimal("NaN")},
            "category": {"category": "UNKNOWN"},
        }[failure]
        c.rule_service.get_rule = AsyncMock(return_value=replace(c.rule, **changes))
    response = await call(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert "PRIVATE_RULE_DATABASE_DETAILS" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["profile", "rule"])
async def test_authority_is_rechecked_after_each_repository_query(tax_rule_http, phase):
    c = tax_rule_http
    name = "get_profile_id" if phase == "profile" else "get_rule"
    method = getattr(c.rule_repo, name)
    async def read(**kwargs):
        result = await method(**kwargs)
        c.membership = False
        return result
    setattr(c.rule_repo, name, read)
    response = await call(c)
    assert response.status_code == 403, response.text
    assert "rate_percent" not in response.text
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_openapi_uses_shared_security_exact_version_and_decimal_string(tax_rule_http):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tax_rule_http.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    operation = document["paths"][PATH + "{profile_id}/rules/{rule_id}"]["get"]
    assert operation["security"] and "requestBody" not in operation
    assert {401, 403, 404, 422, 429, 500} <= {int(key) for key in operation["responses"]}
    schemas = document["components"]["schemas"]
    data = next(schema for schema in schemas.values() if schema.get("title") == "TaxRuleReadData")
    version = data["properties"]["row_version"]
    assert type(version["maximum"]) is int and version["maximum"] == 2**63 - 1
    assert type(version["minimum"]) is int and version["minimum"] == 1
    assert version["format"] == "int64"
    assert data["properties"]["rate_percent"]["type"] == "string"

# con016_canonical_categories_unique_v1
