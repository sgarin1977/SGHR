from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_client_card as cards
from handlers.construction import construction_project_card as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_project_card import project_text
from test_construction_1a_client_card_read_service import card_case
from test_construction_01_client_read_http import client_http
from test_construction_1a_client_card_http import card_http
from test_construction_bot_client_card import body, view_case, click as card_click


@pytest.mark.asyncio
async def test_project_transport_uses_existing_get_and_no_caller_identity_fields(body, card_http):
    item = body["data"]["projects"][0]
    session = PlatformApiClientSession(
        card_http.user, card_http.tenant, SecretStr("test-secret"),
    )
    seen = []

    def transport(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/construction/projects/" + item["id"]
        assert not request.url.query
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert "X-Tenant-ID" not in request.headers
        return httpx.Response(200, json={
            "data": item, "meta": {}, "request_id": "project-card-test",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).project_card(
            session=session, project_id=UUID(item["id"]),
            client_id=card_http.client_id,
        )
    assert result.data["id"] == item["id"]
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["id", "tenant", "client", "schema"])
async def test_unbound_project_response_is_rejected(body, card_http, failure):
    original = body["data"]["projects"][0]
    item = deepcopy(original)
    if failure == "id":
        item["id"] = str(uuid4())
    elif failure == "tenant":
        item["tenant_id"] = str(uuid4())
    elif failure == "client":
        item["client_id"] = str(uuid4())
    else:
        item["status"] = "unknown-private-status"

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": item, "meta": {}, "request_id": "project-card-test",
        })),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).project_card(
                session=PlatformApiClientSession(
                    card_http.user, card_http.tenant, SecretStr("test-secret"),
                ),
                project_id=UUID(original["id"]),
                client_id=card_http.client_id,
            )


@pytest.mark.asyncio
async def test_changed_platform_subject_is_denied_before_project_read(card_http):
    api = NS(project_card=AsyncMock())
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(card_http.user, card_http.tenant, SecretStr("test-secret")),
        {"language": "uk", "name": "Organization"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.project_card(
            telegram_user_id=123, tenant_id=card_http.tenant,
            client_id=card_http.client_id, project_id=uuid4(),
            expected_user_id=uuid4(),
        )
    api.project_card.assert_not_awaited()


@pytest.fixture
def project_case(view_case, monkeypatch):
    c = view_case
    c.project_result = {
        "item": deepcopy(c.result["item"]["projects"][0]),
        "actor_user_id": c.result["actor_user_id"],
        "organization": "Organization", "language": "uk",
    }
    c.service.project_card = AsyncMock(return_value=c.project_result)

    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.project_sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.project_sent)
    return c


async def open_list(c):
    await cards.card_callback(c.callback, c.state)
    await card_click(c, "projects")
    view = c.data[handler.CARD]
    assert view["project_ids"] == [c.project_result["item"]["id"]]
    c.callback.data = "PC:open:" + view["nonce"] + ":0"


@pytest.mark.asyncio
async def test_client_projects_have_short_project_buttons_and_read_fresh_details(project_case):
    c = project_case
    await open_list(c)
    markup = c.sent.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    target = [button for button in buttons if button.callback_data.startswith("PC:open:")]
    assert len(target) == 1
    assert len(target[0].callback_data.encode("utf-8")) <= 64
    await handler.project_callback(c.callback, c.state)
    args = c.service.project_card.await_args.kwargs
    assert args["project_id"] == UUID(c.project_result["item"]["id"])
    assert args["client_id"] == UUID(c.result["item"]["id"])
    assert args["expected_user_id"] == UUID(c.result["actor_user_id"])
    assert c.project_result["item"]["name"] in c.project_sent.await_args.kwargs["text"]
    view = c.data[handler.VIEW]
    assert not {"item", "comment", "name", "address_raw"} & set(view)


@pytest.mark.asyncio
async def test_old_client_page_selector_cannot_retarget_new_page(project_case):
    c = project_case
    await open_list(c)
    old = c.callback.data
    await card_click(c, "refresh")
    c.callback.data = old
    await handler.project_callback(c.callback, c.state)
    c.service.project_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_return_after_message_replacement_preserves_client_page_positions(project_case):
    c = project_case
    await open_list(c)
    c.data[handler.CARD]["positions"]["projects"] = "saved-project-position"
    c.project_sent.return_value = NS(message_id=2)
    await handler.project_callback(c.callback, c.state)
    assert c.data[handler.CARD]["menu_id"] == 2
    c.callback.message.message_id = 2
    await card_click(c, "projects")
    assert c.service.client_card.await_args.kwargs["cursors"]["projects"] == "saved-project-position"


@pytest.mark.asyncio
async def test_refresh_uses_same_project_and_fresh_api_read(project_case):
    c = project_case
    await open_list(c)
    await handler.project_callback(c.callback, c.state)
    first = c.service.project_card.await_args.kwargs
    c.callback.data = "PC:refresh:" + c.data[handler.VIEW]["nonce"]
    await handler.project_callback(c.callback, c.state)
    assert c.service.project_card.await_count == 2
    assert c.service.project_card.await_args.kwargs == first


@pytest.mark.asyncio
async def test_permission_loss_does_not_render_old_project_data(project_case):
    c = project_case
    await open_list(c)
    c.service.project_card.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await handler.project_callback(c.callback, c.state)
    assert c.data[handler.VIEW] == {}
    assert c.project_result["item"]["name"] not in c.project_sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_project_reassigned_to_another_client_is_not_rendered(project_case):
    c = project_case
    await open_list(c)
    c.service.project_card.return_value = {
        **c.project_result,
        "item": {**c.project_result["item"], "client_id": str(uuid4())},
    }
    await handler.project_callback(c.callback, c.state)
    assert c.data[handler.VIEW] == {}
    assert c.project_result["item"]["name"] not in c.project_sent.await_args.kwargs["text"]


@pytest.mark.parametrize("language", ["uk", "ru", "en", "pt", "pl", "de", "nl"])
@pytest.mark.parametrize("status", ["draft", "planning", "active", "completed", "cancelled"])
def test_project_presentation_handles_all_existing_statuses(body, language, status):
    item = {**body["data"]["projects"][0], "status": status}
    result = {
        "item": item,
        "actor_user_id": body["data"]["projects"][0]["created_by"],
        "organization": "Organization",
    }
    text = project_text(result, language)
    assert item["name"] in text
    assert item["address_raw"] in text
    assert item["id"] not in text
    assert "row_version" not in text
    assert len(text) < 3500

# project_action_visibility_v1
@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_client_project_card_only_shows_server_allowed_actions(project_case, allowed):
    c = project_case
    flags = ("can_edit_project", "can_plan_activity", "can_open_team")
    prefixes = ("PDE:open:", "AP:project:", "PT:open:")

    def action_prefixes():
        markup = c.project_sent.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard for button in row
        ]
        return {
            prefix for prefix in prefixes
            if any(value.startswith(prefix) for value in callbacks)
        }

    c.project_result.update({name: allowed for name in flags})
    await open_list(c)
    await handler.project_callback(c.callback, c.state)
    assert action_prefixes() == (set(prefixes) if allowed else set())

    c.project_result.update({name: not allowed for name in flags})
    c.callback.data = "PC:refresh:" + c.data[handler.VIEW]["nonce"]
    await handler.project_callback(c.callback, c.state)
    assert action_prefixes() == (set() if allowed else set(prefixes))
