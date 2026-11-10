"""Bounded profile-rule lists over real local SQL; no native/deployed claim."""
import base64
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import insert

from api.app import create_app
from api.auth import get_current_actor
from api.construction_tax_rule_cursors import decode_tax_rule_cursor, encode_tax_rule_cursor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_tax_rule_list as routes
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_rate_limits import (
    ApiRequestRateLimitService, ApiRequestRateLimitExceededError, InMemoryApiRequestRateLimitStore,
)
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleCursor, ConstructionTaxRulePage, ConstructionTaxRuleValidationError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_tax_profiles import case
from test_construction_02_tax_rules import rule_case, snapshot

PATH = "/api/v1/construction/settings/tax-profiles/"


@pytest.fixture
async def list_http(rule_case):
    c = rule_case
    c.sync.rollback()
    c.empty_profile = uuid4()
    c.sync.execute(insert(c.table).values(
        id=c.empty_profile, tenant_id=c.tenant, name="Empty profile",
        client_price_tax_mode="TAX_EXCLUSIVE", material_cost_basis="NET",
        row_version=1, created_by=c.user, created_at=c.now, updated_at=c.now,
    ))
    values = []
    for number, tenant, profile in [
        *((number, c.tenant, c.profile) for number in range(1, 6)),
        (201, c.tenant, c.other_profile), (301, c.foreign, c.foreign_profile),
    ]:
        values.append(dict(
            id=UUID(int=number), tenant_id=tenant, tax_profile_id=profile,
            category=("WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER")[(number - 1) % 5],
            rate_percent=Decimal("7.125"), row_version=1, created_by=c.user,
            created_at=c.now, updated_at=c.now,
        ))
    c.sync.execute(insert(c.rules), values)
    c.sync.commit()
    c.rule_repo.get_profile_id = AsyncMock(wraps=c.rule_repo.get_profile_id)
    c.rule_repo.list_rules = AsyncMock(wraps=c.rule_repo.list_rules)
    c.app = create_app()
    c.suite, c.core = SimpleNamespace(require_enabled=AsyncMock()), SimpleNamespace(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(wraps=c.rates.ensure_authenticated_user_allowed)

    async def session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return await c.rule_service.resolver.resolve(actor=c.actor)

    async def service(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.rule_service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_tax_rule_list_service] = service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, *, profile=None, params=None, query=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.get(
            PATH + str(c.profile if profile is None else profile) + "/rules" + query,
            params=params, headers={"X-Request-ID": "tax-rule-list-http"},
        )


@pytest.mark.asyncio
async def test_default_page_is_scoped_bounded_exact_and_read_only(list_http):
    c = list_http
    before, commits = snapshot(c), c.session.commits
    c.sync.rollback()
    response = await call(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["data"]] == [
        str(UUID(int=i)) for i in range(1, 6)]
    assert [item["category"] for item in body["data"]] == [
        "WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER"]
    assert all(
        item["tenant_id"] == str(c.tenant)
        and item["tax_profile_id"] == str(c.profile)
        and item["rate_percent"] == "7.125"
        for item in body["data"])
    assert body["meta"]["pagination"]["next_cursor"] is None
    c.rule_repo.list_rules.assert_awaited_once_with(
        tenant_id=c.tenant, profile_id=c.profile, limit=21, after_id=None)
    assert snapshot(c) == before and c.session.commits == commits
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, module_code="construction.core")


@pytest.mark.asyncio
async def test_maximum_page_and_continuation_have_no_duplicates_or_scope_leaks(list_http):
    c = list_http
    response = await call(c, params={"limit": 100})
    assert response.status_code == 200, response.text
    maximum = response.json()
    assert len(maximum["data"]) == 5
    assert maximum["meta"]["pagination"]["next_cursor"] is None

    collected, cursor = [], None
    for page_number in range(3):
        params = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = await call(c, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data"]) == (2 if page_number < 2 else 1)
        assert all(
            item["tenant_id"] == str(c.tenant)
            and item["tax_profile_id"] == str(c.profile)
            for item in body["data"])
        collected.extend(item["id"] for item in body["data"])
        cursor = body["meta"]["pagination"]["next_cursor"]
        if page_number < 2:
            parsed = decode_tax_rule_cursor(
                cursor, tenant_id=c.tenant, user_id=c.user, profile_id=c.profile)
            assert parsed.after_id == UUID(int=2 * (page_number + 1))
        else:
            assert cursor is None
    assert collected == [str(UUID(int=i)) for i in range(1, 6)]
    assert len(set(collected)) == 5


@pytest.mark.asyncio
async def test_empty_profile_is_empty_but_foreign_and_missing_profiles_are_404(list_http):
    c = list_http
    response = await call(c, profile=c.empty_profile)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == [] and response.json()["meta"]["pagination"]["next_cursor"] is None
    foreign, missing = await call(c, profile=c.foreign_profile), await call(c, profile=uuid4())
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "?limit=0", "?limit=101", "?limit=no", "?limit=1&limit=2",
    "?tenant_id=foreign", "?user_id=foreign", "?cursor=", "?cursor=bad!",
    "?cursor=a&cursor=b", "?cursor=" + "a" * 1025,
])
async def test_invalid_or_duplicate_query_does_not_reach_rule_query(list_http, query):
    c = list_http
    response = await call(c, query=query)
    assert response.status_code == 422, response.text
    c.rule_repo.list_rules.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["actor", "tenant", "profile"])
async def test_cursor_is_bound_to_actor_tenant_and_profile(list_http, changed):
    c = list_http
    cursor = ConstructionTaxRuleCursor(c.tenant, c.user, c.profile, UUID(int=20))
    token = encode_tax_rule_cursor(cursor, tenant_id=c.tenant, user_id=c.user, profile_id=c.profile)
    wrong = {
        "actor": dict(user_id=uuid4()),
        "tenant": dict(tenant_id=c.foreign),
        "profile": dict(profile_id=c.other_profile),
    }[changed]
    context = dict(tenant_id=c.tenant, user_id=c.user, profile_id=c.profile) | wrong
    with pytest.raises(ConstructionTaxRuleValidationError):
        decode_tax_rule_cursor(token, **context)
    if changed == "profile":
        response = await call(c, profile=c.other_profile, params={"cursor": token})
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate", "membership", "scope"])
async def test_cursor_or_list_cannot_replace_fresh_authority(list_http, gate):
    c = list_http
    token = encode_tax_rule_cursor(
        ConstructionTaxRuleCursor(c.tenant, c.user, c.profile, UUID(int=20)),
        tenant_id=c.tenant, user_id=c.user, profile_id=c.profile,
    )
    expected = 403
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(status_code=401, code="authentication_required", message="Authentication required.")
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
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "OBSERVER", "project", uuid4()
    response = await call(c, params={"cursor": token})
    assert response.status_code == expected, response.text
    c.rule_repo.list_rules.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["profile", "rules"])
async def test_membership_is_refreshed_after_repository_waits(list_http, phase):
    c = list_http
    name = "get_profile_id" if phase == "profile" else "list_rules"
    method = getattr(c.rule_repo, name)
    async def read(**kwargs):
        result = await method(**kwargs)
        c.membership = False
        return result
    setattr(c.rule_repo, name, read)
    response = await call(c)
    assert response.status_code == 403, response.text
    assert "data" not in response.json()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["foreign_peek", "duplicate", "too_many"])
async def test_invalid_repository_page_is_rejected_without_partial_data(list_http, bad):
    c = list_http
    c.sync.begin()
    rows = await c.rule_repo.list_rules(tenant_id=c.tenant, profile_id=c.profile, limit=2)
    c.sync.rollback()
    if bad == "foreign_peek":
        rows = (rows[0], dict(rows[1]) | {"tenant_id": c.foreign})
    elif bad == "duplicate":
        rows = (rows[0], rows[0])
    else:
        rows = rows + (rows[1],)
    c.rule_repo.list_rules = AsyncMock(return_value=rows)
    response = await call(c, params={"limit": 1})
    assert response.status_code == 500, response.text
    assert "data" not in response.json()


def test_cursor_rejects_duplicate_json_keys():
    raw = b'{"v":1,"v":1}'
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    with pytest.raises(ConstructionTaxRuleValidationError):
        decode_tax_rule_cursor(token, tenant_id=uuid4(), user_id=uuid4(), profile_id=uuid4())


@pytest.mark.asyncio
async def test_openapi_list_has_no_key_or_body_requirement(list_http):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=list_http.app, raise_app_exceptions=False), base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    operation = response.json()["paths"][PATH + "{profile_id}/rules"]["get"]
    assert operation["security"] and "requestBody" not in operation
    assert {item["name"] for item in operation["parameters"]} == {"profile_id", "limit", "cursor"}
    limit = next(item["schema"] for item in operation["parameters"] if item["name"] == "limit")
    assert limit["default"] == 20 and limit["minimum"] == 1 and limit["maximum"] == 100


@pytest.mark.asyncio
async def test_wrong_context_service_continuation_is_internal_error(list_http):
    c = list_http
    c.sync.begin()
    page = await c.rule_service.list_rules(actor=c.actor, profile_id=c.profile, limit=1)
    c.sync.rollback()
    malformed = replace(page, next_cursor=replace(page.next_cursor, tax_profile_id=c.other_profile))
    c.rule_service.list_rules = AsyncMock(return_value=malformed)
    response = await call(c, params={"limit": 1})
    assert response.status_code == 500, response.text
    assert "data" not in response.json()

# con016_canonical_categories_unique_v1
