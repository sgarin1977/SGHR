"""Local CON-019 Telegram forms and exact existing HTTP contract."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_calculation_settings as handler
from handlers.construction import construction_tax_profiles as tax
from services.construction.construction_calculation_settings_bot_api import CalculationSettingsBotApi
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from test_construction_bot_tax_profiles import screen
from ui.construction_calculation_settings import LABELS


@pytest.mark.parametrize("field,value,expected", [
    ("money_input_precision", "0", 0), ("quantity_precision", "6", 6),
    ("supplier_stale_price_days", "3650", 3650),
    ("minimum_job", "0", "0"),
    ("default_local_repair_percent", "20,125", "20.125"),
    ("minimum_job_currency", "eur", "EUR"),
])
def test_valid_input(field, value, expected):
    assert handler.parse_value(field, value) == expected


@pytest.mark.parametrize("field,value", [
    ("money_input_precision", "7"), ("quantity_precision", "1.5"),
    ("supplier_stale_price_days", "3651"),
    ("minimum_job", "-1"), ("default_local_repair_percent", "100.1"),
    ("default_technological_waste_percent", "NaN"),
])
def test_invalid_input(field, value):
    with pytest.raises(ValueError):
        handler.parse_value(field, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_nullable_get_does_not_require_platform_profile_management(missing):
    from uuid import uuid4
    tenant, user = uuid4(), uuid4()
    item = {
        "tenant_id": str(tenant), "money_input_precision": 4,
        "quantity_precision": 4, "supplier_stale_price_days": 0,
        "minimum_job_amount": None, "minimum_job_currency": None,
        "default_technological_waste_percent": None,
        "default_local_repair_percent": None,
        "row_version": 7, "created_by": str(user),
        "created_at": "2026-11-11T12:00:00Z", "updated_at": "2026-11-11T12:00:00Z",
    }
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": None if missing else item, "meta": {}, "request_id": "local",
        })
    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        api.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant), "can_manage_settings": True,
            "can_manage_profile": False,
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {"language": "uk", "name": "Org"})),
            api=NS(construction=api),
        )
        result = await CalculationSettingsBotApi(entry).read(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
        )
    assert (result["item"] is None) is missing
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.path == "/api/v1/construction/settings/calculation"


@pytest.fixture
def calculation_screen(screen, monkeypatch):
    c = screen
    c.settings = {
        "tenant_id": str(c.tenant), "money_input_precision": 4,
        "quantity_precision": 4, "supplier_stale_price_days": 0,
        "minimum_job_amount": "100", "minimum_job_currency": "EUR",
        "default_technological_waste_percent": None,
        "default_local_repair_percent": None,
        "row_version": 7, "created_by": str(c.user),
        "created_at": c.profile["created_at"], "updated_at": c.profile["updated_at"],
    }
    c.read_result = {**c.common, "item": None}
    async def write(**kwargs):
        operation = kwargs["request"]
        item = deepcopy(c.settings)
        if operation["create"]:
            item.update({
                "minimum_job_amount": None, "minimum_job_currency": None,
                "default_technological_waste_percent": None,
                "default_local_repair_percent": None,
            })
        item.update({k: v for k, v in operation["body"].items() if k != "expected_row_version"})
        item["row_version"] = (
            1 if operation["create"] else operation["body"]["expected_row_version"] + 1
        )
        return {**c.common, "item": {**item, "changed": True}}
    c.calculation_api = NS(
        context=c.api.context, read=AsyncMock(return_value=c.read_result),
        currency_codes=AsyncMock(return_value=("EUR", "UAH")),
        write=AsyncMock(side_effect=write),
    )
    c.successful_calculation_write = write
    monkeypatch.setattr(handler, "CalculationSettingsBotApi", lambda service: c.calculation_api)
    monkeypatch.setattr(handler, "construction_bot_api_entry", tax.construction_bot_api_entry)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def open_screen(c):
    await handler.open_settings(
        c.callback, c.state, handler.shell.snapshot(c.data), c.parent, object(),
    )


async def click(c, action, index=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], action, index)
    await handler.calculation_callback(c.callback, c.state)


async def enter(c, value):
    c.message.text = value
    await handler.calculation_message(c.message, c.state)


async def ready_create(c):
    await open_screen(c)
    await click(c, "new")
    assert c.data[handler.VIEW]["fields"] == {}
    await click(c, "number", 3)
    assert c.data[handler.VIEW]["fields"] == {"money_input_precision": 3}
    await click(c, "number", 4)
    await enter(c, "0")
    assert c.data[handler.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_creation_requires_explicit_values_and_confirmation(calculation_screen):
    c = calculation_screen
    await ready_create(c)
    c.calculation_api.write.assert_not_awaited()
    await click(c, "save")
    assert c.calculation_api.write.await_args.kwargs["request"]["body"] == {
        "money_input_precision": 3, "quantity_precision": 4,
        "supplier_stale_price_days": 0,
    }


@pytest.mark.asyncio
async def test_minimum_clear_sends_both_nulls_and_version(calculation_screen):
    c = calculation_screen
    c.read_result["item"] = c.settings
    await open_screen(c)
    await click(c, "field", 3)
    await click(c, "clear")
    c.calculation_api.write.assert_not_awaited()
    await click(c, "save")
    assert c.calculation_api.write.await_args.kwargs["request"]["body"] == {
        "minimum_job_amount": None, "minimum_job_currency": None,
        "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_minimum_pair_is_not_written_after_amount_only(calculation_screen):
    c = calculation_screen
    c.read_result["item"] = c.settings
    await open_screen(c)
    await click(c, "field", 3)
    await enter(c, "250,50")
    assert c.data[handler.VIEW]["field"] == "minimum_job_currency"
    c.calculation_api.write.assert_not_awaited()
    await enter(c, "eur")
    assert c.data[handler.VIEW]["phase"] == "review"
    await click(c, "save")
    assert c.calculation_api.write.await_args.kwargs["request"]["body"] == {
        "minimum_job_amount": "250.50", "minimum_job_currency": "EUR",
        "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_optional_percent_can_be_cleared_without_zero_default(calculation_screen):
    c = calculation_screen
    c.read_result["item"] = c.settings
    await open_screen(c)
    await click(c, "field", 4)
    await click(c, "clear")
    await click(c, "save")
    assert c.calculation_api.write.await_args.kwargs["request"]["body"] == {
        "default_technological_waste_percent": None, "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_retry_preserves_request_and_key(calculation_screen):
    c = calculation_screen
    await ready_create(c)
    attempts = 0
    async def uncertain(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConstructionApiClientError(status_code=504, code="api_timeout")
        return await c.successful_calculation_write(**kwargs)
    c.calculation_api.write.side_effect = uncertain
    await click(c, "save")
    first = deepcopy(c.calculation_api.write.await_args.kwargs["request"])
    await click(c, "refresh")
    await click(c, "retry")
    assert c.calculation_api.write.await_args.kwargs["request"] == first


@pytest.mark.asyncio
async def test_cancel_discards_unsubmitted_minimum(calculation_screen):
    c = calculation_screen
    c.read_result["item"] = c.settings
    await open_screen(c)
    await click(c, "field", 3)
    await enter(c, "250")
    await click(c, "refresh")
    c.calculation_api.write.assert_not_awaited()
    assert c.data[handler.VIEW]["phase"] == "menu"


def test_labels_and_callback_size():
    assert all(len(values) == 7 for values in LABELS.values())
    assert len(handler.cb({"nonce": "a" * 32}, "number", 6).encode("utf-8")) <= 64
