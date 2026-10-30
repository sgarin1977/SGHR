"""Own Telegram UI transport, secret-free state and immutable keyed recovery."""
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from api.routes.platform_telegram_binding import (
    BindingIssuedResponse, BindingResentResponse, BindingRevokedResponse,
)
from handlers import platform_telegram_binding_ui as ui
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.platform_telegram_binding import LABELS


def issued():
    now = datetime.now(timezone.utc)
    code = base64.urlsafe_b64encode(b"b" * 32).decode().rstrip("=")
    return dict(
        binding_id=str(uuid4()), row_version=1, code=code,
        deep_link="https://t.me/SGHR_test_bot?start=bind_" + code,
        issued_at=now.isoformat(), expires_at=(now + timedelta(minutes=15)).isoformat(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["initiate", "resend", "revoke"])
async def test_transport_uses_existing_platform_routes_and_key(operation):
    source = uuid4()
    data = issued()
    if operation == "resend":
        data.update(source_binding_id=str(source), source_row_version=2)
    elif operation == "revoke":
        data = dict(binding_id=str(source), row_version=2, revoked_at=datetime.now(timezone.utc).isoformat())
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"data": data, "meta": {}, "request_id": "binding-ui"})
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-binding-test"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handle),
    ) as http:
        result = await ConstructionApiClient(http=http).telegram_binding(
            session=session, operation=operation, idempotency_key="stable-key",
            binding_id=None if operation == "initiate" else source,
            expected_row_version=None if operation == "initiate" else 1,
        )
    assert len(calls) == 1
    suffix = "/bindings" if operation == "initiate" else "/bindings/" + str(source) + "/" + operation
    assert calls[0].url.path == "/api/v1/platform/integrations/telegram" + suffix
    assert calls[0].headers["idempotency-key"] == "stable-key"
    assert json.loads(calls[0].content) == ({} if operation == "initiate" else {"expected_row_version": 1})
    if operation != "revoke":
        assert result.code == data["code"] and result.deep_link == data["deep_link"]
        assert data["code"] not in repr(result) and data["deep_link"] not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["host", "query", "ttl", "version"])
async def test_transport_rejects_invalid_binding_presentation(failure):
    data = issued()
    if failure == "host":
        data["deep_link"] = data["deep_link"].replace("t.me", "example.com")
    elif failure == "query":
        data["deep_link"] += "&other=1"
    elif failure == "ttl":
        data["expires_at"] = (datetime.fromisoformat(data["issued_at"]) + timedelta(hours=24)).isoformat()
    else:
        data["row_version"] = 2
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-binding-test"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": data, "meta": {}, "request_id": "binding-ui"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).telegram_binding(
                session=session, operation="initiate", idempotency_key="stable-key",
            )


@pytest.mark.asyncio
async def test_account_read_has_no_user_or_tenant_selector():
    calls = []
    rows = [
        dict(account_id=str(UUID(int=index)), telegram_user_id=str(100 + index),
             status="active", row_version=1)
        for index in (1, 2)
    ]
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": rows, "meta": {"pagination": {"next_cursor": None}}, "request_id": "accounts-ui",
        })
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-binding-test"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handle),
    ) as http:
        result = await ConstructionApiClient(http=http).telegram_accounts(session=session)
    assert result.data == rows
    assert dict(calls[0].url.params) == {"limit": "5"}


@pytest.mark.asyncio
async def test_entry_does_not_borrow_construction_permissions():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-binding-test"))
    transport = NS(telegram_binding=AsyncMock(return_value=BindingIssuedResponse.model_validate(issued())))
    entry = ConstructionBotApiEntry(api=NS(construction=transport), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    entry._presentation = AsyncMock(side_effect=AssertionError("No Construction navigation gate."))
    await entry.telegram_binding(
        telegram_user_id=123, tenant_id=session.tenant_id, expected_user_id=session.user_id,
        operation="initiate", idempotency_key="stable-key",
    )
    entry._presentation.assert_not_awaited()
    transport.telegram_binding.assert_awaited_once()


@pytest.fixture
def screen(monkeypatch):
    c = NS(actor=uuid4(), tenant=uuid4(), serial=1, receipts={})
    c.data = {
        ui.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        "last_menu_message_id": 1,
    }
    async def update(**values):
        c.data.update(deepcopy(values))
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: deepcopy(c.data)),
        update_data=AsyncMock(side_effect=update), get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.callback = NS(
        data="TGB:open", from_user=sender, answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    async def show(**kwargs):
        c.last_markup = kwargs["reply_markup"]
        c.serial += 1
        return NS(message_id=c.serial)
    monkeypatch.setattr(ui, "edit_or_replace_menu_message", AsyncMock(side_effect=show))
    async def issue(**kwargs):
        key = kwargs["idempotency_key"]
        if key not in c.receipts:
            data = issued()
            operation = kwargs["operation"]
            if operation == "resend":
                data.update(
                    source_binding_id=str(kwargs["binding_id"]),
                    source_row_version=kwargs["expected_row_version"] + 1,
                )
                model = BindingResentResponse
            elif operation == "revoke":
                data = dict(
                    binding_id=str(kwargs["binding_id"]),
                    row_version=kwargs["expected_row_version"] + 1,
                    revoked_at=datetime.now(timezone.utc).isoformat(),
                )
                model = BindingRevokedResponse
            else:
                model = BindingIssuedResponse
            c.receipts[key] = model.model_validate(data)
        return {"actor_user_id": str(c.actor), "binding": c.receipts[key], "language": "uk"}
    c.issue = issue
    c.service = NS(
        telegram_accounts=AsyncMock(return_value={
            "items": [], "next_cursor": None, "actor_user_id": str(c.actor), "language": "uk",
        }),
        telegram_binding=AsyncMock(side_effect=issue),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(ui, "construction_bot_api_entry", entry)
    return c


async def click(c, action):
    c.callback.data = ui.cb(c.data[ui.VIEW], action)
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await ui.telegram_binding_callback(c.callback, c.state)


async def create(c):
    await ui.telegram_binding_callback(c.callback, c.state)
    await click(c, "new")
    c.service.telegram_binding.assert_not_awaited()
    await click(c, "create")


@pytest.mark.asyncio
async def test_issued_secret_is_only_in_transient_telegram_presentation(screen):
    c = screen
    await create(c)
    attempt = c.data[ui.ATTEMPT]
    assert attempt["phase"] == "issued"
    receipt = c.receipts[attempt["idempotency_key"]]
    assert receipt.code not in repr(c.data) and receipt.deep_link not in repr(c.data)
    urls = [button.url for row in c.last_markup.inline_keyboard for button in row if button.url]
    assert urls == [receipt.deep_link]
    assert c.service.telegram_binding.await_args.kwargs["expected_user_id"] == c.actor


@pytest.mark.asyncio
async def test_restore_replays_original_request_without_new_key_or_expiry(screen):
    c = screen
    await create(c)
    first = deepcopy(c.service.telegram_binding.await_args.kwargs)
    before = deepcopy(c.data[ui.ATTEMPT]["receipt"])
    await click(c, "restore")
    assert c.service.telegram_binding.await_args.kwargs == first
    assert c.data[ui.ATTEMPT]["receipt"] == before


@pytest.mark.asyncio
async def test_resend_targets_previous_binding_and_revoke_requires_confirmation(screen):
    c = screen
    await create(c)
    original = deepcopy(c.data[ui.ATTEMPT]["receipt"])
    await click(c, "resend")
    args = c.service.telegram_binding.await_args.kwargs
    assert args["operation"] == "resend"
    assert args["binding_id"] == UUID(original["binding_id"])
    assert args["expected_row_version"] == original["row_version"]
    assert c.data[ui.ATTEMPT]["receipt"]["binding_id"] != original["binding_id"]
    count = c.service.telegram_binding.await_count
    await click(c, "revoke")
    assert c.service.telegram_binding.await_count == count
    await click(c, "revoke_confirm")
    assert c.data[ui.ATTEMPT]["phase"] == "revoked"
    assert c.service.telegram_binding.await_args.kwargs["operation"] == "revoke"


@pytest.mark.asyncio
async def test_unknown_result_keeps_original_command_for_retry(screen):
    c = screen
    await ui.telegram_binding_callback(c.callback, c.state)
    await click(c, "new")
    c.service.telegram_binding.side_effect = ConstructionApiClientError(status_code=504, code="api_timeout")
    await click(c, "create")
    first = deepcopy(c.service.telegram_binding.await_args.kwargs)
    assert c.data[ui.ATTEMPT]["phase"] == "pending"
    await click(c, "new")
    assert c.service.telegram_binding.await_count == 1
    c.service.telegram_binding.side_effect = c.issue
    await click(c, "restore")
    assert c.service.telegram_binding.await_args.kwargs == first


@pytest.mark.asyncio
async def test_changed_tenant_cannot_mutate_old_view(screen):
    c = screen
    await create(c)
    count = c.service.telegram_binding.await_count
    c.data[ui.CONTEXT]["tenant_id"] = str(uuid4())
    await click(c, "resend")
    assert c.service.telegram_binding.await_count == count


def test_translations_and_callbacks_are_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    view = {"nonce": "a" * 32}
    for action in ("create", "restore", "resend", "revoke", "revoke_confirm", "accounts", "next"):
        assert len(ui.cb(view, action).encode("utf-8")) <= 64


@asynccontextmanager
async def real_memory_screen(c):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    storage = MemoryStorage()
    c.state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=123456, chat_id=123, user_id=123),
    )
    await c.state.set_data(deepcopy(c.data))
    try:
        yield c
    finally:
        await storage.close()


async def memory_click(c, action):
    data = await c.state.get_data()
    c.callback.data = ui.cb(data[ui.VIEW], action)
    c.callback.message.message_id = data["last_menu_message_id"]
    await ui.telegram_binding_callback(c.callback, c.state)
    c.data = deepcopy(await c.state.get_data())


async def memory_create(c):
    await ui.telegram_binding_callback(c.callback, c.state)
    await memory_click(c, "new")
    await memory_click(c, "create")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["initiate", "resend", "revoke"])
async def test_real_memory_storage_renders_validated_binding_result(screen, operation):
    c = screen
    async with real_memory_screen(c):
        await memory_create(c)
        assert c.data[ui.ATTEMPT]["phase"] == "issued"
        if operation == "resend":
            await memory_click(c, "resend")
        elif operation == "revoke":
            await memory_click(c, "revoke")
            await memory_click(c, "revoke_confirm")

        attempt = c.data[ui.ATTEMPT]
        assert attempt["phase"] == ("revoked" if operation == "revoke" else "issued")
        assert c.service.telegram_binding.await_args.kwargs["operation"] == operation
        result = c.receipts[attempt["idempotency_key"]]
        urls = [
            button.url
            for row in c.last_markup.inline_keyboard
            for button in row if button.url
        ]
        if operation == "revoke":
            assert urls == []
        else:
            assert urls == [result.deep_link]
            assert result.code not in repr(await c.state.get_data())
            assert result.deep_link not in repr(await c.state.get_data())


@pytest.mark.asyncio
async def test_real_memory_storage_recovers_original_request_after_503(screen):
    c = screen
    async with real_memory_screen(c):
        await ui.telegram_binding_callback(c.callback, c.state)
        await memory_click(c, "new")
        c.service.telegram_binding.side_effect = ConstructionApiClientError(
            status_code=503, code="service_unavailable",
        )
        await memory_click(c, "create")
        assert c.data[ui.ATTEMPT]["phase"] == "pending"
        original = deepcopy(c.service.telegram_binding.await_args.kwargs)

        c.service.telegram_binding.side_effect = c.issue
        await memory_click(c, "restore")
        assert c.service.telegram_binding.await_args.kwargs == original
        assert c.data[ui.ATTEMPT]["phase"] == "issued"
        result = c.receipts[c.data[ui.ATTEMPT]["idempotency_key"]]
        urls = [
            button.url
            for row in c.last_markup.inline_keyboard
            for button in row if button.url
        ]
        assert urls == [result.deep_link]


@pytest.mark.asyncio
async def test_real_memory_storage_keeps_navigation_and_pending_recovery(screen):
    c = screen
    async with real_memory_screen(c):
        await ui.telegram_binding_callback(c.callback, c.state)
        await memory_click(c, "new")

        async def leave_during_request(**kwargs):
            await c.state.update_data(**{ui.VIEW: {}})
            return await c.issue(**kwargs)

        c.service.telegram_binding.side_effect = leave_during_request
        await memory_click(c, "create")
        data = await c.state.get_data()
        assert data[ui.VIEW] == {}
        assert data[ui.ATTEMPT]["phase"] == "pending"
        assert "receipt" not in data[ui.ATTEMPT]
        result = c.receipts[data[ui.ATTEMPT]["idempotency_key"]]
        assert result.code not in repr(data)
        assert result.deep_link not in repr(data)
