"""Status filter UI and adapter regression without external operations."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from handlers.construction import construction_lead_filters as handler
from services.construction.construction_api_client import (
    ConstructionApiClientError, ConstructionApiResult, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_lead_filters import LABELS, STATUS_ORDER


@pytest.fixture
def screen(monkeypatch):
    c = NS(user=uuid4(), tenant=uuid4(), lead=uuid4())
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        "last_menu_message_id": 1,
    }
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    c.callback = NS(
        data="LF:open", from_user=NS(id=123, is_bot=False, language_code="uk"),
        answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    async def leads(**kwargs):
        status = kwargs["status"]
        return {
            "actor_user_id": str(c.user), "language": "uk", "organization": "Organization",
            "items": [{
                "id": str(c.lead), "name": "Private lead",
                "status": status or "new",
            }],
            "next_cursor": "cursor-" + (status or "all"),
        }
    c.service = NS(leads=AsyncMock(side_effect=leads))
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def click(c, action, index=None):
    c.callback.data = handler.cb(c.data[handler.VIEW], action, index)
    await handler.filter_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_status_change_resets_cursor_and_next_keeps_same_filter(screen):
    c = screen
    await handler.filter_callback(c.callback, c.state)
    await click(c, "set", STATUS_ORDER.index("won") + 1)
    assert c.service.leads.await_args.kwargs["status"] == "won"
    assert c.service.leads.await_args.kwargs["cursor"] is None
    await click(c, "next")
    assert c.service.leads.await_args.kwargs["status"] == "won"
    assert c.service.leads.await_args.kwargs["cursor"] == "cursor-won"
    assert c.service.leads.await_args.kwargs["expected_user_id"] == c.user
    await click(c, "choose")
    await click(c, "set", STATUS_ORDER.index("lost") + 1)
    assert c.service.leads.await_args.kwargs["status"] == "lost"
    assert c.service.leads.await_args.kwargs["cursor"] is None
    assert "Private lead" not in repr(c.data)


@pytest.mark.asyncio
async def test_all_statuses_clear_filter_and_do_not_reuse_filtered_cursor(screen):
    c = screen
    await handler.filter_callback(c.callback, c.state)
    await click(c, "set", 1)
    await click(c, "choose")
    await click(c, "set", 0)
    assert c.service.leads.await_args.kwargs["status"] is None
    assert c.service.leads.await_args.kwargs["cursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "menu", "nonce", "sender"])
async def test_stale_filter_selection_never_calls_api(screen, failure):
    c = screen
    await handler.filter_callback(c.callback, c.state)
    c.service.leads.reset_mock()
    c.callback.data = handler.cb(c.data[handler.VIEW], "set", 1)
    if failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.callback.message.message_id = 99
    elif failure == "sender":
        c.callback.from_user.id = c.callback.message.chat.id = 999
    else:
        c.callback.data = "LF:set:obsolete:1"
    await handler.filter_callback(c.callback, c.state)
    c.service.leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_authority_does_not_show_cached_leads(screen):
    c = screen
    await handler.filter_callback(c.callback, c.state)
    await click(c, "set", 1)
    c.service.leads.side_effect = ConstructionApiClientError(status_code=403, code="forbidden")
    await click(c, "refresh")
    assert c.data[handler.VIEW] == {}
    assert "Private lead" not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_leaving_filter_clears_only_read_state(screen):
    c = screen
    await handler.filter_callback(c.callback, c.state)
    c.data["construction_activity_terminal_form"] = {"phase": "pending"}
    c.callback.data = "CS_HOME"
    await handler.filter_navigation_middleware(AsyncMock(), c.callback, {"state": c.state})
    assert c.data[handler.VIEW] == {}
    assert c.data["construction_activity_terminal_form"] == {"phase": "pending"}


@pytest.mark.asyncio
async def test_adapter_passes_status_and_cursor_without_changing_authorization():
    user, tenant = uuid4(), uuid4()
    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    api = NS(leads=AsyncMock(return_value=ConstructionApiResult(
        [{"id": str(uuid4()), "tenant_id": str(tenant), "status": "won"}],
        {"pagination": {"next_cursor": None}}, "local",
    )))
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        session, {"language": "uk", "name": "Organization"},
    ))
    result = await entry.leads(
        telegram_user_id=123, tenant_id=tenant, status="won",
        cursor="server-position", expected_user_id=user,
    )
    api.leads.assert_awaited_once_with(
        session=session, limit=10, cursor="server-position", filters={"status": "won"},
    )
    assert result["actor_user_id"] == str(user)
    api.leads.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.leads(
            telegram_user_id=123, tenant_id=tenant, status="won", expected_user_id=uuid4(),
        )
    api.leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_rejects_wrong_status_response():
    user, tenant = uuid4(), uuid4()
    api = NS(leads=AsyncMock(return_value=ConstructionApiResult(
        [{"id": str(uuid4()), "tenant_id": str(tenant), "status": "lost"}],
        {"pagination": {"next_cursor": None}}, "local",
    )))
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"language": "uk", "name": "Organization"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.leads(telegram_user_id=123, tenant_id=tenant, status="won")


def test_filter_translations_and_callbacks_are_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    for action in ("set", "choose", "next", "refresh"):
        assert len(handler.cb({"nonce": "a" * 32}, action, 9).encode("utf-8")) <= 64

# Current responsible user filter regression v1.
@pytest.fixture
def mine_screen(screen):
    c = screen
    async def leads(**kwargs):
        status = kwargs["status"]
        mine = kwargs["mine_only"]
        return {
            "actor_user_id": str(c.user), "language": "uk", "organization": "Organization",
            "items": [{
                "id": str(c.lead), "name": "Private lead",
                "status": status or "new",
                "responsible_user_id": str(c.user) if mine else None,
            }],
            "next_cursor": ("mine-" if mine else "all-") + (status or "statuses"),
        }
    c.service.leads.side_effect = leads
    return c


@pytest.mark.asyncio
async def test_responsible_toggle_resets_cursor_and_keeps_status(mine_screen):
    c = mine_screen
    await handler.filter_callback(c.callback, c.state)
    await click(c, "set", STATUS_ORDER.index("won") + 1)
    await click(c, "next")
    assert c.service.leads.await_args.kwargs["cursor"] == "all-won"
    await click(c, "mine")
    args = c.service.leads.await_args.kwargs
    assert args["mine_only"] is True
    assert args["status"] == "won" and args["cursor"] is None
    await click(c, "next")
    args = c.service.leads.await_args.kwargs
    assert args["mine_only"] is True
    assert args["status"] == "won" and args["cursor"] == "mine-won"
    await click(c, "mine")
    args = c.service.leads.await_args.kwargs
    assert args["mine_only"] is False
    assert args["status"] == "won" and args["cursor"] is None


@pytest.mark.asyncio
async def test_responsible_can_be_selected_before_status(mine_screen):
    c = mine_screen
    await handler.filter_callback(c.callback, c.state)
    await click(c, "mine")
    assert c.data[handler.VIEW]["phase"] == "choose"
    assert c.data[handler.VIEW]["mine_only"] is True
    await click(c, "set", STATUS_ORDER.index("lost") + 1)
    args = c.service.leads.await_args.kwargs
    assert args["mine_only"] is True and args["status"] == "lost"
    assert args["cursor"] is None
    assert "Private lead" not in repr(c.data[handler.VIEW])


@pytest.mark.asyncio
async def test_corrupted_responsible_filter_is_not_silently_accepted(mine_screen):
    c = mine_screen
    await handler.filter_callback(c.callback, c.state)
    c.data[handler.VIEW]["mine_only"] = "yes"
    c.service.leads.reset_mock()
    await click(c, "mine")
    c.service.leads.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "won"])
async def test_adapter_uses_canonical_platform_user_as_responsible(status):
    user, tenant = uuid4(), uuid4()
    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    api = NS(leads=AsyncMock(return_value=ConstructionApiResult(
        [{
            "id": str(uuid4()), "tenant_id": str(tenant),
            "status": status or "new", "responsible_user_id": str(user),
        }],
        {"pagination": {"next_cursor": None}}, "local",
    )))
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        session, {"language": "uk", "name": "Organization"},
    ))
    await entry.leads(
        telegram_user_id=123, tenant_id=tenant,
        status=status, mine_only=True, expected_user_id=user,
        cursor="bound-server-cursor",
    )
    filters = {"responsible_user_id": str(user)}
    if status is not None:
        filters["status"] = status
    api.leads.assert_awaited_once_with(
        session=session, limit=10, cursor="bound-server-cursor", filters=filters,
    )


@pytest.mark.asyncio
async def test_responsible_filter_rejects_changed_actor_before_list_query():
    user, tenant = uuid4(), uuid4()
    api = NS(leads=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"language": "uk", "name": "Organization"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.leads(
            telegram_user_id=123, tenant_id=tenant,
            mine_only=True, expected_user_id=uuid4(),
        )
    api.leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_responsible_filter_rejects_wrong_api_projection():
    user, tenant = uuid4(), uuid4()
    api = NS(leads=AsyncMock(return_value=ConstructionApiResult(
        [{
            "id": str(uuid4()), "tenant_id": str(tenant), "status": "new",
            "responsible_user_id": str(uuid4()),
        }],
        {"pagination": {"next_cursor": None}}, "local",
    )))
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"language": "uk", "name": "Organization"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.leads(telegram_user_id=123, tenant_id=tenant, mine_only=True)


def test_responsible_filter_translations_and_callback_are_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    assert len(handler.cb({"nonce": "a" * 32}, "mine").encode("utf-8")) <= 64
