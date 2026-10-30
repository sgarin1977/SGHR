"""Local API transport and private universal-area navigation tests."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_project_areas as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, ConstructionApiResult,
    PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_project_areas import areas_text
from test_construction_01_project_areas_http import area_http, get


@pytest.fixture
async def body(area_http):
    response = await get(area_http)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_transport_uses_registered_area_get_without_caller_identity(body, area_http):
    c = area_http
    def transport(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/construction/projects/{c.project_id}/areas"
        assert not request.url.query
        assert request.headers["Authorization"] == "Bearer local-test-secret"
        assert "X-Tenant-ID" not in request.headers
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).project_areas(
            session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test-secret")),
            project_id=c.project_id,
        )
    assert result.data == body["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "tenant", "project", "duplicate", "version", "order", "status", "private",
])
async def test_transport_rejects_unbound_or_invalid_area_response(body, area_http, failure):
    bad = deepcopy(body)
    item = bad["data"][0]
    if failure == "tenant":
        item["tenant_id"] = str(uuid4())
    elif failure == "project":
        item["project_id"] = str(uuid4())
    elif failure == "duplicate":
        bad["data"].append(deepcopy(item))
    elif failure == "version":
        item["row_version"] = True
    elif failure == "order":
        item["sort_order"] = False
    elif failure == "status":
        item["status"] = "unknown"
    else:
        item["private_marker"] = "private-field"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=bad)),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).project_areas(
                session=PlatformApiClientSession(
                    area_http.user, area_http.tenant, SecretStr("local-test-secret"),
                ),
                project_id=area_http.project_id,
            )


@pytest.mark.asyncio
async def test_changed_subject_is_denied_before_project_or_area_reads():
    user, tenant = uuid4(), uuid4()
    api = NS(project_details=AsyncMock(), project_card=AsyncMock(), project_areas=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test-secret")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.project_areas(
            telegram_user_id=123, tenant_id=tenant,
            project_id=uuid4(), expected_user_id=uuid4(),
        )
    api.project_details.assert_not_awaited()
    api.project_card.assert_not_awaited()
    api.project_areas.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_client_association_stops_before_area_read():
    user, tenant = uuid4(), uuid4()
    api = NS(
        project_card=AsyncMock(side_effect=ConstructionApiClientError(
            status_code=404, code="not_found",
        )),
        project_areas=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test-secret")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.project_areas(
            telegram_user_id=123, tenant_id=tenant, project_id=uuid4(),
            expected_user_id=user, client_id=uuid4(),
        )
    api.project_areas.assert_not_awaited()


@pytest.fixture(params=["client", "standalone"])
def screen(request, monkeypatch):
    c = NS(user=uuid4(), tenant=uuid4(), project=uuid4(), client=uuid4())
    c.token, card_nonce = uuid4().hex, uuid4().hex
    c.source_key = handler.PROJECT if request.param == "client" else handler.FORM
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        "last_menu_message_id": 1,
    }
    source = {
        "telegram_user_id": 123, "tenant_id": str(c.tenant),
        "actor_user_id": str(c.user), "project_id": str(c.project), "menu_id": 1,
    }
    if c.source_key == handler.PROJECT:
        source.update(nonce=c.token, source_nonce=card_nonce, client_id=str(c.client))
        c.data[handler.CARD] = {
            "nonce": card_nonce, "telegram_user_id": 123, "tenant_id": str(c.tenant),
            "actor_user_id": str(c.user), "client_id": str(c.client),
            "section": "projects", "menu_id": 1, "project_ids": [str(c.project)],
            "positions": {"projects": "preserved-client-position"},
        }
    else:
        source.update(id=c.token, phase="done", step=4, payload={})
    c.data[c.source_key] = source
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    c.callback = NS(
        data="PA:open:" + c.token,
        from_user=NS(id=123, is_bot=False, language_code="uk"),
        answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.result = {
        "items": [{
            "id": str(uuid4()), "tenant_id": str(c.tenant),
            "project_id": str(c.project), "name": f"Private room {number}",
            "area_type": "ROOM", "status": "draft", "sort_order": None, "row_version": 1,
        } for number in range(6)],
        "actor_user_id": str(c.user), "project_id": str(c.project),
        "project_name": "Project name", "organization": "Organization", "language": "uk",
    }
    c.service = NS(project_areas=AsyncMock(return_value=c.result))
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def click(c, position):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = f'PA:page:{c.data[handler.VIEW]["nonce"]}:{position}'
    await handler.areas_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_open_and_page_use_fresh_api_and_keep_only_selectors(screen):
    c = screen
    await handler.areas_callback(c.callback, c.state)
    first = c.callback.data = f'PA:page:{c.data[handler.VIEW]["nonce"]}:5'
    await handler.areas_callback(c.callback, c.state)
    assert c.service.project_areas.await_count == 2
    args = c.service.project_areas.await_args.kwargs
    assert args["project_id"] == c.project and args["expected_user_id"] == c.user
    assert args["client_id"] == (c.client if c.source_key == handler.PROJECT else None)
    assert c.data[handler.VIEW]["offset"] == 5
    assert "Private room" not in repr(c.data)
    assert "Project name" not in repr(c.data)
    c.callback.data = first
    await handler.areas_callback(c.callback, c.state)
    assert c.service.project_areas.await_count == 2


@pytest.mark.asyncio
async def test_message_replacement_preserves_back_source_and_client_positions(screen):
    c = screen
    c.sent.return_value = NS(message_id=2)
    await handler.areas_callback(c.callback, c.state)
    assert c.data[c.source_key]["menu_id"] == 2
    assert c.data["last_menu_message_id"] == 2
    if c.source_key == handler.PROJECT:
        assert c.data[handler.CARD]["menu_id"] == 2
        assert c.data[handler.CARD]["positions"]["projects"] == "preserved-client-position"
    buttons = [
        button.callback_data
        for row in c.sent.await_args.kwargs["reply_markup"].inline_keyboard for button in row
    ]
    back = "PC:refresh:" + c.token if c.source_key == handler.PROJECT else f"PF:open:{c.token}:4"
    assert back in buttons
    assert all(len(value.encode()) <= 64 for value in buttons)


@pytest.mark.asyncio
async def test_denied_refresh_does_not_display_cached_area_names(screen):
    c = screen
    await handler.areas_callback(c.callback, c.state)
    c.service.project_areas.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, 0)
    assert c.data[handler.VIEW] == {}
    assert "Private room" not in c.sent.await_args.kwargs["text"]
    assert "Project name" not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["sender", "tenant", "menu", "source"])
async def test_stale_or_foreign_selection_is_rejected_before_api(screen, failure):
    c = screen
    if failure == "sender":
        c.callback.from_user.id = c.callback.message.chat.id = 999
    elif failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.data["last_menu_message_id"] = 99
    else:
        c.data[c.source_key] = {}
    await handler.areas_callback(c.callback, c.state)
    c.service.project_areas.assert_not_awaited()


@pytest.mark.parametrize("language", ["uk", "ru", "en", "pt", "pl", "de", "nl"])
def test_area_list_localizes_types_and_states_without_internal_ids(language):
    tenant, project = uuid4(), uuid4()
    result = {
        "organization": "Organization", "project_name": "Project",
        "items": [{
            "id": str(uuid4()), "tenant_id": str(tenant), "project_id": str(project),
            "name": "Area", "area_type": kind, "status": "draft",
        } for kind in ("ROOM", "PROJECT_GENERAL", "EXTERIOR", "OTHER")],
    }
    text = areas_text(result, offset=0, language=language)
    assert 0 < len(text) < 3800
    assert "PROJECT_GENERAL" not in text and "draft" not in text
    assert str(tenant) not in text and str(project) not in text
