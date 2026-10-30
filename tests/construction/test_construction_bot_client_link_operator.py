"""Private operator form; API-owned identity, versions and immutable retry."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from handlers.construction import construction_client_link_operator as handler
from handlers.construction import construction_client_card as cards
from handlers.construction import construction_client_catalog as catalog
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_client_link_api_client import ClientLinkQueuedReceipt
from ui.construction_client_link_operator import LABELS

@pytest.fixture(params=["direct", "catalog"])
def screen(request, monkeypatch):
    c = NS(
        actor=uuid4(), tenant=uuid4(), client=uuid4(), target=uuid4(),
        clock=1800000000.0, serial=1,
    )
    c.card = {
        "telegram_user_id": 123, "tenant_id": str(c.tenant),
        "actor_user_id": str(c.actor), "client_id": str(c.client),
        "nonce": uuid4().hex, "menu_id": 1, "section": "summary",
        "positions": {
            "projects": "preserved-project-position",
            "leads": None, "activities": None, "history": None,
        },
        "next_cursors": {
            "projects": None, "leads": None, "activities": None, "history": None,
        },
    }
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        handler.CARD: deepcopy(c.card), "last_menu_message_id": 1,
    }
    if request.param == "catalog":
        nonce = uuid4().hex
        c.data[handler.CARD]["catalog_nonce"] = nonce
        c.data[handler.LIST] = {
            "telegram_user_id": 123, "tenant_id": str(c.tenant),
            "actor_user_id": str(c.actor), "nonce": nonce,
            "menu_id": 1, "screen": "card",
            "client_ids": [str(c.client)], "cursor": "preserved-catalog-position",
        }
    async def update(**values):
        c.data.update(deepcopy(values))
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: deepcopy(c.data)),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    async def answer(*args, **kwargs):
        c.serial += 1
        return NS(message_id=c.serial)
    c.answer = AsyncMock(side_effect=answer)
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.callback = NS(
        from_user=sender, answer=AsyncMock(),
        data="CLO:open:" + c.data[handler.CARD]["nonce"],
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=c.answer),
    )
    c.message = NS(
        from_user=sender, chat=c.callback.message.chat,
        text=None, answer=c.answer,
    )
    c.item = {
        "id": str(c.client), "tenant_id": str(c.tenant),
        "row_version": 7, "linked_user_id": None,
        "display_name": "Private client name",
        "email": "profile-contact@example.com", "phone": "private-contact-value",
    }
    issued = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    c.receipt = ClientLinkQueuedReceipt(
        request_id=uuid4(), tenant_id=c.tenant, client_id=c.client,
        target_user_id=c.target, row_version=1,
        issued_at=issued, expires_at=issued + timedelta(hours=24),
        delivery_status="queued",
    )
    c.service = NS(
        client_card=AsyncMock(side_effect=lambda **kwargs: {
            "item": deepcopy(c.item), "actor_user_id": str(c.actor), "language": "uk",
        }),
        request_client_user_link=AsyncMock(side_effect=lambda **kwargs: {
            "request": c.receipt, "actor_user_id": str(c.actor), "language": "uk",
        }),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "time", lambda: c.clock)
    return c

async def open_form(c):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = "CLO:open:" + c.data[handler.CARD]["nonce"]
    await handler.operator_callback(c.callback, c.state)

async def click(c, action):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.FORM], action)
    await handler.operator_callback(c.callback, c.state)

async def enter(c, value=" PERSON@EXAMPLE.COM "):
    c.message.text = value
    await handler.operator_email(c.message, c.state)

@pytest.mark.asyncio
async def test_open_reads_fresh_base_without_using_profile_email_as_identity(screen):
    c = screen
    await open_form(c)
    form = c.data[handler.FORM]
    assert form["stage"] == "input" and form["email"] is None
    assert form["expected_row_version"] == 7
    for name in ("display_name", "email", "phone"):
        assert c.item[name] not in repr(form)
    c.service.request_client_user_link.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
async def test_review_and_explicit_submit_preserve_version_previous_identity_and_origin(screen, replacement):
    c = screen
    previous = uuid4() if replacement else None
    c.item["linked_user_id"] = str(previous) if previous else None
    await open_form(c)
    await enter(c)
    assert c.data[handler.FORM]["stage"] == "review"
    assert c.data[handler.FORM]["email"] == "person@example.com"
    c.service.request_client_user_link.assert_not_awaited()
    if replacement:
        assert "заміну" in c.answer.await_args.args[0]
    await click(c, "submit")
    args = c.service.request_client_user_link.await_args.kwargs
    assert args["expected_row_version"] == 7
    assert args["previous_user_id"] == previous
    assert args["expected_user_id"] == c.actor
    assert args["tenant_id"] == c.tenant and args["client_id"] == c.client
    assert args["target_email"] == "person@example.com"
    assert "target_user_id" not in args
    assert isinstance(UUID(args["idempotency_key"]), UUID)
    assert c.data[handler.FORM]["stage"] == "queued"
    assert c.data[handler.FORM]["email"] is None
    assert c.data[handler.CARD]["positions"]["projects"] == "preserved-project-position"
    if handler.LIST in c.data:
        assert c.data[handler.LIST]["cursor"] == "preserved-catalog-position"
        assert c.data[handler.LIST]["menu_id"] == c.data["last_menu_message_id"]
    text = c.answer.await_args.args[0]
    assert str(c.target) not in text and str(c.receipt.request_id) not in text
    assert "доставки" in text

@pytest.mark.asyncio
async def test_cancel_creates_no_request(screen):
    c = screen
    await open_form(c)
    await enter(c)
    await click(c, "cancel")
    assert c.data[handler.FORM] == {}
    c.service.request_client_user_link.assert_not_awaited()

@pytest.mark.asyncio
async def test_duplicate_submit_does_not_create_another_request(screen):
    c = screen
    await open_form(c)
    await enter(c)
    await click(c, "submit")
    await click(c, "submit")
    c.service.request_client_user_link.assert_awaited_once()

@pytest.mark.asyncio
async def test_uncertain_retry_keeps_exact_email_version_previous_identity_and_key(screen):
    c = screen
    c.item["linked_user_id"] = str(uuid4())
    await open_form(c)
    await enter(c)
    c.service.request_client_user_link.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"request": c.receipt, "actor_user_id": str(c.actor), "language": "uk"},
    ]
    await click(c, "submit")
    frozen = deepcopy(c.data[handler.FORM])
    assert frozen["stage"] == "pending"
    await click(c, "edit")
    assert c.data[handler.FORM]["payload"] == frozen["payload"]
    c.service.client_card.side_effect = AssertionError("Retry must not rebase the card")
    await click(c, "submit")
    calls = c.service.request_client_user_link.await_args_list
    assert len(calls) == 2 and calls[0].kwargs == calls[1].kwargs
    assert c.data[handler.FORM]["stage"] == "queued"

@pytest.mark.asyncio
async def test_navigation_keeps_pending_request_and_reopen_does_not_reread_card(screen):
    c = screen
    await open_form(c)
    await enter(c)
    c.service.request_client_user_link.side_effect = ConstructionApiClientError(
        status_code=503, code="service_unavailable",
    )
    await click(c, "submit")
    saved = deepcopy(c.data[handler.FORM])
    c.callback.data = "CC:summary:" + c.data[handler.CARD]["nonce"]
    await handler.navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.data[handler.FORM]["menu_id"] is None
    assert c.data[handler.FORM]["key"] == saved["key"]
    c.data[handler.CARD]["nonce"] = uuid4().hex
    c.service.client_card.side_effect = AssertionError("Resume must not rebase")
    await open_form(c)
    assert c.data[handler.FORM]["stage"] == "pending"
    assert c.data[handler.FORM]["payload"] == saved["payload"]
    assert c.data[handler.FORM]["key"] == saved["key"]

@pytest.mark.asyncio
async def test_expired_retry_never_posts_old_command_and_new_draft_is_explicit(screen):
    c = screen
    await open_form(c)
    await enter(c)
    c.service.request_client_user_link.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "submit")
    c.clock += handler.SAFE_RETRY_SECONDS
    await click(c, "submit")
    assert c.data[handler.FORM]["stage"] == "expired"
    c.service.request_client_user_link.assert_awaited_once()
    c.item["row_version"] = 8
    await click(c, "new")
    assert c.data[handler.FORM]["stage"] == "input"
    assert c.data[handler.FORM]["expected_row_version"] == 8
    assert c.data[handler.FORM]["key"] is None
    c.service.request_client_user_link.assert_awaited_once()

@pytest.mark.asyncio
async def test_missing_verified_email_can_be_corrected_without_automatic_post(screen):
    c = screen
    await open_form(c)
    await enter(c)
    c.service.request_client_user_link.side_effect = ConstructionApiClientError(
        status_code=404, code="PLATFORM_USER_NOT_FOUND",
    )
    await click(c, "submit")
    assert c.data[handler.FORM]["stage"] == "not_found"
    await click(c, "edit")
    await enter(c, "other@example.com")
    c.service.request_client_user_link.assert_awaited_once()
    assert c.data[handler.FORM]["email"] == "other@example.com"
    assert c.data[handler.FORM]["expected_row_version"] == 7
    assert c.data[handler.FORM]["key"] is None

@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,stage", [
    (403, "forbidden", "blocked"),
    (409, "ROW_VERSION_CONFLICT", "conflict"),
    (409, "idempotency_in_progress", "pending"),
    (429, "RATE_LIMIT_EXCEEDED", "pending"),
])
async def test_domain_errors_have_no_automatic_retry(screen, status, code, stage):
    c = screen
    await open_form(c)
    await enter(c)
    c.service.request_client_user_link.side_effect = ConstructionApiClientError(
        status_code=status, code=code,
    )
    await click(c, "submit")
    assert c.data[handler.FORM]["stage"] == stage
    assert c.data[handler.FORM]["expected_row_version"] == 7
    c.service.request_client_user_link.assert_awaited_once()

@pytest.mark.asyncio
async def test_changed_actor_before_submit_is_rejected_before_api(screen):
    c = screen
    await open_form(c)
    await enter(c)
    c.data[handler.CARD]["actor_user_id"] = str(uuid4())
    await click(c, "submit")
    c.service.request_client_user_link.assert_not_awaited()

@pytest.mark.asyncio
async def test_navigation_race_cannot_install_stale_form(screen):
    c = screen
    async def raced(**kwargs):
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
        return {"item": c.item, "actor_user_id": str(c.actor), "language": "uk"}
    c.service.client_card.side_effect = raced
    await open_form(c)
    assert not c.data.get(handler.FORM)
    c.service.request_client_user_link.assert_not_awaited()

@pytest.mark.asyncio
async def test_draft_is_suspended_by_navigation_and_invalid_email_never_looks_up_identity(screen):
    c = screen
    await open_form(c)
    await enter(c, "not-an-email")
    assert c.data[handler.FORM]["stage"] == "input"
    c.service.request_client_user_link.assert_not_awaited()
    c.callback.data = "CS_HOME"
    await handler.navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.data[handler.FORM] == {}

@pytest.mark.asyncio
async def test_catalog_navigation_preserves_origin_for_operator_callbacks(screen):
    c = screen
    c.callback.data = "CLO:open:" + c.data[handler.CARD]["nonce"]
    before = deepcopy(c.data.get(handler.LIST))
    await catalog.client_catalog_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.data.get(handler.LIST) == before

def test_seven_languages_and_bounded_callback_data():
    assert all(len(values) == 7 for values in LABELS.values())
    form = {"nonce": "a" * 32}
    assert all(
        len(handler.cb(form, action).encode("utf-8")) <= 64
        for action in ("submit", "edit", "cancel", "new")
    )

# client_link_operator_bot_v1
