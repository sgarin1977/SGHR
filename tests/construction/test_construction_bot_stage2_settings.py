"""Local HTTP transport and Telegram language settings behavior."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from pydantic import SecretStr
import pytest

from handlers.construction import construction_stage2_settings as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError,
    PlatformApiClientSession,
)
from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi,
)
from ui.construction_stage2_settings import LABELS


@pytest.fixture
def language_data():
    return {
        "tenant_id": str(uuid4()), "user_id": str(uuid4()),
        "interface_language": "uk",
        "organization_default_language": "ru",
        "organization_updated_at": datetime.now(timezone.utc).isoformat(),
        "row_version": 7,
        "languages": [
            {"code": code, "name": name, "native_name": name,
             "label": name, "enabled_for_organization": code == "ru"}
            for code, name in (
                ("uk", "Українська"), ("ru", "Русский"), ("en", "English"),
            )
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "tenant", "user", "version", "duplicate"])
async def test_read_validates_identity_version_and_catalog(language_data, fault):
    from uuid import UUID
    original = deepcopy(language_data)
    if fault == "tenant":
        language_data["tenant_id"] = str(uuid4())
    elif fault == "user":
        language_data["user_id"] = str(uuid4())
    elif fault == "version":
        language_data["row_version"] = True
    elif fault == "duplicate":
        language_data["languages"].append(language_data["languages"][0])
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": language_data, "meta": {}, "request_id": "local",
        })
    session = PlatformApiClientSession(
        UUID(original["user_id"]), UUID(original["tenant_id"]), SecretStr("local"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        api.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": original["tenant_id"],
            "can_manage_settings": True, "can_manage_profile": True,
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {"name": "Org", "language": "uk"})),
            api=NS(construction=api),
        )
        call = ConstructionLanguageBotApi(entry).read(
            telegram_user_id=123, tenant_id=session.tenant_id,
        )
        if fault:
            with pytest.raises(ConstructionApiClientError):
                await call
        else:
            result = await call
            assert result["language"] == "uk"
            assert result["can_manage"] is True
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url.path == "/api/v1/construction/settings/languages"
        assert not calls[0].url.query


@pytest.mark.asyncio
@pytest.mark.parametrize("can_manage", [True, False])
async def test_patch_is_versioned_without_rebase_or_replay_key(language_data, can_manage):
    from uuid import UUID
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": {
                "tenant_id": language_data["tenant_id"],
                "organization_default_language": "en",
                "organization_updated_at": language_data["organization_updated_at"],
                "row_version": 8, "changed": True,
            }, "meta": {}, "request_id": "local",
        })
    session = PlatformApiClientSession(
        UUID(language_data["user_id"]), UUID(language_data["tenant_id"]),
        SecretStr("local"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        api.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": language_data["tenant_id"],
            "can_manage_settings": can_manage, "can_manage_profile": True,
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {"language": "uk"})),
            api=NS(construction=api),
        )
        call = ConstructionLanguageBotApi(entry).write(
            telegram_user_id=123, tenant_id=session.tenant_id,
            expected_user_id=session.user_id,
            language_code="en", expected_row_version=7,
        )
        if can_manage:
            await call
            assert len(calls) == 1
            assert calls[0].method == "PATCH"
            assert json.loads(calls[0].content) == {
                "default_language": "en", "expected_row_version": 7,
            }
            assert "idempotency-key" not in calls[0].headers
        else:
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == 403
            assert calls == []


@pytest.fixture
def screen(monkeypatch, language_data):
    c = NS(data={
        handler.CONTEXT: {
            "telegram_user_id": 123,
            "tenant_id": language_data["tenant_id"],
        },
        "last_menu_message_id": 10,
    })
    async def update(**values):
        c.data.update(deepcopy(values))
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: deepcopy(c.data)),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    c.callback = NS(
        data="C2:open", from_user=NS(id=123, language_code="en", is_bot=False),
        message=NS(
            message_id=10, chat=NS(id=123, type="private"),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    c.result = {
        "item": language_data, "actor_user_id": language_data["user_id"],
        "organization": "Org", "language": "uk", "can_manage": True,
    }
    c.protocol = NS(
        read=AsyncMock(return_value=c.result),
        write=AsyncMock(return_value={
            "item": {"changed": True}, "language": "uk",
        }),
    )
    monkeypatch.setattr(handler, "ConstructionLanguageBotApi", lambda service: c.protocol)
    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=10))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def click(c, action, value=None):
    c.callback.data = handler.cb(c.data[handler.VIEW], action, value)
    await handler.settings_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_review_is_required_and_cancel_writes_nothing(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state)
    assert c.data[handler.VIEW]["language"] == "uk"
    await click(c, "languages")
    await click(c, "c", 2)
    assert c.data[handler.VIEW]["phase"] == "review"
    assert c.data[handler.VIEW]["selected_code"] == "en"
    c.protocol.write.assert_not_awaited()
    await click(c, "refresh")
    c.protocol.write.assert_not_awaited()
    assert c.data[handler.VIEW]["phase"] == "menu"


@pytest.mark.asyncio
async def test_save_uses_reviewed_version_and_duplicate_callback_cannot_write(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state)
    await click(c, "languages")
    await click(c, "c", 2)
    old_callback = handler.cb(c.data[handler.VIEW], "save")
    await click(c, "save")
    assert c.protocol.write.await_count == 1
    args = c.protocol.write.await_args.kwargs
    assert args["expected_row_version"] == 7
    assert args["language_code"] == "en"
    c.callback.data = old_callback
    await handler.settings_callback(c.callback, c.state)
    assert c.protocol.write.await_count == 1


@pytest.mark.asyncio
async def test_read_only_catalog_has_no_selection_buttons(screen):
    c = screen
    c.result["can_manage"] = False
    await handler.settings_callback(c.callback, c.state)
    await click(c, "languages")
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(":c:" in item.callback_data for row in rows for item in row)
    c.protocol.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_reordering_does_not_change_selected_identity(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state)
    await click(c, "languages")
    c.result["item"]["languages"].reverse()
    await click(c, "c", 2)
    assert c.data[handler.VIEW]["selected_code"] == "en"


@pytest.mark.asyncio
async def test_version_change_before_review_is_not_silently_rebased(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state)
    await click(c, "languages")
    c.result["item"]["row_version"] = 8
    await click(c, "c", 2)
    assert c.data[handler.VIEW]["phase"] == "blocked"
    c.protocol.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_write_requires_read_before_another_attempt(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state)
    await click(c, "languages")
    await click(c, "c", 2)
    c.protocol.write.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "save")
    assert c.data[handler.VIEW]["phase"] == "blocked"
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(":save:" in item.callback_data for row in rows for item in row)
    await click(c, "save")
    assert c.protocol.write.await_count == 1


@pytest.mark.asyncio
async def test_late_read_cannot_restore_another_organization(screen):
    c = screen
    async def late_read(**kwargs):
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
        return c.result
    c.protocol.read.side_effect = late_read
    await handler.settings_callback(c.callback, c.state)
    c.sent.assert_not_awaited()
    c.protocol.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_and_stale_menu_cannot_read_settings(screen):
    c = screen
    c.callback.message.chat.type = "group"
    await handler.settings_callback(c.callback, c.state)
    c.protocol.read.assert_not_awaited()
    c.callback.message.chat.type = "private"
    c.callback.message.message_id = 9
    await handler.settings_callback(c.callback, c.state)
    c.protocol.read.assert_not_awaited()


def test_translations_and_callback_lengths():
    assert all(len(values) == 7 for values in LABELS.values())
    view = {"nonce": "a" * 32}
    for action, argument in (
        ("languages", None), ("refresh", None),
        ("save", None), ("p", 999999), ("c", 7),
    ):
        assert len(handler.cb(view, action, argument).encode("utf-8")) <= 64
