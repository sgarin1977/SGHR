"""Deferred local pricing UI checks; no database or external operations."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from handlers.construction import construction_pricing_profiles as pricing
from handlers.construction import construction_tax_profiles as tax
from test_construction_bot_tax_profiles import screen
from ui.construction_pricing_profiles import LABELS


@pytest.mark.parametrize("field,text,expected", [
    ("work_coefficient", "1,125", "1.125"),
    ("work_coefficient", "0.00000001", "0.00000001"),
    ("material_markup_percent", "0", "0"),
    ("material_markup_percent", "100", "100"),
    ("material_markup_percent", "20.5000", "20.5000"),
])
def test_exact_input(field, text, expected):
    assert pricing.parse_value(text, field) == expected


@pytest.mark.parametrize("field,text", [
    ("work_coefficient", "0"), ("work_coefficient", "-1"),
    ("material_markup_percent", "100.001"),
    ("material_markup_percent", "NaN"), ("work_coefficient", 1.5),
])
def test_invalid_values_are_not_defaulted(field, text):
    with pytest.raises(ValueError):
        pricing.parse_value(text, field)


@pytest.fixture
def pricing_screen(screen, monkeypatch):
    c = screen
    c.price = {
        "id": c.profile["id"], "tenant_id": str(c.tenant), "code": "ECONOMY",
        "work_coefficient": "1.125", "material_markup_percent": "0",
        "row_version": 7, "created_by": str(c.user),
        "created_at": c.profile["created_at"], "updated_at": c.profile["updated_at"],
    }
    c.page = {**c.common, "items": []}
    async def write(**kwargs):
        operation = kwargs["request"]
        item = deepcopy(c.price)
        item.update({k: v for k, v in operation["body"].items() if k != "expected_row_version"})
        item["row_version"] = (
            1 if operation["operation"] == "create"
            else operation["body"]["expected_row_version"] + 1
        )
        return {**c.common, "item": {**item, "changed": True}}
    from types import SimpleNamespace as NS
    c.pricing_api = NS(
        context=c.api.context, list_profiles=AsyncMock(return_value=c.page),
        get_profile=AsyncMock(side_effect=lambda **kwargs: {**c.common, "item": deepcopy(c.price)}),
        write_profile=AsyncMock(side_effect=write),
    )
    c.successful_pricing_write = write
    monkeypatch.setattr(pricing, "PricingProfileBotApi", lambda service: c.pricing_api)
    monkeypatch.setattr(pricing, "construction_bot_api_entry", tax.construction_bot_api_entry)
    monkeypatch.setattr(pricing, "edit_or_replace_menu_message", c.sent)
    return c


async def open_screen(c):
    await pricing.open_profiles(
        c.callback, c.state, pricing.shell.snapshot(c.data), c.parent, object(),
    )


async def click(c, action, index=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = pricing.cb(c.data[pricing.VIEW], action, index)
    await pricing.pricing_callback(c.callback, c.state)


async def input_value(c, value):
    c.message.text = value
    await pricing.pricing_message(c.message, c.state)


async def ready(c):
    await open_screen(c)
    await click(c, "level", 0)
    assert c.data[pricing.VIEW]["fields"] == {"code": "ECONOMY"}
    await input_value(c, "1,125")
    assert "material_markup_percent" not in c.data[pricing.VIEW]["fields"]
    await input_value(c, "0")
    assert c.data[pricing.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_create_requires_both_values_and_explicit_save(pricing_screen):
    c = pricing_screen
    await ready(c)
    c.pricing_api.write_profile.assert_not_awaited()
    await click(c, "save")
    assert c.pricing_api.write_profile.await_args.kwargs["request"]["body"] == {
        "code": "ECONOMY", "work_coefficient": "1.125", "material_markup_percent": "0",
    }
    assert c.data[pricing.PENDING] == {}


@pytest.mark.asyncio
async def test_edit_is_sparse_and_keeps_version(pricing_screen):
    c = pricing_screen
    c.page["items"] = [c.price]
    await open_screen(c)
    await click(c, "level", 0)
    await click(c, "field", 0)
    await input_value(c, "1.25")
    await click(c, "save")
    assert c.pricing_api.write_profile.await_args.kwargs["request"]["body"] == {
        "work_coefficient": "1.25", "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_retry_keeps_original_body_and_key(pricing_screen):
    from services.construction.construction_api_client import ConstructionApiClientError
    c = pricing_screen
    await ready(c)
    attempts = 0
    async def uncertain(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConstructionApiClientError(status_code=504, code="api_timeout")
        return await c.successful_pricing_write(**kwargs)
    c.pricing_api.write_profile.side_effect = uncertain
    await click(c, "save")
    first = deepcopy(c.pricing_api.write_profile.await_args.kwargs["request"])
    await click(c, "list")
    await click(c, "retry")
    assert c.pricing_api.write_profile.await_args.kwargs["request"] == first
    assert c.data[pricing.PENDING] == {}


@pytest.mark.asyncio
async def test_cancel_writes_nothing(pricing_screen):
    c = pricing_screen
    await ready(c)
    await click(c, "list")
    c.pricing_api.write_profile.assert_not_awaited()


def test_labels_and_callbacks():
    assert all(len(values) == 7 for values in LABELS.values())
    view = {"nonce": "a" * 32}
    assert len(pricing.cb(view, "level", 2).encode("utf-8")) <= 64
