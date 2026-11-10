"""CON-012 offline catalog and local SQL HTTP workflow; no native acceptance."""
from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from test_construction_02_language_http import (
    http_language, request as language_request, snapshot,
)
from test_platform_organization_bootstrap import (
    case, run as run_bootstrap, counts as bootstrap_counts,
)
from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service
from api.routes import construction_currency_settings as routes
from database.repositories.platform_organization_currency import (
    PlatformOrganizationCurrencyRepository,
)
from services.construction.construction_currency_settings import (
    ConstructionCurrencySettingsService,
)
from services.module_access import ModuleDisabledError
from services.platform_currency_catalog import require_platform_currency
from services.platform_organization_bootstrap import (
    OrganizationBootstrapValidationError,
)

PATH = "/api/v1/construction/settings/currency"
MAX = 2**63 - 1


@pytest.fixture
def currency_http(http_language):
    c = http_language
    c.currency = ConstructionCurrencySettingsService(
        session=c.db,
        repository=PlatformOrganizationCurrencyRepository(c.db),
        identity=c.identity,
        authorizer=c.authorizer,
        events=c.events,
        now_provider=lambda: c.base,
    )
    c.app.dependency_overrides[
        routes.get_currency_settings_service
    ] = lambda: c.currency
    return c


async def call(c, method="GET", payload=None, suffix=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=c.app, raise_app_exceptions=False,
        ),
        base_url="https://testserver",
    ) as client:
        if method == "GET":
            return await client.get(PATH + suffix)
        return await client.patch(
            PATH + suffix,
            json=(
                {"default_currency": "USD", "expected_row_version": 1}
                if payload is None else payload
            ),
        )


@pytest.mark.parametrize("code", ["EUR", "USD", "UAH", "JPY", "KWD"])
def test_shared_catalog_uses_existing_platform_currency_codes(code):
    result = require_platform_currency(code)
    assert result.code == code and result.name


@pytest.mark.asyncio
async def test_read_is_nonsecret_and_does_not_need_manage_permission(currency_http):
    c = currency_http
    c.role = "OBSERVER"
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["organization_default_currency"] == "EUR"
    assert data["row_version"] == 1
    assert data["tenant_id"] == str(c.tenant)
    codes = [value["code"] for value in data["currencies"]]
    assert {"EUR", "USD", "UAH", "JPY", "KWD"} <= set(codes)
    assert len(codes) == len(set(codes))
    assert response.headers["Cache-Control"] == "private, no-store"
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_currency_change_is_scoped_versioned_and_audited(
    currency_http, role,
):
    c = currency_http
    c.role = role
    users = c.sync.execute(select(c.users)).all()
    foreign = c.sync.execute(
        select(c.tenants).where(c.tenants.c.id == c.foreign)
    ).all()

    response = await call(c, "PATCH")
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["changed"] is True and result["row_version"] == 2
    assert result["organization_default_currency"] == "USD"
    assert c.sync.execute(select(c.users)).all() == users
    assert c.sync.execute(
        select(c.tenants).where(c.tenants.c.id == c.foreign)
    ).all() == foreign

    own = c.sync.execute(
        select(c.tenants).where(c.tenants.c.id == c.tenant)
    ).mappings().one()
    assert own["default_currency"] == "USD"
    assert own["default_language"] == "pt"

    events = c.sync.execute(select(c.audit)).mappings().all()
    assert len(events) == 1
    assert events[0]["trace_id"] == response.json()["request_id"]
    payload = events[0]["payload"]
    assert payload["field"] == "default_currency"
    assert payload["old_value"] == "EUR"
    assert payload["new_value"] == "USD"
    assert payload["previous_row_version"] == 1
    assert payload["row_version"] == 2
    assert (
        c.events.create_event.await_args.kwargs["event_type"]
        == "construction_organization_currency_updated"
    )

    after = await call(c)
    assert after.status_code == 200
    assert after.json()["data"]["row_version"] == 2


@pytest.mark.asyncio
async def test_language_and_currency_share_one_organization_counter(currency_http):
    c = currency_http
    assert (await call(c, "PATCH")).status_code == 200
    committed = snapshot(c)

    stale = await language_request(c, payload={
        "default_language": "es", "expected_row_version": 1,
    })
    assert stale.status_code == 409
    assert snapshot(c) == committed

    fresh = await language_request(c, "GET")
    assert fresh.json()["data"]["row_version"] == 2
    response = await language_request(c, payload={
        "default_language": "es", "expected_row_version": 2,
    })
    assert response.status_code == 200
    assert response.json()["data"]["row_version"] == 3

    committed = snapshot(c)
    stale = await call(c, "PATCH", {
        "default_currency": "JPY", "expected_row_version": 2,
    })
    assert stale.status_code == 409
    assert snapshot(c) == committed


@pytest.mark.asyncio
async def test_current_noop_and_stale_noop(currency_http):
    c = currency_http
    before = snapshot(c)
    response = await call(c, "PATCH", {
        "default_currency": "EUR", "expected_row_version": 1,
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["changed"] is False
    assert snapshot(c) == before

    c.sync.execute(
        c.tenants.update().where(c.tenants.c.id == c.tenant)
        .values(row_version=2)
    )
    c.sync.commit()
    before = snapshot(c)
    response = await call(c, "PATCH", {
        "default_currency": "EUR", "expected_row_version": 1,
    })
    assert response.status_code == 409
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"default_currency": "ZZZ"},
    {"default_currency": "usd"},
    {"default_currency": " US"},
    {"default_currency": "ЕUR"},
    {"default_currency": None},
    {"expected_row_version": 0},
    {"expected_row_version": True},
    {"expected_row_version": "1"},
    {"expected_row_version": MAX + 1},
    {"tenant_id": "foreign"},
    {"user_id": "caller"},
    {"exchange_rate": 1.25},
])
async def test_invalid_request_cannot_write(currency_http, fields):
    c = currency_http
    payload = {"default_currency": "USD", "expected_row_version": 1}
    payload.update(fields)
    before = snapshot(c)
    response = await call(c, "PATCH", payload)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["observer", "project_scope", "membership", "global_claims"],
)
async def test_fresh_scoped_authority_is_required(currency_http, case):
    c = currency_http
    if case in ("observer", "global_claims"):
        c.role = "OBSERVER"
    elif case == "project_scope":
        c.scope = "project"
    else:
        c.member = False

    if case == "global_claims":
        c.actor = replace(
            c.actor,
            roles=("super_admin",),
            permissions=(
                "construction.settings.manage",
                "platform.organization.profile.manage",
            ),
        )

    before = snapshot(c)
    response = await call(c, "PATCH")
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "late_authority"])
async def test_failure_after_lock_or_real_write_rolls_back(currency_http, failure):
    c = currency_http
    c.fail = failure
    if failure == "late_authority":
        c.deny_at = 2

    before = snapshot(c)
    response = await call(c, "PATCH")
    assert response.status_code == (
        403 if failure == "late_authority" else 500
    ), response.text
    assert "private" not in response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_invalid_stored_currency_has_no_fabricated_default_and_can_be_repaired(
    currency_http,
):
    c = currency_http
    c.sync.execute(
        c.tenants.update().where(c.tenants.c.id == c.tenant)
        .values(default_currency="ZZZ", row_version=2)
    )
    c.sync.commit()
    before = snapshot(c)

    response = await call(c)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CURRENCY_CONFIGURATION_REQUIRED"
    assert snapshot(c) == before

    version = (await language_request(c, "GET")).json()["data"]["row_version"]
    response = await call(c, "PATCH", {
        "default_currency": "USD", "expected_row_version": version,
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["row_version"] == 3


@pytest.mark.asyncio
async def test_authentication_module_and_selectors_are_enforced(currency_http):
    c = currency_http
    before = snapshot(c)
    c.app.dependency_overrides.pop(get_current_actor)
    assert (await call(c, "PATCH")).status_code == 401

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.core.require_enabled.side_effect = ModuleDisabledError()
    assert (await call(c, "PATCH")).status_code == 403

    c.core.require_enabled.side_effect = None
    assert (
        await call(c, "PATCH", suffix="?tenant_id=" + str(c.foreign))
    ).status_code == 422
    assert snapshot(c) == before
    c.identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_rate_limit_precedes_mutation(currency_http):
    c = currency_http
    rate = c.app.dependency_overrides[
        get_api_request_rate_limit_service
    ]()
    for _ in range(120):
        await rate.ensure_authenticated_user_allowed(
            tenant_id=c.tenant, user_id=c.user,
        )

    before = snapshot(c)
    response = await call(c, "PATCH")
    assert response.status_code == 429
    assert snapshot(c) == before
    c.identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_openapi_has_exact_versions_and_no_closed_currency_enum(
    currency_http,
):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=currency_http.app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    document = response.json()
    assert set(document["paths"][PATH]) == {"get", "patch"}
    schemas = document["components"]["schemas"]
    for title, name in (
        ("CurrencySettingsReadData", "row_version"),
        ("CurrencySettingsWriteData", "row_version"),
        ("CurrencySettingsUpdate", "expected_row_version"),
    ):
        field = next(
            value for value in schemas.values()
            if value.get("title") == title
        )["properties"][name]
        assert type(field["maximum"]) is int
        assert field["maximum"] == MAX
        assert field["minimum"] == 1
        assert field["format"] == "int64"

    request_model = schemas["CurrencySettingsUpdate"]
    assert set(request_model["required"]) == {
        "default_currency", "expected_row_version",
    }
    assert request_model["additionalProperties"] is False
    assert "enum" not in request_model["properties"]["default_currency"]


@pytest.mark.asyncio
async def test_new_organization_rejects_unknown_iso_currency_before_creating_rows(case):
    before = bootstrap_counts(case)
    with pytest.raises(OrganizationBootstrapValidationError):
        await run_bootstrap(case, currency="ZZZ")
    assert bootstrap_counts(case) == before


@pytest.mark.asyncio
async def test_successful_bootstrap_receipt_replay_does_not_revalidate_currency_catalog(
    case, monkeypatch,
):
    import services.platform_organization_bootstrap as bootstrap

    first = await run_bootstrap(case)
    before = bootstrap_counts(case)
    validator = Mock(
        side_effect=AssertionError("Historical currency must not be revalidated.")
    )
    monkeypatch.setattr(bootstrap, "require_platform_currency", validator)

    assert await run_bootstrap(case) == first
    assert bootstrap_counts(case) == before
    validator.assert_not_called()

# con_012_currency_settings_v1
