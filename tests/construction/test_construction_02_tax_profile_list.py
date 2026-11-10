"""Registered profile collection with real local SQL; no native or deployed claim."""
import base64
import json
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import delete, insert, select

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_suite_access_service, get_tenant_module_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_tax_profile_list as routes
from api.routes.construction import get_construction_scoped_session, get_construction_access_context
from api.construction_tax_profile_cursors import encode_tax_profile_cursor, decode_tax_profile_cursor
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore, ApiRequestRateLimitExceededError,
)
from services.module_access import SuiteDisabledError, ModuleDisabledError
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileCursor as Cursor, ConstructionTaxProfilePage as Page,
    ConstructionTaxProfileValidationError as ValidationError,
)
from test_construction_02_tax_profiles import case

PATH = "/api/v1/construction/settings/tax-profiles"


@pytest.fixture
def profiles_http(case):
    c = case
    c.sync.rollback()
    rows = [
        dict(id=UUID(int=number), tenant_id=c.tenant, name="Profile " + str(number),
             client_price_tax_mode="TAX_EXCLUSIVE", material_cost_basis="NET",
             row_version=1, created_by=c.user, created_at=c.now, updated_at=c.now)
        for number in range(1, 103)
    ]
    rows.append(dict(rows[0], id=UUID(int=999), tenant_id=c.foreign, name="PRIVATE_FOREIGN_PROFILE"))
    c.sync.execute(insert(c.table), rows)
    c.sync.commit()
    c.profile = c.service._state(
        dict(c.sync.execute(select(c.table).where(c.table.c.id == UUID(int=1))).mappings().one()),
        tenant_id=c.tenant, profile_id=UUID(int=1))
    c.sync.rollback()
    c.query_profiles = c.repository.list_profiles
    c.repository.list_profiles = AsyncMock(wraps=c.query_profiles)
    c.service.list_profiles = AsyncMock(wraps=c.service.list_profiles)
    c.app = create_app()
    c.suite, c.core = NS(require_enabled=AsyncMock()), NS(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(wraps=c.rates.ensure_authenticated_user_allowed)

    async def session():
        if c.sync.in_transaction():
            c.sync.rollback()
        c.sync.begin()
        try:
            yield c.session
        finally:
            if c.sync.in_transaction():
                await c.session.rollback()

    async def context(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return await c.service.resolver.resolve(actor=c.actor)

    async def service(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_tax_profile_list_service] = service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


def snapshot(c):
    return tuple(tuple(dict(row) for row in c.sync.execute(
        select(table).order_by(table.c.id)).mappings()) for table in (c.table, c.audits))


async def call(c, *, params=None, suffix=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.get(PATH + suffix, params=params, headers={"X-Request-ID": "tax-profile-list"})


@pytest.mark.asyncio
async def test_collection_is_bounded_scoped_and_read_only(profiles_http):
    c = profiles_http
    before, commits = snapshot(c), c.session.commits
    response = await call(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_id"] == "tax-profile-list"
    assert [item["id"] for item in body["data"]] == [str(UUID(int=i)) for i in range(1, 21)]
    assert all(item["tenant_id"] == str(c.tenant) and item["client_price_tax_mode"] == "TAX_EXCLUSIVE"
               and item["material_cost_basis"] == "NET" for item in body["data"])
    assert "PRIVATE_FOREIGN_PROFILE" not in response.text
    cursor = decode_tax_profile_cursor(
        body["meta"]["pagination"]["next_cursor"], tenant_id=c.tenant, user_id=c.user)
    assert cursor.after_id == UUID(int=20)
    c.repository.list_profiles.assert_awaited_once_with(tenant_id=c.tenant, limit=21, after_id=None)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert snapshot(c) == before and c.session.commits == commits
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")


@pytest.mark.asyncio
async def test_maximum_page_and_continuation_have_no_duplicates(profiles_http):
    c = profiles_http
    first_response = await call(c, params={"limit": 100})
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    second_response = await call(c, params={"limit": 100, "cursor": first["meta"]["pagination"]["next_cursor"]})
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert [item["id"] for item in first["data"] + second["data"]] == [
        str(UUID(int=i)) for i in range(1, 103)]
    assert second["meta"]["pagination"]["next_cursor"] is None


@pytest.mark.asyncio
async def test_empty_own_collection_creates_no_default_profiles_or_rates(profiles_http):
    c = profiles_http
    c.sync.execute(delete(c.table).where(c.table.c.tenant_id == c.tenant))
    c.sync.commit()
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []
    assert response.json()["meta"]["pagination"]["next_cursor"] is None
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_existing_tenant_tax_read_roles_are_allowed(profiles_http, role):
    c = profiles_http
    c.grant.role = role
    response = await call(c)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_transport_gates_precede_collection_read(profiles_http, gate):
    c = profiles_http
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
    c.repository.list_profiles.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "observer", "estimator"])
async def test_fresh_membership_and_tenant_scope_remain_required(profiles_http, failure):
    c = profiles_http
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    elif failure == "expired":
        c.grant.expires_at = c.now
    else:
        c.grant.role = "OBSERVER" if failure == "observer" else "ESTIMATOR"
        c.grant.scope_type, c.grant.scope_id = "project", uuid4()
    response = await call(c)
    assert response.status_code == 403, response.text
    c.repository.list_profiles.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_authority_is_refreshed_after_the_real_query(profiles_http):
    c = profiles_http
    original = c.query_profiles
    async def revoked(**values):
        result = await original(**values)
        c.membership = False
        return result
    c.repository.list_profiles.side_effect = revoked
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [
    "?tenant_id=foreign", "?user_id=foreign", "?profile_id=foreign",
    "?limit=0", "?limit=101", "?limit=20&limit=20", "?cursor=",
    "?cursor=invalid!", "?cursor=x&cursor=y",
])
async def test_invalid_query_and_selectors_are_rejected(profiles_http, suffix):
    c = profiles_http
    response = await call(c, suffix=suffix)
    assert response.status_code == 422, response.text
    c.service.list_profiles.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tenant", "user", "domain"])
async def test_cursor_cannot_be_reused_for_another_context_or_resource(profiles_http, kind):
    c = profiles_http
    tenant, user = (c.foreign, c.user) if kind == "tenant" else (
        (c.tenant, uuid4()) if kind == "user" else (c.tenant, c.user))
    value = encode_tax_profile_cursor(Cursor(tenant, user, UUID(int=20)), tenant_id=tenant, user_id=user)
    if kind == "domain":
        raw = base64.urlsafe_b64decode(value + "=" * ((-len(value)) % 4))
        record = json.loads(raw)
        record["kind"] = "construction.tax_rules.list"
        value = base64.urlsafe_b64encode(json.dumps(
            record, sort_keys=True, separators=(",", ":")).encode("ascii")).decode("ascii").rstrip("=")
    response = await call(c, params={"cursor": value})
    assert response.status_code == 422, response.text
    c.service.list_profiles.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "order", "cursor", "private"])
async def test_invalid_response_pages_are_sanitized(profiles_http, failure):
    c = profiles_http
    if failure == "private":
        c.service.list_profiles.side_effect = RuntimeError("PRIVATE_DATABASE_DETAILS")
    else:
        profile = replace(c.profile, tenant_id=c.foreign) if failure == "tenant" else c.profile
        items = (profile, profile) if failure == "order" else (profile,)
        cursor = Cursor(c.foreign, c.user, profile.id) if failure == "cursor" else None
        c.service.list_profiles.return_value = Page(items, cursor)
    response = await call(c, params={"limit": 1} if failure == "cursor" else None)
    assert response.status_code == 500, response.text
    assert "PRIVATE_DATABASE_DETAILS" not in response.text and str(c.foreign) not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 101, True, "20", 1.0])
async def test_internal_limits_are_strict_before_repository_read(profiles_http, limit):
    c = profiles_http
    with pytest.raises(ValidationError):
        await c.service.list_profiles(actor=c.actor, limit=limit)
    c.repository.list_profiles.assert_not_awaited()


def test_openapi_keeps_existing_profile_operations_and_exact_version(profiles_http):
    document = profiles_http.app.openapi()
    assert "get" in document["paths"][PATH] and "post" in document["paths"][PATH]
    detail = PATH + "/{profile_id}"
    assert "get" in document["paths"][detail] and "patch" in document["paths"][detail]
    operation = document["paths"][PATH]["get"]
    assert operation.get("security")
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert "Idempotency-Key" not in parameters
    bound = document["components"]["schemas"]["TaxProfileReadData"]["properties"]["row_version"]["maximum"]
    assert type(bound) is int and bound == 2**63 - 1
