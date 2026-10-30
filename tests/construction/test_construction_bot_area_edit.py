"""Area editor transport, authority, versioning and navigation regressions."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import json

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_area_edit as editor
from handlers.construction import construction_project_areas as areas
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError,
    PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_permissions import CONSTRUCTION_ROLE_PERMISSIONS
from ui.construction_area_edit import LABELS, LANGUAGES

from test_construction_01_project_areas_http import area_http, get

@pytest.fixture
def identities():
    return NS(user=uuid4(), tenant=uuid4(), project=uuid4(), area=uuid4())

@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"name": " Renamed "},
    {"sort_order": None},
    {"sort_order": -12},
])
async def test_sparse_transport_preserves_field_presence(identities, fields):
    c = identities
    seen = []
    def transport(request):
        seen.append(request)
        assert request.method == "PATCH"
        assert request.url.path == "/api/v1/construction/areas/" + str(c.area)
        assert not request.url.query
        assert "Idempotency-Key" not in request.headers
        assert "X-Tenant-ID" not in request.headers
        assert request.headers["Authorization"] == "Bearer test-area-token"
        expected = {
            "expected_row_version": 7,
            **{key: value.strip() if key == "name" else value
               for key, value in fields.items()},
        }
        assert json.loads(request.content) == expected
        return httpx.Response(200, json={
            "data": {
                "id": str(c.area), "tenant_id": str(c.tenant),
                "project_id": str(c.project), "name": expected.get("name", "Original"),
                "sort_order": expected.get("sort_order", 4), "row_version": 8,
            },
            "meta": {}, "request_id": "area-editor-test",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).patch_project_area(
            session=PlatformApiClientSession(
                c.user, c.tenant, SecretStr("test-area-token"),
            ),
            project_id=c.project, area_id=c.area,
            payload={"expected_row_version": 7, **fields},
        )
    assert len(seen) == 1 and result.data["row_version"] == 8

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "project", "id", "version", "value"])
async def test_transport_rejects_unbound_receipts(identities, failure):
    c = identities
    item = {
        "id": str(c.area), "tenant_id": str(c.tenant),
        "project_id": str(c.project), "name": "New",
        "sort_order": None, "row_version": 8,
    }
    field = {
        "tenant": "tenant_id", "project": "project_id", "id": "id",
        "version": "row_version", "value": "name",
    }[failure]
    item[field] = 12 if failure == "version" else (
        "Wrong" if failure == "value" else str(uuid4())
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": item, "meta": {}, "request_id": "area-editor-test",
        })),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).patch_project_area(
                session=PlatformApiClientSession(
                    c.user, c.tenant, SecretStr("test-area-token"),
                ),
                project_id=c.project, area_id=c.area,
                payload={"expected_row_version": 7, "name": "New"},
            )

@pytest.mark.asyncio
async def test_changed_identity_cannot_start_write(identities):
    c = identities
    api = NS(patch_project_area=AsyncMock())
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(c.user, c.tenant, SecretStr("test-area-token")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.patch_project_area(
            telegram_user_id=123, tenant_id=c.tenant,
            project_id=c.project, area_id=c.area,
            expected_user_id=uuid4(),
            payload={"expected_row_version": 7, "name": "New"},
        )
    api.patch_project_area.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [
    ("active", True), ("completed", False), ("cancelled", False),
])
async def test_edit_capability_requires_mutable_parent(area_http, status, expected):
    area_http.project.status = status
    area_http.area.status = "confirmed"
    response = await get(area_http)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["capabilities"]["can_edit_area"] is expected

@pytest.mark.asyncio
async def test_read_permission_does_not_imply_edit_capability(area_http):
    c = area_http
    c.access.list_active_grants.return_value = [NS(
        role="OBSERVER", scope_type="project", scope_id=c.project_id,
        status="active", expires_at=None,
    )]
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["capabilities"]["can_edit_area"] is False

@pytest.fixture(params=["client", "standalone", "catalog"])
def screen(request, monkeypatch, identities):
    c = identities
    c.client, c.token, c.origin = uuid4(), uuid4().hex, request.param
    c.data = {
        areas.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        "last_menu_message_id": 1,
    }
    common = {
        "telegram_user_id": 123, "tenant_id": str(c.tenant),
        "actor_user_id": str(c.user), "project_id": str(c.project), "menu_id": 1,
    }
    if c.origin == "client":
        c.source_key = areas.PROJECT
        client_nonce = uuid4().hex
        c.data[areas.CARD] = {
            **common, "nonce": client_nonce, "client_id": str(c.client),
            "section": "projects", "project_ids": [str(c.project)],
            "positions": {"projects": "preserved-position"},
        }
        c.data[c.source_key] = {
            **common, "nonce": c.token, "source_nonce": client_nonce,
            "client_id": str(c.client),
        }
    elif c.origin == "standalone":
        c.source_key = areas.FORM
        c.data[c.source_key] = {
            **common, "id": c.token, "phase": "done", "step": 4, "payload": {},
        }
    else:
        c.source_key = areas.CATALOG
        list_nonce = uuid4().hex
        c.data["construction_project_catalog_view"] = {
            **common, "nonce": list_nonce, "screen": "card",
            "project_ids": [str(c.project)], "cursor": "preserved-position",
        }
        c.data[c.source_key] = {
            **common, "nonce": c.token, "source_nonce": list_nonce,
        }
    async def update(**values):
        c.data.update(deepcopy(values))
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: deepcopy(c.data)),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    c.callback = NS(
        data="PA:open:" + c.token,
        from_user=NS(id=123, is_bot=False, language_code="uk"),
        answer=AsyncMock(),
        message=NS(
            message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock(),
        ),
    )
    c.item = {
        "id": str(c.area), "tenant_id": str(c.tenant),
        "project_id": str(c.project), "area_type": "ROOM",
        "name": "Private original area name", "sort_order": 4,
        "status": "confirmed", "row_version": 7,
    }
    c.result = {
        "items": [c.item], "project_id": str(c.project),
        "actor_user_id": str(c.user), "organization": "Organization",
        "project_name": "Project", "language": "uk",
        "can_create_area": True, "can_edit_area": True,
    }
    async def patch(**kwargs):
        payload = kwargs["payload"]
        return {
            "item": {
                key: value for key, value in {
                    **c.item, **payload, "row_version": 8,
                }.items() if key in {
                    "id", "tenant_id", "project_id",
                    "name", "sort_order", "row_version",
                }
            },
            "actor_user_id": str(c.user), "project_id": str(c.project),
            "language": "uk",
        }
    c.service = NS(
        project_areas=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        patch_project_area=AsyncMock(side_effect=patch),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    for module in (areas, editor):
        monkeypatch.setattr(module, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    for module in (areas, editor):
        monkeypatch.setattr(module, "edit_or_replace_menu_message", c.sent)
    return c

async def open_editor(c):
    await areas.areas_callback(c.callback, c.state)
    view = c.data[areas.VIEW]
    c.callback.data = f'AE:open:{view["nonce"]}:0'
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await editor.edit_callback(c.callback, c.state)

async def click(c, action):
    c.callback.data = editor.cb(c.data[editor.FORM], action)
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await editor.edit_callback(c.callback, c.state)

async def enter(c, field, value):
    await click(c, field)
    message = NS(
        text=value, from_user=c.callback.from_user,
        chat=c.callback.message.chat,
        answer=AsyncMock(return_value=NS(message_id=2)),
    )
    await editor.edit_input(message, c.state)

@pytest.mark.asyncio
async def test_editor_keeps_only_selectors_until_user_enters_draft(screen):
    c = screen
    await open_editor(c)
    assert c.data[editor.FORM]["phase"] == "choose"
    assert c.data[editor.FORM]["expected_row_version"] == 7
    assert c.item["name"] not in repr(c.data)
    c.service.patch_project_area.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,expected", [
    ("name", " Renamed area ", {"name": "Renamed area"}),
    ("order", "-3", {"sort_order": -3}),
])
async def test_explicit_save_is_sparse_and_preserves_origin(screen, field, value, expected):
    c = screen
    await open_editor(c)
    await enter(c, field, value)
    assert c.data[editor.FORM]["phase"] == "review"
    c.service.patch_project_area.assert_not_awaited()
    c.sent.return_value = NS(message_id=3)
    await click(c, "save")
    c.service.patch_project_area.assert_awaited_once()
    args = c.service.patch_project_area.await_args.kwargs
    assert args["payload"] == {"expected_row_version": 7, **expected}
    assert args["expected_user_id"] == c.user
    assert args["tenant_id"] == c.tenant and args["area_id"] == c.area
    assert c.data[editor.FORM]["phase"] == "done"
    assert c.data[c.source_key]["menu_id"] == 3
    if c.origin == "client":
        assert c.data[areas.CARD]["positions"]["projects"] == "preserved-position"
        assert c.data[areas.CARD]["menu_id"] == 3
    if c.origin == "catalog":
        listing = c.data["construction_project_catalog_view"]
        assert listing["cursor"] == "preserved-position"
        assert listing["menu_id"] == 3
    await click(c, "save")
    c.service.patch_project_area.assert_awaited_once()

@pytest.mark.asyncio
async def test_clear_order_is_explicit_null(screen):
    c = screen
    await open_editor(c)
    await click(c, "order")
    await click(c, "clear")
    c.service.patch_project_area.assert_not_awaited()
    await click(c, "save")
    assert c.service.patch_project_area.await_args.kwargs["payload"] == {
        "expected_row_version": 7, "sort_order": None,
    }

@pytest.mark.asyncio
async def test_cancel_has_no_write(screen):
    c = screen
    await open_editor(c)
    await enter(c, "name", "Discarded draft")
    await click(c, "cancel")
    assert c.data[editor.FORM] == {}
    c.service.patch_project_area.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [
    (409, "ROW_VERSION_CONFLICT"), (403, "forbidden"),
    (504, "api_timeout"), (500, "internal_error"),
])
async def test_failure_never_rebases_or_automatically_repeats_write(screen, status, code):
    c = screen
    await open_editor(c)
    await enter(c, "name", "New")
    c.service.patch_project_area.side_effect = ConstructionApiClientError(
        status_code=status, code=code,
    )
    before_reads = c.service.project_areas.await_count
    await click(c, "save")
    assert c.data[editor.FORM]["phase"] == "blocked"
    assert c.data[editor.FORM]["expected_row_version"] == 7
    assert c.service.project_areas.await_count == before_reads
    await click(c, "save")
    c.service.patch_project_area.assert_awaited_once()

@pytest.mark.asyncio
async def test_version_changed_after_list_requires_explicit_refresh(screen):
    c = screen
    await areas.areas_callback(c.callback, c.state)
    c.item["row_version"] = 8
    c.callback.data = f'AE:open:{c.data[areas.VIEW]["nonce"]}:0'
    await editor.edit_callback(c.callback, c.state)
    assert not c.data.get(editor.FORM)
    c.service.patch_project_area.assert_not_awaited()

@pytest.mark.asyncio
async def test_navigation_change_during_read_cannot_install_editor(screen):
    c = screen
    await areas.areas_callback(c.callback, c.state)
    async def raced(**kwargs):
        c.data[areas.CONTEXT]["tenant_id"] = str(uuid4())
        return deepcopy(c.result)
    c.service.project_areas.side_effect = raced
    c.callback.data = f'AE:open:{c.data[areas.VIEW]["nonce"]}:0'
    await editor.edit_callback(c.callback, c.state)
    assert not c.data.get(editor.FORM)
    c.service.patch_project_area.assert_not_awaited()

@pytest.mark.asyncio
async def test_revoked_edit_capability_prevents_editor(screen):
    c = screen
    await areas.areas_callback(c.callback, c.state)
    c.result["can_edit_area"] = False
    c.callback.data = f'AE:open:{c.data[areas.VIEW]["nonce"]}:0'
    await editor.edit_callback(c.callback, c.state)
    assert not c.data.get(editor.FORM)
    c.service.patch_project_area.assert_not_awaited()

def test_order_input_and_translation_bounds():
    for values in LABELS.values():
        assert len(values) == len(LANGUAGES) == 7
    assert editor.parse_value("sort_order", "-2147483648") == -2147483648
    assert editor.parse_value("sort_order", "2147483647") == 2147483647
    for value in ("2147483648", "-2147483649", "1.5", "", "true"):
        with pytest.raises(ValueError):
            editor.parse_value("sort_order", value)
    form = {"id": "a" * 32, "revision": 999}
    assert all(
        len(editor.cb(form, action).encode("utf-8")) <= 64
        for action in ("name", "order", "clear", "fields", "save", "cancel")
    )

# universal_area_editor_v1
