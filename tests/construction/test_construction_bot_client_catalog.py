"""Client catalog API transport, CRM hub and preserved card origin."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Response
from pydantic import SecretStr

from handlers.construction import construction_client_catalog as handler
from handlers.construction import construction_client_card as cards
from handlers.construction import construction_project_card as projects
from api.routes import construction_navigation as navigation
from api.routes.construction_client_read import ClientReadResponse
from api.routes.construction_project_read import ProjectReadResponse
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from ui.construction_client_catalog import LABELS
from ui.construction_client_card import SECTIONS
from test_construction_client_catalog import client_catalog_http, client_catalog_case
from test_construction_1a_client_card_read_service import card_case


@pytest.fixture
def screen(client_catalog_http, monkeypatch):
    c = client_catalog_http
    c.base = ClientReadResponse.model_validate({
        name: getattr(c.client_row, name) for name in ClientReadResponse.model_fields
    }).model_dump(mode="json")
    c.base["phone"] = "+12025550123"
    c.project_item = ProjectReadResponse.model_validate({
        name: getattr(c.project, name) for name in ProjectReadResponse.model_fields
    }).model_dump(mode="json")
    c.page = {
        "items": [deepcopy(c.base)], "next_cursor": None,
        "actor_user_id": str(c.user), "organization": "Organization",
        "language": "uk", "can_create_client": False,
    }
    c.detail = {
        "item": {
            **deepcopy(c.base), "projects": [deepcopy(c.project_item)],
            "leads": [], "activities": [], "history": [],
        },
        "actor_user_id": str(c.user), "organization": "Organization", "language": "uk",
        "next_cursors": {section: None for section in SECTIONS},
    }
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
        data="CL:open", from_user=NS(id=123, is_bot=False, language_code="uk"),
        answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.ui_service = NS(
        client_catalog=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.page)),
        client_card=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.detail)),
        project_card=AsyncMock(return_value={
            "item": c.project_item, "actor_user_id": str(c.user),
            "organization": "Organization", "language": "uk",
        }),
        home=AsyncMock(return_value={
            "name": "Organization", "language": "uk", "can_read_leads": True,
            "can_read_clients": True, "can_read_projects": True,
        }),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.ui_service
    c.sent = AsyncMock(return_value=NS(message_id=1))
    for module in (handler, cards, projects):
        monkeypatch.setattr(module, "construction_bot_api_entry", entry)
        monkeypatch.setattr(module, "edit_or_replace_menu_message", c.sent)
    return c


async def list_click(c, action):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = f'CL:{action}:{c.data[handler.LIST]["nonce"]}'
    await handler.catalog_callback(c.callback, c.state)


async def open_client(c):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = f'CC:catalog:{c.data[handler.LIST]["nonce"]}:0'
    await cards.card_callback(c.callback, c.state)


async def card_click(c, action):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = cards.cb(c.data[cards.VIEW], action)
    await cards.card_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_catalog_selection_pins_actor_and_stores_no_client_content(screen):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    await open_client(c)
    args = c.ui_service.client_card.await_args.kwargs
    assert args["client_id"] == c.client and args["expected_user_id"] == c.user
    assert c.data[cards.VIEW]["catalog_nonce"] == c.data[handler.LIST]["nonce"]
    assert c.base["display_name"] not in repr(c.data)
    assert c.base["phone"] not in repr(c.data[cards.VIEW])
    assert c.base["phone"] not in repr(c.data)
    private_fields = {
        "display_name", "phone", "email", "notes", "linked_user_id",
        "item", "payload", "access_token", "refresh_token",
    }
    assert not private_fields.intersection(c.data[cards.VIEW])
    assert not private_fields.intersection(c.data[handler.LIST])
    assert c.data[handler.LIST]["screen"] == "card"


@pytest.mark.asyncio
async def test_back_from_card_keeps_catalog_page_and_independent_card_sections(screen):
    c = screen
    c.page["next_cursor"] = "opaque-next"
    await handler.catalog_callback(c.callback, c.state)
    c.page["next_cursor"] = None
    await list_click(c, "next")
    await open_client(c)
    await card_click(c, "projects")
    await card_click(c, "history")
    await list_click(c, "back")
    assert c.ui_service.client_catalog.await_args.kwargs["cursor"] == "opaque-next"
    assert c.data[handler.LIST]["screen"] == "list"


@pytest.mark.asyncio
async def test_project_message_replacement_preserves_client_catalog_origin(screen):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    await open_client(c)
    await card_click(c, "projects")
    c.sent.return_value = NS(message_id=2)
    c.callback.data = "PC:open:" + c.data[cards.VIEW]["nonce"] + ":0"
    await projects.project_callback(c.callback, c.state)
    assert c.data[handler.LIST]["menu_id"] == 2
    assert c.data[cards.VIEW]["menu_id"] == 2
    c.callback.message.message_id = 2
    await card_click(c, "summary")
    await list_click(c, "back")
    assert c.data[handler.LIST]["screen"] == "list"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "menu", "sender", "nonce"])
async def test_stale_or_foreign_selector_never_opens_client_card(screen, failure):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    token = c.data[handler.LIST]["nonce"]
    if failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.data[handler.LIST]["menu_id"] = 99
    elif failure == "sender":
        c.callback.from_user.id = c.callback.message.chat.id = 999
    else:
        token = "obsolete"
    c.callback.data = "CC:catalog:" + token + ":0"
    await cards.card_callback(c.callback, c.state)
    c.ui_service.client_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_hub_uses_current_server_capabilities(screen):
    c = screen
    c.callback.data = "CM:open"
    c.ui_service.home.return_value["can_read_clients"] = False
    await handler.catalog_callback(c.callback, c.state)
    commands = [
        value.callback_data
        for row in c.sent.await_args.kwargs["reply_markup"].inline_keyboard
        for value in row
    ]
    assert "CS_LEADS" in commands and "PL:open" in commands
    assert "CL:open" not in commands


@pytest.mark.asyncio
async def test_denied_refresh_does_not_keep_catalog_or_private_text(screen):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    c.ui_service.client_catalog.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await list_click(c, "refresh")
    assert c.data[handler.LIST] == {} and c.data[cards.VIEW] == {}
    assert c.base["display_name"] not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_navigation_clears_catalog_without_touching_pending_commands(screen):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    c.data["construction_project_replace_attempt"] = {"status": "pending", "id": "keep"}
    c.callback.data = "CS_HOME"
    await handler.client_catalog_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.data[handler.LIST] == {}
    assert c.data["construction_project_replace_attempt"]["id"] == "keep"


@pytest.mark.asyncio
async def test_transport_reads_registered_catalog(client_catalog_http):
    c = client_catalog_http
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        result = await ConstructionApiClient(http=http).client_catalog(
            session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
        )
    assert result.data[0]["id"] == str(c.client)
    assert result.meta == {"pagination": {"next_cursor": None}}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "duplicate", "version", "private", "cursor"])
async def test_transport_rejects_unbound_or_invalid_catalog_data(screen, failure):
    c = screen
    body = {
        "data": [deepcopy(c.base)],
        "meta": {"pagination": {"next_cursor": None}}, "request_id": "local",
    }
    if failure == "tenant":
        body["data"][0]["tenant_id"] = str(uuid4())
    elif failure == "duplicate":
        body["data"] *= 2
    elif failure == "version":
        body["data"][0]["row_version"] = True
    elif failure == "private":
        body["data"][0]["private"] = "hidden"
    else:
        body["meta"]["pagination"]["next_cursor"] = True
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).client_catalog(
                session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
            )


@pytest.mark.asyncio
async def test_subject_change_stops_before_client_catalog_read():
    user, tenant = uuid4(), uuid4()
    api = NS(client_catalog=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"can_read_clients": True},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.client_catalog(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=uuid4(),
        )
    api.client_catalog.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope,allowed", [
    ("OWNER", "tenant", True), ("ADMIN", "tenant", True),
    ("PROJECT_MANAGER", "project", False), ("OBSERVER", "document", False),
])
async def test_client_menu_capability_keeps_tenant_crm_boundary(role, scope, allowed):
    tenant, user = uuid4(), uuid4()
    result = await navigation.navigation_context(
        request=NS(query_params={}, state=NS(request_id="client-catalog-navigation")),
        response=Response(), actor=NS(user_id=user, tenant_id=tenant, status="active"),
        context=ConstructionAccessContext(
            user, tenant, (role,), tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
            (ConstructionGrantContext(role, scope, tenant if scope == "tenant" else uuid4()),),
        ),
        access=NS(has_active_platform_membership=AsyncMock(return_value=True)),
        organizations=NS(require_current=AsyncMock(return_value=NS(
            tenant_id=tenant, name="Organization", default_currency="EUR",
        ))),
    )
    assert result["data"]["can_read_clients"] is allowed


def test_all_labels_have_seven_languages():
    assert all(len(values) == 7 for values in LABELS.values())
