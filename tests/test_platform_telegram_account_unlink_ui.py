"""Own-account unlink transport and real MemoryStorage confirmation/recovery."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from pydantic import SecretStr

from api.routes.platform_telegram_binding import AccountUnlinkedResponse
from handlers import platform_telegram_binding_ui as parent
from handlers import platform_telegram_account_unlink_ui as unlink
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_platform_telegram_binding_ui import screen
from ui.platform_telegram_binding import LABELS, tr


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [True, False])
async def test_transport_uses_owned_account_route_version_and_mandatory_key(changed):
    identifier = uuid4()
    calls = []
    receipt = dict(account_id=str(identifier), status="unlinked", row_version=7 + int(changed), changed=changed)
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"data": receipt, "meta": {}, "request_id": "unlink-ui"})
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-unlink-test"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handle),
    ) as http:
        result = await ConstructionApiClient(http=http).unlink_telegram_account(
            session=session, account_id=identifier, expected_row_version=7,
            idempotency_key="frozen-unlink",
        )
    assert result.model_dump(mode="json") == receipt
    assert len(calls) == 1 and calls[0].method == "POST"
    assert calls[0].url.path == "/api/v1/platform/integrations/telegram/accounts/" + str(identifier) + "/unlink"
    assert calls[0].headers["idempotency-key"] == "frozen-unlink"
    assert json.loads(calls[0].content) == {"expected_row_version": 7}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["account", "version", "status", "changed"])
async def test_transport_rejects_wrong_unlink_receipt(failure):
    identifier = uuid4()
    receipt = dict(account_id=str(identifier), status="unlinked", row_version=8, changed=True)
    if failure == "account":
        receipt["account_id"] = str(uuid4())
    elif failure == "version":
        receipt["row_version"] = 9
    elif failure == "status":
        receipt["status"] = "active"
    else:
        receipt["changed"] = 1
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-unlink-test"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": receipt, "meta": {}, "request_id": "unlink-ui"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).unlink_telegram_account(
                session=session, account_id=identifier, expected_row_version=7,
                idempotency_key="frozen-unlink",
            )


@pytest.mark.asyncio
async def test_entry_uses_platform_identity_without_construction_navigation():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-unlink-test"))
    identifier = uuid4()
    result = AccountUnlinkedResponse(
        account_id=identifier, status="unlinked", row_version=8, changed=True,
    )
    api = NS(unlink_telegram_account=AsyncMock(return_value=result))
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    entry._presentation = AsyncMock(side_effect=AssertionError("No Construction navigation."))
    await entry.unlink_telegram_account(
        telegram_user_id=123, tenant_id=session.tenant_id, expected_user_id=session.user_id,
        account_id=identifier, expected_row_version=7, idempotency_key="frozen-unlink",
    )
    entry._presentation.assert_not_awaited()
    api.unlink_telegram_account.assert_awaited_once_with(
        session=session, account_id=identifier, expected_row_version=7,
        idempotency_key="frozen-unlink",
    )


@pytest_asyncio.fixture
async def unlink_screen(screen):
    c = screen
    storage = MemoryStorage()
    c.state = FSMContext(storage, StorageKey(bot_id=123456, chat_id=123, user_id=123))
    await c.state.set_data(deepcopy(c.data))
    c.rows = [
        dict(account_id=str(UUID(int=1)), telegram_user_id="123", status="active", row_version=3),
        dict(account_id=str(UUID(int=2)), telegram_user_id="456", status="active", row_version=5),
    ]
    c.service.telegram_accounts.return_value["items"] = c.rows
    c.unlink_receipts = {}
    async def perform(**kwargs):
        key = kwargs["idempotency_key"]
        if key not in c.unlink_receipts:
            c.unlink_receipts[key] = AccountUnlinkedResponse(
                account_id=kwargs["account_id"], status="unlinked",
                row_version=kwargs["expected_row_version"] + 1, changed=True,
            )
        return {"actor_user_id": str(c.actor), "account": c.unlink_receipts[key], "language": "uk"}
    c.perform_unlink = perform
    c.service.unlink_telegram_account = AsyncMock(side_effect=perform)
    original_show = parent.edit_or_replace_menu_message
    async def capture(**kwargs):
        c.last_text = kwargs["text"]
        return await original_show(**kwargs)
    # Parent fixture already provides a local Telegram rendering mock.
    from unittest.mock import patch
    with patch.object(parent, "edit_or_replace_menu_message", side_effect=capture):
        try:
            yield c
        finally:
            await storage.close()


async def send(c, command):
    data = await c.state.get_data()
    c.callback.data = command
    c.callback.message.message_id = data["last_menu_message_id"]
    await parent.telegram_binding_callback(c.callback, c.state)
    c.data = deepcopy(await c.state.get_data())


async def open_accounts(c):
    await send(c, "TGB:open")


async def select_account(c, index):
    await send(c, "TGB:unlink:" + c.data[parent.VIEW]["nonce"] + ":" + str(index))


async def confirm(c):
    await send(c, unlink.command(c.data[unlink.VIEW], "confirm"))


@pytest.mark.asyncio
async def test_review_then_confirm_unlinks_only_selected_account(unlink_screen):
    c = unlink_screen
    await open_accounts(c)
    await select_account(c, 1)
    assert c.data[unlink.VIEW]["phase"] == "review"
    assert "456" in c.last_text
    c.service.unlink_telegram_account.assert_not_awaited()
    await confirm(c)
    args = c.service.unlink_telegram_account.await_args.kwargs
    assert args["account_id"] == UUID(int=2)
    assert args["expected_row_version"] == 5
    assert args["expected_user_id"] == c.actor
    assert c.data[unlink.VIEW]["phase"] == "done"
    c.service.telegram_binding.assert_not_awaited()
    await confirm(c)
    c.service.unlink_telegram_account.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_account_has_explicit_warning_and_no_last_account_prohibition(unlink_screen):
    c = unlink_screen
    c.service.telegram_accounts.return_value["items"] = c.rows[:1]
    await open_accounts(c)
    await select_account(c, 0)
    assert c.data[unlink.VIEW]["is_current"] is True
    assert tr("unlink_current", "uk") in c.last_text
    await confirm(c)
    assert c.data[unlink.VIEW]["phase"] == "done"
    assert c.service.unlink_telegram_account.await_args.kwargs["account_id"] == UUID(int=1)


@pytest.mark.asyncio
async def test_cancel_does_not_unlink(unlink_screen):
    c = unlink_screen
    await open_accounts(c)
    await select_account(c, 1)
    await open_accounts(c)
    assert c.data.get(unlink.VIEW) == {}
    c.service.unlink_telegram_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_version_before_review_requires_refresh(unlink_screen):
    c = unlink_screen
    await open_accounts(c)
    c.rows[1]["row_version"] = 6
    await select_account(c, 1)
    assert c.data[unlink.VIEW]["phase"] == "conflict"
    c.service.unlink_telegram_account.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,phase", [
    (409, "ROW_VERSION_CONFLICT", "conflict"),
    (403, "account_unavailable", "blocked"),
    (504, "api_timeout", "pending"),
])
async def test_failures_keep_original_version_without_rebasing(unlink_screen, status, code, phase):
    c = unlink_screen
    await open_accounts(c)
    await select_account(c, 1)
    c.service.unlink_telegram_account.side_effect = ConstructionApiClientError(
        status_code=status, code=code,
    )
    await confirm(c)
    assert c.data[unlink.VIEW]["phase"] == phase
    assert c.service.unlink_telegram_account.await_args.kwargs["expected_row_version"] == 5
    c.service.unlink_telegram_account.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_recovery_uses_original_key_and_target_after_returning_to_accounts(unlink_screen):
    c = unlink_screen
    await open_accounts(c)
    await select_account(c, 1)
    c.service.unlink_telegram_account.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await confirm(c)
    original = deepcopy(c.service.unlink_telegram_account.await_args.kwargs)
    await open_accounts(c)
    # Selecting another account must preserve the earlier uncertain operation.
    await select_account(c, 0)
    assert c.data[unlink.VIEW]["account_id"] == str(UUID(int=2))
    c.service.unlink_telegram_account.side_effect = c.perform_unlink
    reads = c.service.telegram_accounts.await_count
    await send(c, unlink.command(c.data[unlink.VIEW], "retry"))
    assert c.service.unlink_telegram_account.await_args.kwargs == original
    assert c.service.telegram_accounts.await_count == reads
    assert c.data[unlink.VIEW]["phase"] == "done"


@pytest.mark.asyncio
async def test_navigation_invalidates_old_review(unlink_screen):
    c = unlink_screen
    await open_accounts(c)
    await select_account(c, 1)
    old = unlink.command(c.data[unlink.VIEW], "confirm")
    c.callback.data = "CS_HOME"
    await parent.telegram_binding_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    await send(c, old)
    c.service.unlink_telegram_account.assert_not_awaited()


def test_translations_and_callback_lengths():
    assert all(len(values) == 7 for values in LABELS.values())
    assert len(("TGB:unlink:" + "a" * 32 + ":4").encode("utf-8")) <= 64
    view = {"nonce": "a" * 32}
    for action in ("confirm", "retry"):
        assert len(unlink.command(view, action).encode("utf-8")) <= 64
