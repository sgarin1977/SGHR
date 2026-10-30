"""Local bank transport and private bot workflow regression."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_company_banks as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_company_banks import LABELS, EDIT_FIELDS


@pytest.fixture
def account():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid4()), "label": "PRIVATE_BANK_LABEL",
        "account_holder_name": "PRIVATE_BANK_HOLDER",
        "bank_name": "Example bank", "country_code": "DE",
        "iban": "DE89370400440532013000", "bic_swift": "COBADEFFXXX",
        "account_number": None, "currency": "EUR",
        "is_primary": False, "is_active": True, "show_in_documents": False,
        "created_at": now, "updated_at": now, "row_version": 7,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "deactivate"])
async def test_transport_uses_existing_platform_routes_and_sparse_bodies(account, operation):
    calls = []
    payload = {
        "create": {
            "account_holder_name": "Example holder", "currency": "EUR",
            "iban": "DE89 3704 0044 0532 0130 00",
            "is_primary": False, "show_in_documents": False,
        },
        "update": {"label": None, "expected_row_version": 7},
        "deactivate": {"expected_row_version": 7},
    }[operation]
    receipt = deepcopy(account)
    if operation == "deactivate":
        receipt["is_active"] = False
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": receipt, "meta": {}, "request_id": "local-bank",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        await ConstructionApiClient(http=http).mutate_company_bank_account(
            session=PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test")),
            operation=operation, payload=payload,
            account_id=None if operation == "create" else UUID(account["id"]),
        )
    suffix = "" if operation == "create" else "/" + account["id"]
    if operation == "deactivate":
        suffix += "/deactivate"
    assert len(calls) == 1
    assert calls[0].url.path == "/api/v1/organization/company-profile/bank-accounts" + suffix
    assert calls[0].method == ("PATCH" if operation == "update" else "POST")
    assert json.loads(calls[0].content) == payload
    assert "idempotency-key" not in calls[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["duplicate", "version", "flag", "timestamp"])
async def test_transport_rejects_malformed_account_list(account, failure):
    items = [deepcopy(account)]
    if failure == "duplicate":
        items.append(deepcopy(account))
    elif failure == "version":
        items[0]["row_version"] = True
    elif failure == "flag":
        items[0]["show_in_documents"] = "false"
    else:
        items[0]["updated_at"] = "2026-10-26T12:00:00"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": items, "meta": {}, "request_id": "local"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).company_bank_accounts(
                session=PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test")),
            )


@pytest.mark.asyncio
async def test_deactivation_receipt_requires_exact_target_and_inactive_state(account):
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": account, "meta": {}, "request_id": "local"},
        )),
    ) as http:
        api = ConstructionApiClient(http=http)
        session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test"))
        with pytest.raises(ConstructionApiClientError):
            await api.mutate_company_bank_account(
                session=session, operation="deactivate",
                account_id=UUID(account["id"]), payload={"expected_row_version": 7},
            )
        with pytest.raises(ConstructionApiClientError):
            await api.mutate_company_bank_account(
                session=session, operation="update", account_id=uuid4(),
                payload={"label": None, "expected_row_version": 7},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"label": "new"},
    {"label": "new", "expected_row_version": True},
    {"is_active": False, "expected_row_version": 7},
    {"show_in_documents": None, "expected_row_version": 7},
    {"account_holder_name": None, "expected_row_version": 7},
])
async def test_invalid_update_never_reaches_http(account, payload):
    calls = []
    def transport(request):
        calls.append(request)
        raise AssertionError("Unexpected HTTP call")
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        with pytest.raises(ValueError):
            await ConstructionApiClient(http=http).mutate_company_bank_account(
                session=PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test")),
                operation="update", account_id=UUID(account["id"]), payload=payload,
            )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["actor", "permission", "target"])
async def test_entry_blocks_unauthorized_or_foreign_target(account, failure):
    user, tenant = uuid4(), uuid4()
    api = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={
            "can_manage_profile": failure != "permission",
        })),
        company_bank_accounts=AsyncMock(return_value=NS(data=[])),
        mutate_company_bank_account=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.mutate_company_bank_account(
            telegram_user_id=123, tenant_id=tenant,
            expected_user_id=uuid4() if failure == "actor" else user,
            operation="deactivate", account_id=UUID(account["id"]),
            payload={"expected_row_version": 7},
        )
    api.mutate_company_bank_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_does_not_rebase_expected_version_after_fresh_scope_read(account):
    user, tenant = uuid4(), uuid4()
    latest = {**account, "row_version": 99}
    api = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={"can_manage_profile": True})),
        company_bank_accounts=AsyncMock(return_value=NS(data=[latest])),
        mutate_company_bank_account=AsyncMock(side_effect=ConstructionApiClientError(
            status_code=409, code="ROW_VERSION_CONFLICT",
        )),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.mutate_company_bank_account(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
            operation="update", account_id=UUID(account["id"]),
            payload={"label": "New label", "expected_row_version": 7},
        )
    assert api.mutate_company_bank_account.await_args.kwargs["payload"] == {
        "label": "New label", "expected_row_version": 7,
    }
    assert api.mutate_company_bank_account.await_count == 1


@pytest.fixture
def screen(account, monkeypatch):
    c = NS(user=uuid4(), tenant=uuid4(), account=account)
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        handler.PARENT: {
            "nonce": "source", "telegram_user_id": 123,
            "tenant_id": str(c.tenant), "actor_user_id": str(c.user), "menu_id": 1,
        },
        "last_menu_message_id": 1,
        "construction_project_replace_attempt": {"status": "pending"},
    }
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.callback = NS(
        data="CBK:open:source", from_user=sender, answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"), text="New label",
        answer=AsyncMock(return_value=NS(message_id=1)),
    )
    c.result = {
        "items": [deepcopy(account)], "profile_id": str(uuid4()),
        "can_manage": True, "actor_user_id": str(c.user),
        "organization": "Organization", "language": "uk",
    }
    async def mutate(**kwargs):
        item = deepcopy(account)
        if kwargs["operation"] == "deactivate":
            item["is_active"] = False
        return {"item": item, "actor_user_id": str(c.user)}
    c.api = NS(
        company_bank_accounts=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        mutate_company_bank_account=AsyncMock(side_effect=mutate),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.api
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def action(c, name, value=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], name, value)
    await handler.bank_callback(c.callback, c.state)


async def send(c, text):
    c.message.text = text
    await handler.bank_message(c.message, c.state)


async def update_review(c, field="label", value="New private label"):
    await handler.bank_callback(c.callback, c.state)
    await action(c, "item", 0)
    await action(c, "edit")
    await action(c, "field", EDIT_FIELDS.index(field))
    await send(c, value)
    assert c.data[handler.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_bank_card_does_not_cache_existing_bank_details(screen):
    c = screen
    await handler.bank_callback(c.callback, c.state)
    await action(c, "item", 0)
    assert c.account["iban"] in c.sent.await_args.kwargs["text"]
    assert c.account["iban"] not in repr(c.data)
    assert c.account["account_holder_name"] not in repr(c.data)
    assert c.data[handler.VIEW]["expected_row_version"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [0, 1])
async def test_create_requires_explicit_details_flags_review_and_save(screen, kind):
    c = screen
    await handler.bank_callback(c.callback, c.state)
    await action(c, "add")
    await send(c, "New holder")
    await send(c, "EUR")
    assert c.data[handler.VIEW]["phase"] == "kind"
    await action(c, "kind", kind)
    field = "iban" if kind == 0 else "account_number"
    number = "DE89 3704 0044 0532 0130 00" if kind == 0 else "LOCAL-12345"
    await send(c, number)
    await action(c, "flag", 0)
    await action(c, "flag", 0)
    c.api.mutate_company_bank_account.assert_not_awaited()
    assert c.data[handler.VIEW]["phase"] == "review"
    await action(c, "commit")
    args = c.api.mutate_company_bank_account.await_args.kwargs
    assert args["operation"] == "create" and args["account_id"] is None
    assert args["payload"] == {
        "account_holder_name": "New holder", "currency": "EUR",
        field: number, "is_primary": False, "show_in_documents": False,
    }
    assert c.data[handler.VIEW]["phase"] == "done"
    assert "payload" not in c.data[handler.VIEW]


@pytest.mark.asyncio
async def test_update_is_sparse_versioned_and_cannot_submit_twice(screen):
    c = screen
    await update_review(c)
    old_callback = handler.cb(c.data[handler.VIEW], "commit")
    await action(c, "commit")
    args = c.api.mutate_company_bank_account.await_args.kwargs
    assert args["payload"] == {"label": "New private label", "expected_row_version": 7}
    assert args["expected_user_id"] == c.user
    assert args["account_id"] == UUID(c.account["id"])
    assert "New private label" not in repr(c.data)
    c.callback.data = old_callback
    await handler.bank_callback(c.callback, c.state)
    assert c.api.mutate_company_bank_account.await_count == 1


@pytest.mark.asyncio
async def test_optional_field_clear_is_explicit_null(screen):
    c = screen
    await handler.bank_callback(c.callback, c.state)
    await action(c, "item", 0)
    await action(c, "edit")
    await action(c, "field", EDIT_FIELDS.index("bic_swift"))
    await action(c, "clear")
    await action(c, "commit")
    assert c.api.mutate_company_bank_account.await_args.kwargs["payload"] == {
        "bic_swift": None, "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_deactivation_uses_version_and_no_delete_operation(screen):
    c = screen
    await handler.bank_callback(c.callback, c.state)
    await action(c, "item", 0)
    await action(c, "deactivate")
    c.api.mutate_company_bank_account.assert_not_awaited()
    await action(c, "commit")
    args = c.api.mutate_company_bank_account.await_args.kwargs
    assert args["operation"] == "deactivate"
    assert args["payload"] == {"expected_row_version": 7}
    assert c.data[handler.VIEW]["phase"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,phase", [(409, "blocked"), (422, "blocked"), (503, "uncertain")])
async def test_failure_keeps_original_version_and_has_no_retry(screen, status, phase):
    c = screen
    await update_review(c)
    c.api.mutate_company_bank_account.side_effect = ConstructionApiClientError(
        status_code=status, code="local_failure",
    )
    await action(c, "commit")
    assert c.data[handler.VIEW]["phase"] == phase
    assert c.data[handler.VIEW]["expected_row_version"] == 7
    assert "payload" not in c.data[handler.VIEW]
    await action(c, "commit")
    assert c.api.mutate_company_bank_account.await_count == 1


@pytest.mark.asyncio
async def test_tenant_switch_blocks_submit(screen):
    c = screen
    await update_review(c)
    c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    await action(c, "commit")
    c.api.mutate_company_bank_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_readonly_member_cannot_open_creation_form(screen):
    c = screen
    c.result["can_manage"] = False
    await handler.bank_callback(c.callback, c.state)
    await action(c, "add")
    assert c.data[handler.VIEW]["phase"] == "list"
    c.api.mutate_company_bank_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_profile_does_not_trigger_automatic_creation(screen):
    c = screen
    c.result.update(items=[], profile_id=None)
    await handler.bank_callback(c.callback, c.state)
    await action(c, "add")
    assert c.data[handler.VIEW]["phase"] == "list"
    c.api.mutate_company_bank_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_accounts_remain_visible_without_deactivation_button(screen):
    c = screen
    c.result["items"][0]["is_active"] = False
    await handler.bank_callback(c.callback, c.state)
    await action(c, "item", 0)
    assert "Неактивний" in c.sent.await_args.kwargs["text"]
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(":deactivate:" in button.callback_data for row in rows for button in row)


@pytest.mark.asyncio
async def test_navigation_discards_draft_preserving_other_pending_operations(screen):
    c = screen
    await update_review(c)
    c.callback.data = "CP:open"
    await handler.bank_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.data[handler.VIEW] == {}
    assert c.data["construction_project_replace_attempt"] == {"status": "pending"}


@pytest.mark.asyncio
@pytest.mark.parametrize("index,field", list(enumerate(EDIT_FIELDS)))
async def test_compact_field_callback_selects_correct_bank_field(screen, index, field):
    c = screen
    await handler.bank_callback(c.callback, c.state)
    await action(c, "item", 0)
    await action(c, "edit")
    await action(c, "field", index)
    assert c.data[handler.VIEW]["field"] == field
    assert c.data[handler.VIEW]["phase"] == (
        "boolean" if field in handler.FLAGS else "input"
    )


def test_bank_callbacks_fit_telegram_and_translations_have_seven_languages():
    view = {"nonce": "a" * 32}
    for action_name in ("refresh", "previous", "deactivate", "commit", "field"):
        for index in range(len(EDIT_FIELDS)):
            assert len(handler.cb(view, action_name, index).encode()) <= 64
    assert all(len(values) == 7 for values in LABELS.values())
