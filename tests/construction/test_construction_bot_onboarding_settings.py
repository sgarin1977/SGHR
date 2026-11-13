"""Wizard navigation uses existing screens and their own resource versions."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_construction_bot_onboarding import screen, wizard, progress, open_guide
from test_construction_bot_stage2_settings import language_data
from handlers.construction import construction_onboarding as guide
from handlers.construction import construction_stage2_settings as shell
from handlers.construction import construction_onboarding_settings as handler
from handlers.construction import construction_region_settings as region
from handlers.construction import construction_pricing_profiles as pricing
from handlers.construction import construction_unit_catalog as units


@pytest.fixture
def settings_screen(wizard, language_data, monkeypatch):
    c = wizard
    c.progress = progress(c.tenant, started=True, step="LOCALIZATION")
    c.language_result = {
        **c.common,
        "item": {
            **deepcopy(language_data),
            "tenant_id": str(c.tenant), "user_id": str(c.user),
            "row_version": 39,
        },
    }
    c.currency_result = {
        **c.common,
        "item": {
            "tenant_id": str(c.tenant),
            "organization_default_currency": "EUR",
            "row_version": 41,
            "currencies": [
                {"code": "EUR", "name": "Euro"},
                {"code": "USD", "name": "US Dollar"},
            ],
        },
    }
    c.language_api = NS(read=AsyncMock(return_value=c.language_result))
    c.currency_api = NS(read=AsyncMock(return_value=c.currency_result))
    monkeypatch.setattr(handler, "ConstructionLanguageBotApi", lambda service: c.language_api)
    monkeypatch.setattr(handler, "ConstructionCurrencyBotApi", lambda service: c.currency_api)
    monkeypatch.setattr(handler, "OnboardingBotApi", lambda service: c.guide_api)
    monkeypatch.setattr(shell, "edit_or_replace_menu_message", c.sent)
    c.targets = {
        "region": AsyncMock(), "pricing": AsyncMock(), "units": AsyncMock(),
    }
    monkeypatch.setattr(region, "open_settings", c.targets["region"])
    monkeypatch.setattr(pricing, "open_profiles", c.targets["pricing"])
    monkeypatch.setattr(units, "show_catalog", c.targets["units"])

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def click(c, action):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[guide.VIEW], action)

    async def dispatch(event, data):
        return await handler.configure_callback(event, c.state)

    await guide.navigation_middleware(dispatch, c.callback, {"state": c.state})


@pytest.mark.asyncio
@pytest.mark.parametrize("action,resource,version", [
    ("language", "language", 39), ("currency", "currency", 41),
])
async def test_language_and_currency_use_their_own_versions(settings_screen, action, resource, version):
    c = settings_screen
    await open_guide(c)
    old_callback = handler.cb(c.data[guide.VIEW], action)
    await click(c, action)
    selected = c.data[shell.VIEW]
    assert selected["resource"] == resource
    assert selected["phase"] == "catalog"
    assert selected["row_version"] == version
    assert selected["row_version"] != c.progress["row_version"]
    assert selected["actor_user_id"] == str(c.user)
    assert c.data[guide.VIEW] == {}
    api = c.language_api if action == "language" else c.currency_api
    args = api.read.await_args.kwargs
    assert args["tenant_id"] == c.tenant
    assert args["expected_user_id"] == c.user
    assert args["telegram_user_id"] == 123
    c.callback.data = old_callback
    await handler.configure_callback(c.callback, c.state)
    api.read.assert_awaited_once()
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,step", [
    ("region", "LOCALIZATION"), ("units", "LOCALIZATION"),
    ("pricing", "PRICING_PROFILES"),
])
async def test_existing_child_screen_receives_bound_context(settings_screen, action, step):
    c = settings_screen
    c.progress["current_step"] = step
    c.data["construction_pricing_profile_pending_requests"] = {"preserved": {"key": "original"}}
    await open_guide(c)
    await click(c, action)
    target = c.targets[action]
    target.assert_awaited_once()
    args = target.await_args.args
    source, parent = args[2], args[3]
    assert source == shell.snapshot(c.data)
    assert parent["tenant_id"] == str(c.tenant)
    assert parent["actor_user_id"] == str(c.user)
    assert parent["telegram_user_id"] == 123
    assert "row_version" not in parent and "expected_row_version" not in parent
    assert c.data[guide.VIEW] == {}
    assert c.data["construction_pricing_profile_pending_requests"] == {"preserved": {"key": "original"}}
    if action == "units":
        assert target.await_args.kwargs == {"page": 0}
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_step_change_prevents_opening_old_step_settings(settings_screen):
    c = settings_screen
    await open_guide(c)
    c.progress["current_step"] = "PRICING_PROFILES"
    await click(c, "language")
    c.language_api.read.assert_not_awaited()
    assert c.data[guide.VIEW]["phase"] == "blocked"
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_progress_read_does_not_restore_another_tenant(settings_screen):
    c = settings_screen
    await open_guide(c)
    c.sent.reset_mock()

    async def late(**kwargs):
        c.data[guide.CONTEXT]["tenant_id"] = str(uuid4())
        return {**c.common, "item": deepcopy(c.progress)}

    c.guide_api.read.side_effect = late
    await click(c, "language")
    c.language_api.read.assert_not_awaited()
    c.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_actor_prevents_configuration(settings_screen):
    c = settings_screen
    await open_guide(c)
    c.guide_api.read.side_effect = lambda **kwargs: {
        **c.common, "actor_user_id": str(uuid4()), "item": deepcopy(c.progress),
    }
    await click(c, "currency")
    c.currency_api.read.assert_not_awaited()
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_and_stale_menu_cannot_open_settings(settings_screen):
    c = settings_screen
    await open_guide(c)
    c.guide_api.read.reset_mock()
    c.callback.message.chat.type = "group"
    await click(c, "language")
    c.callback.message.chat.type = "private"
    c.callback.message.message_id = 999
    c.callback.data = handler.cb(c.data[guide.VIEW], "language")
    await handler.configure_callback(c.callback, c.state)
    c.guide_api.read.assert_not_awaited()
    c.language_api.read.assert_not_awaited()


def test_configuration_buttons_match_fixed_wizard_steps():
    view = {"language": "uk", "nonce": "a" * 32}
    rows = handler.configuration_rows(view, "COMPANY")
    assert rows[0][0].callback_data == "CP:open"
    localization = handler.configuration_rows(view, "LOCALIZATION")
    assert [row[0].callback_data.split(":")[1] for row in localization] == [
        "language", "region", "currency", "units",
    ]
    assert handler.configuration_rows(view, "TAXES") == []
    assert handler.configuration_rows(view, "MEASUREMENT_TEMPLATES") == []
    for row in localization + handler.configuration_rows(view, "PRICING_PROFILES"):
        assert len(row[0].callback_data.encode("utf-8")) <= 64
