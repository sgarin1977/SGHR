from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_client_card as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_client_card import SECTIONS, card_text
from test_construction_1a_client_card_read_service import card_case
from test_construction_01_client_read_http import client_http, get
from test_construction_1a_client_card_http import card_http


@pytest.fixture
async def body(card_http):
    response = await get(
        card_http,
        params={section + "_limit": 5 for section in SECTIONS},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_transport_uses_flat_real_contract_and_independent_positions(body, card_http):
    session = PlatformApiClientSession(card_http.user, card_http.tenant, SecretStr("test-secret"))
    seen = []

    def transport(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/construction/clients/{card_http.client_id}"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert "X-Tenant-ID" not in request.headers
        params = dict(request.url.params)
        assert params.pop("projects_cursor") == "projects-position"
        assert params.pop("history_cursor") == "history-position"
        assert params == {section + "_limit": "5" for section in SECTIONS}
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).client_card(
            session=session, client_id=card_http.client_id,
            cursors={"projects": "projects-position", "history": "history-position"},
        )
    assert "client" not in result.data
    assert all(len(result.data[section]) == 1 for section in SECTIONS)
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "client", "project_tenant", "project_client", "lead_client",
    "activity_tenant", "history_tenant", "history_pair",
    "duplicate", "cursor",
])
async def test_transport_rejects_unbound_card_data(body, card_http, failure):
    bad = deepcopy(body)
    data = bad["data"]
    if failure == "client":
        data["id"] = str(uuid4())
    elif failure == "project_tenant":
        data["projects"][0]["tenant_id"] = str(uuid4())
    elif failure == "project_client":
        data["projects"][0]["client_id"] = str(uuid4())
    elif failure == "lead_client":
        data["leads"][0]["converted_client_id"] = str(uuid4())
    elif failure == "activity_tenant":
        data["activities"][0]["tenant_id"] = str(uuid4())
    elif failure == "history_tenant":
        data["history"][0]["tenant_id"] = str(uuid4())
    elif failure == "history_pair":
        data["history"][0]["event_type"] = "admin_login"
    elif failure == "duplicate":
        data["projects"].append(deepcopy(data["projects"][0]))
    else:
        bad["meta"]["pagination"]["projects_next_cursor"] = ""
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=bad)),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).client_card(
                session=PlatformApiClientSession(
                    card_http.user, card_http.tenant, SecretStr("test-secret"),
                ),
                client_id=card_http.client_id,
            )


@pytest.mark.asyncio
async def test_adapter_rejects_changed_platform_subject_before_card_read(card_http):
    session = PlatformApiClientSession(card_http.user, card_http.tenant, SecretStr("test-secret"))
    api = NS(client_card=AsyncMock())
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(session, {"name": "Organization", "language": "uk"}))
    with pytest.raises(ConstructionApiClientError):
        await entry.client_card(
            telegram_user_id=123, tenant_id=card_http.tenant,
            client_id=card_http.client_id, expected_user_id=uuid4(),
        )
    api.client_card.assert_not_awaited()


@pytest.fixture
def view_case(monkeypatch, body, card_http):
    c = NS()
    c.data = {
        handler.CONTEXT: {
            "telegram_user_id": 123, "tenant_id": str(card_http.tenant),
        },
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
        from_user=sender, answer=AsyncMock(),
        message=NS(
            message_id=1, chat=NS(id=123, type="private"),
            answer=AsyncMock(),
        ),
        data="CC:open:" + card_http.client_id.hex,
    )
    c.result = {
        "item": deepcopy(body["data"]),
        "actor_user_id": str(card_http.user),
        "organization": "Organization", "language": "uk",
        "next_cursors": {
            section: section + "-page-2" for section in SECTIONS
        },
    }
    c.service = NS(client_card=AsyncMock(return_value=c.result))
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    for name in (
        "suspend_lead_form", "suspend_activity_form",
        "suspend_terminal_form", "suspend_conversion_form",
    ):
        monkeypatch.setattr(handler, name, AsyncMock())
    return c


async def click(c, action):
    c.callback.data = handler.cb(c.data[handler.VIEW], action)
    await handler.card_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_four_lists_keep_independent_positions_without_payloads_in_fsm(view_case):
    c = view_case
    await handler.card_callback(c.callback, c.state)
    await click(c, "projects")
    await click(c, "next")
    await click(c, "history")
    await click(c, "next")
    positions = c.service.client_card.await_args.kwargs["cursors"]
    assert positions == {
        "projects": "projects-page-2", "history": "history-page-2",
        "leads": None, "activities": None,
    }
    await click(c, "first")
    positions = c.service.client_card.await_args.kwargs["cursors"]
    assert positions["history"] is None
    assert positions["projects"] == "projects-page-2"
    view = c.data[handler.VIEW]
    assert not {"item", "payload", "display_name", "phone", "notes"} & set(view)
    assert c.result["item"]["display_name"] not in repr(view)


@pytest.mark.asyncio
async def test_old_page_callback_is_rejected_before_another_api_read(view_case):
    c = view_case
    await handler.card_callback(c.callback, c.state)
    old = handler.cb(c.data[handler.VIEW], "projects")
    await click(c, "history")
    count = c.service.client_card.await_count
    c.callback.data = old
    await handler.card_callback(c.callback, c.state)
    assert c.service.client_card.await_count == count


@pytest.mark.asyncio
async def test_denied_next_page_does_not_render_cached_client_data(view_case):
    c = view_case
    await handler.card_callback(c.callback, c.state)
    c.service.client_card.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "history")
    assert c.data[handler.VIEW] == {}
    assert c.result["item"]["display_name"] not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_other_telegram_sender_cannot_reuse_card(view_case):
    c = view_case
    await handler.card_callback(c.callback, c.state)
    c.callback.from_user.id = 999
    c.callback.message.chat.id = 999
    c.service.client_card.reset_mock()
    await click(c, "history")
    c.service.client_card.assert_not_awaited()


@pytest.mark.parametrize("language", ["uk", "ru", "en", "pt", "pl", "de", "nl"])
@pytest.mark.parametrize("section", ["summary", *SECTIONS])
def test_card_sections_render_without_raw_history_payloads(body, language, section):
    result = {"item": body["data"], "organization": "Organization"}
    text = card_text(result, section, language)
    assert 0 < len(text) < 3500
    assert "related_client_id" not in text
    assert "old_value" not in text

# Client self identity bot regression v1.
@pytest.fixture
def identity_case(view_case):
    c = view_case
    c.result["item"].update(linked_user_id=None, row_version=1)
    c.link_receipt = {
        "item": {
            "client_id": c.result["item"]["id"],
            "tenant_id": c.result["item"]["tenant_id"],
            "linked_user_id": c.result["actor_user_id"],
            "row_version": 2, "changed": True,
        },
        "actor_user_id": c.result["actor_user_id"], "language": "uk",
    }
    c.service.self_link_client = AsyncMock(return_value=c.link_receipt)
    return c


async def identity_review(c):
    await handler.card_callback(c.callback, c.state)
    await click(c, "identity")
    await click(c, "identity_review")
    assert c.data[handler.VIEW]["section"] == "identity_review"


@pytest.mark.asyncio
async def test_self_link_requires_review_and_consumes_confirmation_nonce(identity_case):
    from uuid import UUID
    c = identity_case
    await identity_review(c)
    c.service.self_link_client.assert_not_awaited()
    old_callback = handler.cb(c.data[handler.VIEW], "identity_submit")
    await click(c, "identity_submit")
    args = c.service.self_link_client.await_args.kwargs
    assert args["expected_user_id"] == UUID(c.result["actor_user_id"])
    assert args["expected_row_version"] == 1
    assert args["previous_user_id"] is None
    assert c.data[handler.VIEW]["section"] == "identity_result"
    assert "identity_version" not in c.data[handler.VIEW]
    c.callback.data = old_callback
    await handler.card_callback(c.callback, c.state)
    c.service.self_link_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_identity_replacement_is_explicit_and_binds_previous_user(identity_case):
    from uuid import UUID
    c = identity_case
    previous = str(uuid4())
    c.result["item"]["linked_user_id"] = previous
    await identity_review(c)
    assert c.data[handler.VIEW]["identity_previous"] == previous
    assert "замінено" in c.sent.await_args.kwargs["text"].lower()
    c.service.self_link_client.assert_not_awaited()
    await click(c, "identity_submit")
    assert c.service.self_link_client.await_args.kwargs["previous_user_id"] == UUID(previous)


@pytest.mark.asyncio
async def test_already_own_link_offers_no_new_confirmation(identity_case):
    c = identity_case
    c.result["item"]["linked_user_id"] = c.result["actor_user_id"]
    await handler.card_callback(c.callback, c.state)
    await click(c, "identity")
    await click(c, "identity_review")
    assert c.data[handler.VIEW]["section"] == "identity"
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(
        ":identity_submit:" in (button.callback_data or "")
        for row in rows for button in row
    )
    await click(c, "identity_submit")
    c.service.self_link_client.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 409, 503])
async def test_denied_conflicting_and_uncertain_writes_have_no_automatic_retry(identity_case, status):
    c = identity_case
    await identity_review(c)
    c.service.self_link_client.side_effect = ConstructionApiClientError(
        status_code=status, code="local-failure",
    )
    await click(c, "identity_submit")
    c.service.self_link_client.assert_awaited_once()
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(
        ":identity_submit:" in (button.callback_data or "")
        for row in rows for button in row
    )
    if status == 503:
        c.result["item"].update(
            linked_user_id=c.result["actor_user_id"], row_version=2,
        )
    await click(c, "identity")
    assert c.data[handler.VIEW]["section"] == "identity"
    c.service.self_link_client.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "menu", "nonce"])
async def test_stale_self_identity_confirmation_never_writes(identity_case, failure):
    c = identity_case
    await identity_review(c)
    if failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.data["last_menu_message_id"] = 999
    else:
        c.data[handler.VIEW]["nonce"] = "changed"
        c.callback.data = "CC:identity_submit:obsolete"
        await handler.card_callback(c.callback, c.state)
        c.service.self_link_client.assert_not_awaited()
        return
    await click(c, "identity_submit")
    c.service.self_link_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_self_identity_draft_keeps_only_metadata(identity_case):
    c = identity_case
    await identity_review(c)
    view = c.data[handler.VIEW]
    assert "identity_version" in view
    assert not {"item", "payload", "display_name", "phone", "email", "notes"} & set(view)
    assert c.result["item"]["display_name"] not in repr(view)
    assert all(
        len(handler.cb(view, action).encode("utf-8")) <= 64
        for action in ("identity", "identity_review", "identity_submit")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
async def test_self_link_transport_uses_only_registered_body_without_replay_key(replacement):
    import json
    from uuid import UUID
    user, tenant, client_id = uuid4(), uuid4(), uuid4()
    previous = uuid4() if replacement else None
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": {
                "client_id": str(client_id), "tenant_id": str(tenant),
                "linked_user_id": str(user), "row_version": 8, "changed": True,
            }, "meta": {}, "request_id": "self-link-local",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).self_link_client(
            session=PlatformApiClientSession(user, tenant, SecretStr("local")),
            client_id=client_id, expected_row_version=7, previous_user_id=previous,
        )
    body = {"expected_row_version": 7}
    if replacement:
        body["previous_user_id"] = str(previous)
    assert calls[0].method == "POST"
    assert calls[0].url.path == f"/api/v1/construction/clients/{client_id}/user-link/self"
    assert not calls[0].url.query
    assert "Idempotency-Key" not in calls[0].headers
    assert calls[0].headers["Authorization"] == "Bearer local"
    assert json.loads(calls[0].content) == body
    assert result.data["linked_user_id"] == str(user)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["user", "tenant", "version", "extra"])
async def test_self_link_transport_rejects_unbound_receipts(failure):
    user, tenant, client_id = uuid4(), uuid4(), uuid4()
    item = {
        "client_id": str(client_id), "tenant_id": str(tenant),
        "linked_user_id": str(user), "row_version": 2, "changed": True,
    }
    if failure == "user":
        item["linked_user_id"] = str(uuid4())
    elif failure == "tenant":
        item["tenant_id"] = str(uuid4())
    elif failure == "version":
        item["row_version"] = 3
    else:
        item["token"] = "must-not-be-returned"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": item, "meta": {}, "request_id": "local",
        })),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).self_link_client(
                session=PlatformApiClientSession(user, tenant, SecretStr("local")),
                client_id=client_id, expected_row_version=1,
            )


@pytest.mark.asyncio
async def test_self_link_entry_pins_actor_before_mutation():
    user, tenant = uuid4(), uuid4()
    api = NS(self_link_client=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")), {"language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.self_link_client(
            telegram_user_id=123, tenant_id=tenant, client_id=uuid4(),
            expected_user_id=uuid4(), expected_row_version=1,
        )
    api.self_link_client.assert_not_awaited()


def test_self_identity_translations_have_seven_languages():
    from ui.construction_client_card import LABELS
    assert all(len(values) == 7 for values in LABELS.values())

# Client unlink bot regression v1.
@pytest.fixture
def unlink_case(identity_case):
    c = identity_case
    c.result["item"].update(
        linked_user_id=c.result["actor_user_id"], row_version=7,
    )
    c.service.own_client_link_state = AsyncMock(return_value={
        "item": {
            "client_id": c.result["item"]["id"],
            "linked_user_id": c.result["actor_user_id"], "row_version": 7,
        },
        "actor_user_id": c.result["actor_user_id"], "language": "uk",
    })
    c.service.unlink_client = AsyncMock(return_value={
        "item": {
            "client_id": c.result["item"]["id"], "linked_user_id": None,
            "row_version": 8, "changed": True,
        },
        "actor_user_id": c.result["actor_user_id"], "language": "uk",
    })
    return c


async def unlink_review(c):
    await handler.card_callback(c.callback, c.state)
    await click(c, "identity")
    c.service.client_card.reset_mock()
    await click(c, "identity_unlink")
    assert c.data[handler.VIEW]["section"] == "identity_unlink_review"


@pytest.mark.asyncio
async def test_owner_unlink_uses_own_state_without_card_read_and_requires_review(unlink_case):
    from uuid import UUID
    c = unlink_case
    await unlink_review(c)
    c.service.client_card.assert_not_awaited()
    c.service.own_client_link_state.assert_awaited_once()
    c.service.unlink_client.assert_not_awaited()
    stale = handler.cb(c.data[handler.VIEW], "identity_unlink_submit")
    await click(c, "identity_unlink_submit")
    args = c.service.unlink_client.await_args.kwargs
    assert args["expected_row_version"] == 7
    assert args["expected_user_id"] == UUID(c.result["actor_user_id"])
    assert c.data[handler.VIEW]["section"] == "identity_unlink_result"
    assert "unlink_version" not in c.data[handler.VIEW]
    c.callback.data = stale
    await handler.card_callback(c.callback, c.state)
    c.service.unlink_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_editor_unlink_reviews_fresh_card_and_preserves_reviewed_version(unlink_case):
    c = unlink_case
    c.result["item"]["linked_user_id"] = str(uuid4())
    await unlink_review(c)
    c.service.client_card.assert_awaited_once()
    c.service.own_client_link_state.assert_not_awaited()
    assert c.data[handler.VIEW]["unlink_owner"] is False
    c.result["item"]["row_version"] = 99
    await click(c, "identity_unlink_submit")
    assert c.service.unlink_client.await_args.kwargs["expected_row_version"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 409, 503])
async def test_unlink_failure_never_offers_automatic_write_retry(unlink_case, status):
    c = unlink_case
    await unlink_review(c)
    c.service.unlink_client.side_effect = ConstructionApiClientError(
        status_code=status, code="local-failure",
    )
    await click(c, "identity_unlink_submit")
    c.service.unlink_client.assert_awaited_once()
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(
        ":identity_unlink_submit:" in (button.callback_data or "")
        for row in rows for button in row
    )
    c.service.own_client_link_state.side_effect = ConstructionApiClientError(
        status_code=404, code="not_found",
    )
    await click(c, "identity_own")
    c.service.unlink_client.assert_awaited_once()
    assert c.result["item"]["display_name"] not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_owner_changed_before_review_does_not_fall_back_to_editor(unlink_case):
    c = unlink_case
    await handler.card_callback(c.callback, c.state)
    await click(c, "identity")
    c.service.client_card.reset_mock()
    c.service.own_client_link_state.side_effect = ConstructionApiClientError(
        status_code=404, code="not_found",
    )
    await click(c, "identity_unlink")
    c.service.client_card.assert_not_awaited()
    c.service.unlink_client.assert_not_awaited()
    assert c.data[handler.VIEW]["section"] == "identity_unlink_result"


@pytest.mark.asyncio
async def test_changed_tenant_blocks_unlink_confirmation(unlink_case):
    c = unlink_case
    await unlink_review(c)
    c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    await click(c, "identity_unlink_submit")
    c.service.unlink_client.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "unlink"])
async def test_identity_only_entry_does_not_call_membership_navigation(operation):
    from services.construction.construction_api_client import ConstructionApiResult
    user, tenant, client_id = uuid4(), uuid4(), uuid4()
    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    item = (
        {"client_id": str(client_id), "linked_user_id": str(user), "row_version": 7}
        if operation == "read" else
        {"client_id": str(client_id), "linked_user_id": None, "row_version": 8, "changed": True}
    )
    api = NS(
        own_client_link_state=AsyncMock(return_value=ConstructionApiResult(item, {}, "local")),
        unlink_client=AsyncMock(return_value=ConstructionApiResult(item, {}, "local")),
    )
    sessions = NS(get=AsyncMock(return_value=session))
    entry = ConstructionBotApiEntry(
        api=NS(construction=api, own_client_link_state=api.own_client_link_state),
        context=NS(), sessions=sessions,
    )
    entry._identity = lambda telegram_user_id: NS(language_code="uk")
    entry._presentation = AsyncMock(side_effect=AssertionError("No navigation for own unlink."))
    values = dict(
        telegram_user_id=123, tenant_id=tenant, client_id=client_id,
        expected_user_id=user,
    )
    if operation == "read":
        result = await entry.own_client_link_state(**values)
    else:
        result = await entry.unlink_client(**values, expected_row_version=7)
    assert result["actor_user_id"] == str(user)
    entry._presentation.assert_not_awaited()
    if operation == "read":
        sessions.get.assert_not_awaited()
        api.own_client_link_state.assert_awaited_once_with(
            client_id=client_id, expected_user_id=user,
        )
    else:
        sessions.get.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "foreign_user", "extra"])
async def test_own_link_transport_is_narrow_and_identity_bound(failure):
    user, tenant, client_id = uuid4(), uuid4(), uuid4()
    item = {"client_id": str(client_id), "linked_user_id": str(user), "row_version": 7}
    if failure == "foreign_user":
        item["linked_user_id"] = str(uuid4())
    elif failure == "extra":
        item["display_name"] = "must-not-be-exposed"
    def transport(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/construction/clients/{client_id}/user-link/self"
        assert not request.url.query
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "local"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        async def call():
            return await ConstructionApiClient(http=http).own_client_link_state(
                session=PlatformApiClientSession(user, tenant, SecretStr("local")),
                client_id=client_id,
            )
        if failure:
            with pytest.raises(ConstructionApiClientError):
                await call()
        else:
            assert (await call()).data == item


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "version", "client", "linked"])
async def test_unlink_transport_uses_only_version_without_replay_key(failure):
    import json
    user, tenant, client_id = uuid4(), uuid4(), uuid4()
    item = {
        "client_id": str(client_id), "linked_user_id": None,
        "row_version": 8, "changed": True,
    }
    if failure == "version":
        item["row_version"] = 9
    elif failure == "client":
        item["client_id"] = str(uuid4())
    elif failure == "linked":
        item["linked_user_id"] = str(user)
    def transport(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/construction/clients/{client_id}/user-link/unlink"
        assert not request.url.query
        assert "Idempotency-Key" not in request.headers
        assert json.loads(request.content) == {"expected_row_version": 7}
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "local"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        async def call():
            return await ConstructionApiClient(http=http).unlink_client(
                session=PlatformApiClientSession(user, tenant, SecretStr("local")),
                client_id=client_id, expected_row_version=7,
            )
        if failure:
            with pytest.raises(ConstructionApiClientError):
                await call()
        else:
            assert (await call()).data == item


def test_unlink_callbacks_fit_telegram_limit():
    from ui.construction_client_card import LABELS
    view = {"nonce": "a" * 32}
    for action in ("identity_unlink", "identity_unlink_submit", "identity_own"):
        assert len(handler.cb(view, action).encode("utf-8")) <= 64
    assert all(len(values) == 7 for values in LABELS.values())
