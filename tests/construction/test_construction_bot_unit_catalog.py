"""Local unit catalog transport and current Telegram settings integration."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr
import pytest

from handlers.construction import construction_stage2_settings as settings
from handlers.construction import construction_unit_catalog as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_unit_catalog_bot_api import (
    ConstructionUnitCatalogBotApi,
)
from test_construction_bot_stage2_settings import language_data, screen, click
from ui.construction_unit_catalog import LABELS


def catalog(tenant):
    return {
        "tenant_id": str(tenant), "catalog_version": 1,
        "language_code": "uk", "label_source_version": 1,
        "units": [
            {
                "code": code, "dimension": "length", "symbol": None,
                "label_key": "construction.unit." + code.lower(),
                "label": "LOCAL_UNIT_" + code, "label_status": "provided",
            }
            for code in (
                "MM", "CM", "M", "M2", "M3", "ML", "L", "G",
                "KG", "PCS", "PACKAGE", "ROLL", "BAG", "SET", "HOUR", "DAY",
            )
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    None, "tenant", "duplicate", "language", "source_version",
    "catalog_version", "label_key", "empty",
])
async def test_transport_validates_catalog_and_only_calls_existing_get(failure):
    tenant, user = uuid4(), uuid4()
    data = catalog(tenant)
    if failure == "tenant":
        data["tenant_id"] = str(uuid4())
    elif failure == "duplicate":
        data["units"].append(deepcopy(data["units"][0]))
    elif failure == "language":
        data["language_code"] = "en"
    elif failure == "source_version":
        data["label_source_version"] = True
    elif failure == "catalog_version":
        data["catalog_version"] = True
    elif failure == "label_key":
        data["units"][0]["label_key"] = "foreign.label"
    elif failure == "empty":
        data["units"] = []

    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": data, "meta": {}, "request_id": "local",
        })

    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        api.company_profile_capabilities = AsyncMock()
        entry = NS(
            _presentation=AsyncMock(return_value=(
                session, {"language": "uk", "name": "Organization"},
            )),
            api=NS(construction=api),
        )
        call = ConstructionUnitCatalogBotApi(entry).read(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
        )
        if failure:
            with pytest.raises(ConstructionApiClientError):
                await call
        else:
            result = await call
            assert result["language"] == "uk"
            assert len(result["item"]["units"]) == 16
        api.company_profile_capabilities.assert_not_awaited()

    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.path == "/api/v1/construction/settings/units"
    assert not calls[0].url.query
    assert "idempotency-key" not in calls[0].headers


@pytest.mark.asyncio
async def test_changed_actor_is_rejected_before_catalog_request():
    tenant, user = uuid4(), uuid4()
    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    api = NS(_request=AsyncMock())
    entry = NS(
        _presentation=AsyncMock(return_value=(session, {"language": "uk"})),
        api=NS(construction=api),
    )
    with pytest.raises(ConstructionApiClientError) as caught:
        await ConstructionUnitCatalogBotApi(entry).read(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=uuid4(),
        )
    assert caught.value.status_code == 403
    api._request.assert_not_awaited()


@pytest.fixture
def units_screen(screen, monkeypatch):
    c = screen
    c.units_result = {
        "item": catalog(c.result["item"]["tenant_id"]),
        "actor_user_id": c.result["actor_user_id"],
        "organization": "Organization", "language": "uk",
    }
    c.units = NS(read=AsyncMock(return_value=c.units_result))
    monkeypatch.setattr(
        handler, "ConstructionUnitCatalogBotApi", lambda service: c.units,
    )
    return c


async def open_units(c):
    await settings.settings_callback(c.callback, c.state)
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert any(":units:" in b.callback_data for row in rows for b in row)
    await click(c, "units")


@pytest.mark.asyncio
async def test_pages_use_fresh_get_and_keep_no_catalog_content_in_state(units_screen):
    c = units_screen
    await open_units(c)
    assert c.data[settings.VIEW]["phase"] == "units"
    assert c.data[settings.VIEW]["language"] == "uk"
    assert "LOCAL_UNIT_MM" in c.sent.await_args.kwargs["text"]
    assert "LOCAL_UNIT_KG" not in c.sent.await_args.kwargs["text"]
    assert "LOCAL_UNIT_" not in repr(c.data)
    await click(c, "up", 1)
    assert "LOCAL_UNIT_KG" in c.sent.await_args.kwargs["text"]
    assert "LOCAL_UNIT_MM" not in c.sent.await_args.kwargs["text"]
    assert c.units.read.await_count == 2
    c.protocol.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_back_returns_to_settings_without_mutation(units_screen):
    c = units_screen
    await open_units(c)
    await click(c, "refresh")
    assert c.data[settings.VIEW]["phase"] == "menu"
    assert c.protocol.read.await_count == 2
    c.protocol.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_catalog_callback_is_invalid_after_return(units_screen):
    c = units_screen
    await open_units(c)
    old = settings.cb(c.data[settings.VIEW], "up", 1)
    await click(c, "refresh")
    c.callback.data = old
    await settings.settings_callback(c.callback, c.state)
    assert c.units.read.await_count == 1
    c.protocol.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_catalog_cannot_restore_another_tenant(units_screen):
    c = units_screen
    await open_units(c)
    sent = c.sent.await_count
    async def late(**kwargs):
        c.data[settings.CONTEXT]["tenant_id"] = str(uuid4())
        return c.units_result
    c.units.read.side_effect = late
    await click(c, "up", 1)
    assert c.sent.await_count == sent


@pytest.mark.asyncio
async def test_denial_has_no_old_catalog_content_or_write(units_screen):
    c = units_screen
    await open_units(c)
    c.units.read.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "up", 1)
    assert c.data[settings.VIEW]["phase"] == "blocked"
    assert "LOCAL_UNIT_" not in c.sent.await_args.kwargs["text"]
    c.protocol.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_page_cannot_turn_into_setting_write(units_screen):
    c = units_screen
    await open_units(c)
    await click(c, "up", 99)
    assert c.data[settings.VIEW]["phase"] == "blocked"
    c.protocol.write.assert_not_awaited()


def test_navigation_has_all_languages_and_bounded_callbacks():
    assert all(len(values) == 7 for values in LABELS.values())
    view = {"nonce": "a" * 32}
    for action, argument in (("units", None), ("up", 999999)):
        assert len(settings.cb(view, action, argument).encode("utf-8")) <= 64
