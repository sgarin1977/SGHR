"""Local HTTP and Telegram tests; no external operations."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_region_settings as handler
from handlers.construction import construction_stage2_settings as shell
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_region_bot_api import RegionBotApi
from ui.construction_region_settings import LABELS


def region(tenant, version=7, configured=False):
    return {
        "tenant_id": str(tenant), "row_version": version,
        "organization_updated_at": datetime.now(timezone.utc).isoformat(),
        "country_code": "UA" if configured else None,
        "timezone": "Europe/Kyiv" if configured else None,
        "configured": configured,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [True, False])
async def test_existing_http_contract_preserves_reviewed_version_without_replay_key(changed):
    tenant, user = uuid4(), uuid4()
    calls = []

    def transport(request):
        calls.append(request)
        if request.url.path.endswith("/company-capabilities"):
            data = {
                "tenant_id": str(tenant),
                "can_manage_settings": True, "can_manage_profile": True,
            }
        else:
            data = region(tenant, 7 + int(changed), True)
            data["changed"] = changed
        return httpx.Response(200, json={"data": data, "meta": {}, "request_id": "local"})

    session = PlatformApiClientSession(user, tenant, SecretStr("local-only"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {"name": "Org", "language": "uk"})),
            api=NS(construction=ConstructionApiClient(http=http)),
        )
        result = await RegionBotApi(entry).write(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
            country_code="UA", timezone_code="Europe/Kyiv", expected_row_version=7,
        )
    writes = [request for request in calls if request.method == "PATCH"]
    assert len(writes) == 1
    assert writes[0].url.path == "/api/v1/construction/settings/region"
    assert not writes[0].url.query
    assert "idempotency-key" not in writes[0].headers
    assert json.loads(writes[0].content) == {
        "country_code": "UA", "timezone": "Europe/Kyiv", "expected_row_version": 7,
    }
    assert result["item"]["row_version"] == 7 + int(changed)


@pytest.mark.asyncio
@pytest.mark.parametrize("settings,profile", [(False, True), (True, False), (False, False)])
async def test_both_existing_server_capabilities_are_required(settings, profile):
    tenant, user = uuid4(), uuid4()
    client = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant),
            "can_manage_settings": settings, "can_manage_profile": profile,
        })),
        _request=AsyncMock(),
    )
    entry = NS(
        _presentation=AsyncMock(return_value=(
            PlatformApiClientSession(user, tenant, SecretStr("local-only")),
            {"name": "Org", "language": "uk"},
        )),
        api=NS(construction=client),
    )
    with pytest.raises(ConstructionApiClientError) as caught:
        await RegionBotApi(entry).read(telegram_user_id=123, tenant_id=tenant)
    assert caught.value.status_code == 403
    client._request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["foreign", "partial", "version", "timezone"])
async def test_read_rejects_malformed_or_foreign_region(fault):
    tenant, user = uuid4(), uuid4()
    item = region(tenant)
    if fault == "foreign":
        item["tenant_id"] = str(uuid4())
    elif fault == "partial":
        item["country_code"] = "UA"
    elif fault == "version":
        item["row_version"] = True
    else:
        item.update(configured=True, country_code="UA", timezone="not/a/timezone")
    session = PlatformApiClientSession(user, tenant, SecretStr("local-only"))
    client = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant), "can_manage_settings": True,
            "can_manage_profile": True,
        })),
        _request=AsyncMock(return_value=NS(data=item, meta={})),
    )
    entry = NS(
        _presentation=AsyncMock(return_value=(session, {"name": "Org", "language": "uk"})),
        api=NS(construction=client),
    )
    with pytest.raises(ConstructionApiClientError) as caught:
        await RegionBotApi(entry).read(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
        )
    assert caught.value.status_code == 502


@pytest.fixture
def screen(monkeypatch):
    c = NS(tenant=uuid4(), user=uuid4())
    c.item = region(c.tenant)
    c.common = {
        "actor_user_id": str(c.user), "language": "uk",
        "organization": "Org", "can_manage": True,
    }
    c.parent = {
        "telegram_user_id": 123, "tenant_id": str(c.tenant),
        "actor_user_id": str(c.user), "language": "uk", "menu_id": 10,
    }
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        shell.VIEW: deepcopy(c.parent), "last_menu_message_id": 10,
    }

    async def update(**values):
        c.data.update(deepcopy(values))

    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: deepcopy(c.data)),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.callback = NS(
        data="", from_user=sender, answer=AsyncMock(),
        message=NS(message_id=10, chat=NS(id=123, type="private")),
    )
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"), text="UA",
        answer=AsyncMock(return_value=NS(message_id=20)),
    )

    async def edited(*, callback, **kwargs):
        return NS(message_id=callback.message.message_id)

    c.sent = AsyncMock(side_effect=edited)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)

    async def write(**kwargs):
        item = region(c.tenant, kwargs["expected_row_version"] + 1, True)
        item.update(
            country_code=kwargs["country_code"],
            timezone=kwargs["timezone_code"], changed=True,
        )
        return {**c.common, "item": item}

    c.api = NS(
        read=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.item),
        }),
        context=AsyncMock(side_effect=lambda **kwargs: (None, deepcopy(c.common))),
        write=AsyncMock(side_effect=write),
    )
    monkeypatch.setattr(handler, "RegionBotApi", lambda service: c.api)

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def open_screen(c):
    await handler.open_settings(
        c.callback, c.state, shell.snapshot(c.data), c.parent, NS(),
    )


async def click(c, action):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], action)
    await handler.region_callback(c.callback, c.state)


async def enter(c, text):
    c.message.text = text
    await handler.region_message(c.message, c.state)


async def review(c):
    await open_screen(c)
    await click(c, "edit")
    await enter(c, "ua")
    await enter(c, "Europe/Kyiv")


@pytest.mark.asyncio
async def test_both_values_and_review_are_required_before_one_versioned_write(screen):
    c = screen
    await open_screen(c)
    assert c.data[handler.VIEW]["phase"] == "menu"
    assert "country_code" not in c.data[handler.VIEW]
    await click(c, "edit")
    assert c.data[handler.VIEW]["phase"] == "country"
    await enter(c, "ua")
    assert c.data[handler.VIEW]["phase"] == "timezone"
    assert "timezone" not in c.data[handler.VIEW]
    await enter(c, "Europe/Kyiv")
    assert c.data[handler.VIEW]["phase"] == "review"
    c.api.write.assert_not_awaited()
    c.callback.message.message_id = c.data["last_menu_message_id"]
    old_callback = handler.cb(c.data[handler.VIEW], "save")
    await click(c, "save")
    args = c.api.write.await_args.kwargs
    assert args["expected_row_version"] == 7
    assert args["country_code"] == "UA" and args["timezone_code"] == "Europe/Kyiv"
    assert args["expected_user_id"] == c.user
    c.callback.data = old_callback
    await handler.region_callback(c.callback, c.state)
    c.api.write.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_discards_draft_without_writing(screen):
    c = screen
    await review(c)
    await click(c, "refresh")
    assert c.data[handler.VIEW]["phase"] == "menu"
    assert "country_code" not in c.data[handler.VIEW]
    c.api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_write_requires_refresh_and_never_automatically_retries(screen):
    c = screen
    await review(c)
    c.api.write.side_effect = ConstructionApiClientError(status_code=504, code="api_timeout")
    await click(c, "save")
    assert c.data[handler.VIEW]["phase"] == "blocked"
    assert "country_code" not in c.data[handler.VIEW]
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(":save:" in b.callback_data for row in rows for b in row)
    await click(c, "save")
    c.api.write.assert_awaited_once()
    await click(c, "refresh")
    assert c.data[handler.VIEW]["phase"] == "menu"
    c.api.write.assert_awaited_once()


@pytest.mark.asyncio
async def test_conflict_does_not_rebase_or_send_second_write(screen):
    c = screen
    await review(c)
    c.item["row_version"] = 99
    c.api.write.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "save")
    assert c.api.write.await_args.kwargs["expected_row_version"] == 7
    assert c.data[handler.VIEW]["phase"] == "blocked"
    c.api.write.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_read_does_not_restore_another_organization(screen):
    c = screen

    async def late(**kwargs):
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
        return {**c.common, "item": deepcopy(c.item)}

    c.api.read.side_effect = late
    await open_screen(c)
    c.sent.assert_not_awaited()
    assert handler.VIEW not in c.data


@pytest.mark.asyncio
async def test_group_and_stale_callbacks_cannot_read_or_write(screen):
    c = screen
    await open_screen(c)
    c.api.read.reset_mock()
    c.callback.message.chat.type = "group"
    await click(c, "edit")
    c.callback.message.chat.type = "private"
    c.callback.message.message_id = 999
    c.callback.data = handler.cb(c.data[handler.VIEW], "edit")
    await handler.region_callback(c.callback, c.state)
    c.api.read.assert_not_awaited()
    c.api.write.assert_not_awaited()


def test_translations_and_callback_size():
    assert all(set(values) == set(("uk", "ru", "en", "pt", "pl", "de", "nl"))
               for values in LABELS.values())
    for action in ("refresh", "edit", "save"):
        assert len(handler.cb({"nonce": "a" * 32}, action).encode("utf-8")) <= 64
