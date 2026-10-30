"""Direct catalog navigation, selector isolation and shared API integration."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Response
from pydantic import SecretStr

from handlers.construction import construction_project_catalog as handler
from handlers.construction import construction_project_areas as areas
from handlers.construction import construction_project_team as team
from api.routes import construction_navigation as navigation
from api.routes.construction_project_read import ProjectReadResponse
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from ui.construction_project_catalog import LABELS
from test_construction_project_catalog_http import catalog_http
from test_construction_project_catalog_read import catalog_case
from test_construction_1a_client_projects_read_service import service_case


@pytest.fixture
def screen(catalog_http, monkeypatch):
    c = catalog_http
    c.item = ProjectReadResponse.model_validate({
        name: getattr(c.row, name) for name in ProjectReadResponse.model_fields
    }).model_dump(mode="json")
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
        data="PL:open", from_user=NS(id=123, is_bot=False, language_code="uk"),
        answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.page = {
        "items": [deepcopy(c.item)], "next_cursor": None,
        "actor_user_id": str(c.user), "organization": "Organization",
        "language": "uk", "can_create_project": False,
    }
    c.card = {
        "item": deepcopy(c.item), "actor_user_id": str(c.user),
        "organization": "Organization", "language": "uk", "can_open_team": True,
    }
    c.ui_service = NS(
        project_catalog=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.page)),
        project_catalog_card=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.card)),
        project_team=AsyncMock(return_value={
            "items": [], "project_id": str(c.row.id), "actor_user_id": str(c.user),
            "project_name": c.row.name, "organization": "Organization",
            "language": "uk", "can_add": False,
        }),
        project_areas=AsyncMock(return_value={
            "items": [], "project_id": str(c.row.id), "actor_user_id": str(c.user),
            "project_name": c.row.name, "organization": "Organization",
            "language": "uk", "can_create_area": False,
        }),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.ui_service
    for module in (handler, team, areas):
        monkeypatch.setattr(module, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    for module in (handler, team, areas):
        monkeypatch.setattr(module, "edit_or_replace_menu_message", c.sent)
    return c


async def click(c, action, value=None, card=False):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    source = c.data[handler.CARD if card else handler.LIST]
    c.callback.data = f'PL:{action}:{source["nonce"]}'
    if value is not None:
        c.callback.data += ":" + str(value)
    await handler.catalog_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_direct_list_card_has_no_fake_client_or_created_project_form(screen):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    await click(c, "project", 0)
    selected = handler.project_binding(
        c.data, token=c.data[handler.CARD]["nonce"], sender=123, menu=1,
    )
    assert selected["source_key"] == handler.CARD and selected["client_id"] is None
    assert "construction_project_create_form" not in c.data
    assert "construction_client_card_view" not in c.data or not c.data["construction_client_card_view"]
    assert c.item["name"] not in repr(c.data)
    args = c.ui_service.project_catalog_card.await_args.kwargs
    assert args["project_id"] == c.row.id and args["expected_user_id"] == c.user


@pytest.mark.asyncio
async def test_back_from_card_reuses_original_page_cursor(screen):
    c = screen
    c.page["next_cursor"] = "opaque-next"
    await handler.catalog_callback(c.callback, c.state)
    c.page["next_cursor"] = None
    await click(c, "next")
    assert c.data[handler.LIST]["cursor"] == "opaque-next"
    await click(c, "project", 0)
    await click(c, "back", card=True)
    assert c.ui_service.project_catalog.await_args.kwargs["cursor"] == "opaque-next"
    assert c.data[handler.LIST]["screen"] == "list"


@pytest.mark.asyncio
@pytest.mark.parametrize("child", ["team", "areas"])
async def test_child_screen_updates_catalog_parent_when_message_is_replaced(screen, child):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    await click(c, "project", 0)
    token = c.data[handler.CARD]["nonce"]
    c.sent.return_value = NS(message_id=2)
    c.callback.data = ("PT:" if child == "team" else "PA:") + "open:" + token
    if child == "team":
        await team.project_team_callback(c.callback, c.state)
    else:
        await areas.areas_callback(c.callback, c.state)
    assert c.data[handler.CARD]["menu_id"] == 2
    assert c.data[handler.LIST]["menu_id"] == 2
    c.callback.message.message_id = 2
    assert handler.project_binding(c.data, token=token, sender=123, menu=2) is not None
    await click(c, "card", card=True)
    assert c.data[handler.CARD]["nonce"] != token


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "menu", "sender", "nonce"])
async def test_stale_or_foreign_selection_never_reads_project_card(screen, failure):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    if failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.data[handler.LIST]["menu_id"] = 99
    elif failure == "sender":
        c.callback.from_user.id = c.callback.message.chat.id = 999
    else:
        c.callback.data = "PL:project:obsolete:0"
        await handler.catalog_callback(c.callback, c.state)
        c.ui_service.project_catalog_card.assert_not_awaited()
        return
    await click(c, "project", 0)
    c.ui_service.project_catalog_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_clears_read_selectors_without_touching_pending_writes(screen):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    c.data["construction_project_replace_attempt"] = {"status": "pending", "id": "keep"}
    c.callback.data = "CS_HOME"
    downstream = AsyncMock()
    await handler.catalog_navigation_middleware(downstream, c.callback, {"state": c.state})
    assert c.data[handler.LIST] == {} and c.data[handler.CARD] == {}
    assert c.data["construction_project_replace_attempt"]["id"] == "keep"
    downstream.assert_awaited_once()


@pytest.mark.asyncio
async def test_denied_refresh_clears_read_selectors_and_cached_names_are_absent(screen):
    c = screen
    await handler.catalog_callback(c.callback, c.state)
    c.ui_service.project_catalog.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "refresh")
    assert c.data[handler.LIST] == {} and c.data[handler.CARD] == {}
    assert c.item["name"] not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_transport_reads_actual_registered_catalog(catalog_http):
    c = catalog_http
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://127.0.0.1",
    ) as http:
        result = await ConstructionApiClient(http=http).project_catalog(
            session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
        )
    assert result.data[0]["id"] == str(c.row.id)
    assert result.meta == {"pagination": {"next_cursor": None}}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "duplicate", "version", "private", "cursor"])
async def test_transport_rejects_unbound_or_invalid_catalog_data(screen, failure):
    c = screen
    body = {"data": [deepcopy(c.item)], "meta": {"pagination": {"next_cursor": None}}, "request_id": "local"}
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
            await ConstructionApiClient(http=http).project_catalog(
                session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
            )


@pytest.mark.asyncio
async def test_changed_subject_stops_before_catalog_http():
    user, tenant = uuid4(), uuid4()
    api = NS(project_catalog=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"can_read_projects": True},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.project_catalog(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=uuid4(),
        )
    api.project_catalog.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope,allowed", [
    ("OWNER", "tenant", True), ("PROJECT_MANAGER", "project", True),
    ("OBSERVER", "project", True), ("OBSERVER", "document", False),
])
async def test_navigation_project_capability_uses_existing_server_policy(role, scope, allowed):
    tenant, user = uuid4(), uuid4()
    actor = NS(user_id=user, tenant_id=tenant, status="active")
    context = ConstructionAccessContext(
        user, tenant, (role,), tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        (ConstructionGrantContext(role, scope, tenant if scope == "tenant" else uuid4()),),
    )
    body = await navigation.navigation_context(
        request=NS(query_params={}, state=NS(request_id="project-catalog-navigation")),
        response=Response(), actor=actor, context=context,
        access=NS(has_active_platform_membership=AsyncMock(return_value=True)),
        organizations=NS(require_current=AsyncMock(return_value=NS(
            tenant_id=tenant, name="Organization", default_currency="EUR",
        ))),
    )
    assert body["data"]["can_read_projects"] is allowed


def test_catalog_labels_cover_all_seven_languages():
    assert all(len(values) == 7 for values in LABELS.values())

# project_action_visibility_v1
@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_catalog_hides_forbidden_actions_and_refreshes_capabilities(screen, allowed):
    c = screen
    flags = ("can_edit_project", "can_plan_activity", "can_open_team")
    prefixes = ("PDE:open:", "AP:project:", "PT:open:")

    def action_prefixes():
        markup = c.sent.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard for button in row
        ]
        assert any(value.startswith("PA:open:") for value in callbacks)
        assert any(value.startswith("PAV:open:") for value in callbacks)
        return {
            prefix for prefix in prefixes
            if any(value.startswith(prefix) for value in callbacks)
        }

    c.card.update({name: allowed for name in flags})
    await handler.catalog_callback(c.callback, c.state)
    await click(c, "project", 0)
    assert action_prefixes() == (set(prefixes) if allowed else set())

    c.card.update({name: not allowed for name in flags})
    await click(c, "card", card=True)
    assert action_prefixes() == (set() if allowed else set(prefixes))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", [
    "project_catalog_card", "project_card", "read_created_project",
])
@pytest.mark.parametrize("allowed", [False, True])
async def test_project_adapter_uses_server_flags_instead_of_home_role(method, allowed):
    user, tenant, project, client = uuid4(), uuid4(), uuid4(), uuid4()
    flags = {
        "can_edit_project": allowed,
        "can_plan_activity": allowed,
        "can_open_team": allowed,
    }
    reader = AsyncMock(return_value=NS(
        data={"id": str(project)}, meta={"capabilities": flags},
    ))
    team_reader = AsyncMock()
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(
            project_details=reader, project_card=reader,
            team_members=team_reader,
        )),
        context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {
            "language": "uk", "name": "Organization",
            "roles": ("OWNER",), "can_manage_team": not allowed,
        },
    ))
    options = dict(
        telegram_user_id=123, tenant_id=tenant,
        project_id=project, expected_user_id=user,
    )
    if method == "project_card":
        options["client_id"] = client
    result = await getattr(entry, method)(**options)
    assert {name: result[name] for name in flags} == flags
    reader.assert_awaited_once()
    team_reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_project_capabilities_cannot_enable_owner_buttons():
    user, tenant, project = uuid4(), uuid4(), uuid4()
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(
            project_details=AsyncMock(return_value=NS(
                data={"id": str(project)}, meta={},
            )),
        )),
        context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"language": "uk", "name": "Organization", "can_manage_team": True},
    ))
    result = await entry.project_catalog_card(
        telegram_user_id=123, tenant_id=tenant,
        project_id=project, expected_user_id=user,
    )
    assert all(result[name] is False for name in (
        "can_edit_project", "can_plan_activity", "can_open_team",
    ))
