"""CON-013 exact catalog and actual ASGI gates; local stores/SQLite only."""
from contextlib import nullcontext
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service
from api.routes import construction_unit_catalog as routes
from services.construction.construction_unit_catalog import (
    ConstructionUnitCatalogService, ConstructionUnitCatalogOperationError,
)
from services.construction.construction_units import (
    UnitCode, UnitDimension, ConstructionUnitValidationError,
    list_construction_units, require_construction_unit,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_language_http import http_language, snapshot

PATH = "/api/v1/construction/settings/units"


@pytest.fixture
def units_http(http_language):
    c = http_language
    c.units = ConstructionUnitCatalogService(session=c.db, identity=c.identity)
    c.app.dependency_overrides[routes.get_unit_catalog_service] = lambda: c.units
    return c


async def call(c, suffix="", method="GET"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(method, PATH + suffix)


def test_catalog_covers_section_10_1_with_immutable_canonical_metadata():
    units = list_construction_units()
    required = {
        "MM", "CM", "M", "M2", "M3", "ML", "L", "G", "KG",
        "PCS", "PACKAGE", "ROLL", "BAG", "SET", "HOUR", "DAY",
    }
    assert isinstance(units, tuple)
    assert {unit.code.value for unit in units} == required
    assert len(units) == len(required)
    assert len({unit.label_key for unit in units}) == len(required)
    assert require_construction_unit("MM").dimension == UnitDimension.LENGTH
    assert require_construction_unit("M2").dimension == UnitDimension.AREA
    assert require_construction_unit("M3").dimension == UnitDimension.VOLUME
    assert require_construction_unit("L").dimension == UnitDimension.LIQUID_VOLUME
    assert require_construction_unit("KG").dimension == UnitDimension.MASS
    assert require_construction_unit("DAY").dimension == UnitDimension.TIME
    assert require_construction_unit(UnitCode.M).symbol == "m"
    with pytest.raises(FrozenInstanceError):
        units[0].symbol = "changed"


@pytest.mark.parametrize("code", [None, True, 1, [], {}, "", "m", " M", "M ", "м", "M²", "UNKNOWN"])
def test_unknown_or_display_labels_are_not_silently_normalized_as_codes(code):
    with pytest.raises(ConstructionUnitValidationError):
        require_construction_unit(code)


@pytest.mark.asyncio
async def test_http_returns_catalog_without_business_writes_or_manage_permission(units_http):
    c = units_http
    c.role = "OBSERVER"
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["tenant_id"] == str(c.tenant)
    assert body["data"]["catalog_version"] == 1
    units = body["data"]["units"]
    assert {unit["code"] for unit in units} == {code.value for code in UnitCode}
    assert len(units) == len(UnitCode)
    assert all(set(unit) == {"code", "dimension", "symbol", "label_key", "label", "label_status"} for unit in units)
    assert body["data"]["language_code"] == "it"
    assert body["data"]["label_source_version"] == 1
    assert all(unit["label"] and unit["label_status"] == "provided" for unit in units)
    assert body["meta"] == {} and body["request_id"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert snapshot(c) == before and c.commits == 0
    assert c.identity.require_actor.await_count == 2
    c.resolver.resolve.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["anonymous", "inactive", "membership", "suite", "core", "late_identity", "wrong_identity"])
async def test_platform_membership_and_module_gates_are_preserved(units_http, failure):
    c = units_http
    if failure == "anonymous":
        c.app.dependency_overrides.pop(get_current_actor)
    elif failure == "inactive":
        c.actor = replace(c.actor, status="blocked")
    elif failure == "membership":
        c.member = False
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "core":
        c.core.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "late_identity":
        c.deny_at = 2
    else:
        c.identity.require_actor.side_effect = None
        c.identity.require_actor.return_value = replace(c.actor, user_id=uuid4())
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    assert "data" not in response.json()
    assert snapshot(c) == before and c.commits == 0
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["?tenant_id=other", "?language=ru", "?unit=M", "?user_id=other"])
async def test_request_selectors_cannot_replace_trusted_context(units_http, suffix):
    c = units_http
    before = snapshot(c)
    response = await call(c, suffix)
    assert response.status_code == 422
    assert snapshot(c) == before and c.commits == 0
    c.identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_rate_limit_runs_before_catalog_read(units_http):
    c = units_http
    rate = c.app.dependency_overrides[get_api_request_rate_limit_service]()
    for _ in range(120):
        await rate.ensure_authenticated_user_allowed(tenant_id=c.tenant, user_id=c.user)
    response = await call(c)
    assert response.status_code == 429
    c.identity.require_actor.assert_not_awaited()
    assert c.commits == 0


@pytest.mark.asyncio
async def test_unexpected_identity_failure_is_sanitized_without_writes(units_http):
    c = units_http
    c.identity.require_actor.side_effect = RuntimeError("private identity details")
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private identity details" not in response.text
    assert snapshot(c) == before and c.commits == 0


@pytest.mark.asyncio
async def test_dirty_session_is_rejected_before_identity_read(units_http):
    session = NS(
        new={object()}, dirty=set(), deleted=set(),
        no_autoflush=nullcontext(), expire_all=Mock(),
    )
    identity = NS(session=session, require_actor=AsyncMock())
    service = ConstructionUnitCatalogService(session=session, identity=identity)
    with pytest.raises(ConstructionUnitCatalogOperationError):
        await service.read_for_actor(actor=units_http.actor)
    identity.require_actor.assert_not_awaited()


def test_factory_keeps_shared_identity_session_and_openapi_is_read_only(units_http):
    c = units_http
    service = routes.get_unit_catalog_service(c.db)
    assert service.session is c.db and service.identity.session is c.db
    document = c.app.openapi()
    assert set(document["paths"][PATH]) == {"get"}
    operation = document["paths"][PATH]["get"]
    assert {"401", "403", "422", "429", "500"} <= set(operation["responses"])
    assert not operation.get("parameters") and "requestBody" not in operation
    assert "UnitCode" in document["components"]["schemas"]
    with pytest.raises(ValueError):
        ConstructionUnitCatalogService(session=c.db, identity=NS(session=object()))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
async def test_catalog_has_no_mutation_operation(units_http, method):
    c = units_http
    before = snapshot(c)
    response = await call(c, method=method)
    assert response.status_code == 405
    assert snapshot(c) == before and c.commits == 0
