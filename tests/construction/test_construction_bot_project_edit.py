"""Project editor transport and parent navigation for all three entry paths."""
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

from handlers.construction import construction_project_edit as editor
from handlers.construction import construction_project_areas as areas
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_project_edit import LABELS


@pytest.fixture
def project_item():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid4()), "tenant_id": str(uuid4()), "client_id": str(uuid4()),
        "name": "Private original project", "status": "draft",
        "responsible_user_id": None, "comment": "Private original comment",
        "address_raw": "Private original address", "address_formatted": None,
        "country_code": None, "region": None, "city": None, "postal_code": None,
        "latitude": None, "longitude": None, "address_provider": None,
        "provider_place_id": None, "address_verification_status": "pending",
        "created_by": str(uuid4()), "created_at": now, "updated_at": now,
        "row_version": 7,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"name": " New project "}, {"address_raw": " New address "}, {"comment": None},
])
async def test_transport_sends_sparse_patch_and_original_version(project_item, fields):
    item = deepcopy(project_item)
    item.update({name: value.strip() if isinstance(value, str) else value
                 for name, value in fields.items()})
    item["row_version"] = 8
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "project-edit"})
    session = PlatformApiClientSession(
        uuid4(), UUID(item["tenant_id"]), SecretStr("local-project-edit"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handle),
    ) as http:
        result = await ConstructionApiClient(http=http).patch_project(
            session=session, project_id=UUID(item["id"]),
            payload={"expected_row_version": 7, **fields},
        )
    assert len(calls) == 1 and calls[0].method == "PATCH"
    assert calls[0].url.path == "/api/v1/construction/projects/" + item["id"]
    assert json.loads(calls[0].content) == {
        "expected_row_version": 7,
        **{name: value.strip() if isinstance(value, str) else value
           for name, value in fields.items()},
    }
    assert "idempotency-key" not in calls[0].headers
    actual, expected = deepcopy(result.data), deepcopy(item)
    for name in ("created_at", "updated_at"):
        actual[name] = datetime.fromisoformat(actual[name].replace("Z", "+00:00"))
        expected[name] = datetime.fromisoformat(expected[name].replace("Z", "+00:00"))
        assert actual[name].utcoffset() == timezone.utc.utcoffset(None)
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "id", "version", "field"])
async def test_transport_rejects_unbound_receipts(project_item, failure):
    item = {**project_item, "name": "New project", "row_version": 8}
    if failure in ("tenant", "id"):
        item["tenant_id" if failure == "tenant" else "id"] = str(uuid4())
    elif failure == "version":
        item["row_version"] = 9
    else:
        item["name"] = "Wrong project name"
    session = PlatformApiClientSession(
        uuid4(), UUID(project_item["tenant_id"]), SecretStr("local-project-edit"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": item, "meta": {}, "request_id": "project-edit"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).patch_project(
                session=session, project_id=UUID(project_item["id"]),
                payload={"expected_row_version": 7, "name": "New project"},
            )


@pytest.mark.asyncio
async def test_entry_does_not_read_or_rebase_before_patch(project_item):
    session = PlatformApiClientSession(
        uuid4(), UUID(project_item["tenant_id"]), SecretStr("local-project-edit"),
    )
    transport = NS(
        patch_project=AsyncMock(return_value=NS(data=project_item)),
        project_details=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=transport), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(session, {"language": "uk"}))
    payload = {"expected_row_version": 7, "comment": None}
    await entry.patch_project(
        telegram_user_id=123, tenant_id=session.tenant_id,
        project_id=UUID(project_item["id"]), expected_user_id=session.user_id,
        payload=payload,
    )
    transport.patch_project.assert_awaited_once_with(
        session=session, project_id=UUID(project_item["id"]), payload=payload,
    )
    transport.project_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_actor_is_denied_before_patch(project_item):
    session = PlatformApiClientSession(
        uuid4(), UUID(project_item["tenant_id"]), SecretStr("local-project-edit"),
    )
    transport = NS(patch_project=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=transport), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(session, {"language": "uk"}))
    with pytest.raises(ConstructionApiClientError):
        await entry.patch_project(
            telegram_user_id=123, tenant_id=session.tenant_id,
            project_id=UUID(project_item["id"]), expected_user_id=uuid4(),
            payload={"expected_row_version": 7, "comment": None},
        )
    transport.patch_project.assert_not_awaited()


@pytest.fixture(params=("catalog", "client", "created"))
def screen(request, monkeypatch, project_item):
    c = NS(item=project_item, actor=uuid4(), serial=1, origin=request.param)
    base = {
        "telegram_user_id": 123, "tenant_id": project_item["tenant_id"],
        "actor_user_id": str(c.actor), "menu_id": 1,
    }
    token = "b" * 32
    c.data = {
        editor.CONTEXT: {"telegram_user_id": 123, "tenant_id": project_item["tenant_id"]},
        "last_menu_message_id": 1,
    }
    if c.origin == "catalog":
        c.source_key = areas.CATALOG
        c.data["construction_project_catalog_view"] = {
            **base, "nonce": "a" * 32, "screen": "card",
            "project_ids": [project_item["id"]], "cursor": "original-position",
        }
        c.data[areas.CATALOG] = {
            **base, "nonce": token, "source_nonce": "a" * 32,
            "project_id": project_item["id"],
        }
    elif c.origin == "client":
        c.source_key = areas.PROJECT
        c.data[areas.CARD] = {
            **base, "nonce": "a" * 32, "section": "projects",
            "client_id": project_item["client_id"], "project_ids": [project_item["id"]],
            "positions": {"projects": "original-position"},
        }
        c.data[areas.PROJECT] = {
            **base, "nonce": token, "source_nonce": "a" * 32,
            "client_id": project_item["client_id"], "project_id": project_item["id"],
        }
    else:
        c.source_key = areas.FORM
        c.data[areas.FORM] = {
            **base, "id": token, "phase": "done", "step": 4,
            "project_id": project_item["id"], "payload": {},
        }

    async def update(**values):
        c.data.update(deepcopy(values))
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: deepcopy(c.data)),
        update_data=AsyncMock(side_effect=update), get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    async def answer(*args, **kwargs):
        c.serial += 1
        return NS(message_id=c.serial)
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"),
        text=None, answer=AsyncMock(side_effect=answer),
    )
    c.callback = NS(
        from_user=sender, data="PDE:open:" + token, answer=AsyncMock(),
        message=NS(message_id=1, chat=c.message.chat, answer=c.message.answer),
    )
    async def change(**kwargs):
        fields = {name: value for name, value in kwargs["payload"].items()
                  if name != "expected_row_version"}
        return {
            "actor_user_id": str(c.actor),
            "item": {**project_item, **fields, "row_version": 8},
        }
    c.service = NS(
        project_edit_address_suggestions=AsyncMock(return_value={
            "items": [], "actor_user_id": str(c.actor),
            "tenant_id": project_item["tenant_id"], "project_id": project_item["id"],
            "language": "uk",
        }),
        read_created_project=AsyncMock(return_value={
            "actor_user_id": str(c.actor), "language": "uk",
            "item": deepcopy(project_item),
        }),
        patch_project=AsyncMock(side_effect=change),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(editor, "construction_bot_api_entry", entry)
    return c


async def open_editor(c):
    await editor.project_edit_callback(c.callback, c.state)
    assert c.data[editor.FORM]["stage"] == "choose"


async def click(c, action, selector=None):
    c.callback.data = editor.cb(c.data[editor.FORM], action, selector)
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await editor.project_edit_callback(c.callback, c.state)


async def enter(c, field, value):
    await click(c, "field", editor.FIELDS.index(field))
    c.message.text = value
    await editor.project_edit_message(c.message, c.state)
    if field == "address_raw":
        assert c.data[editor.FORM]["stage"] == "address_suggestions"
        await click(c, "address_continue")


@pytest.mark.asyncio
async def test_editor_preserves_parent_binding_and_stores_no_original_content(screen):
    c = screen
    await open_editor(c)
    form = c.data[editor.FORM]
    assert form["expected_row_version"] == 7
    assert editor.selected(c.data, form, 123, c.data["last_menu_message_id"]) is not None
    for field in ("name", "address_raw", "comment"):
        assert c.item[field] not in repr(form)
    assert "item" not in form and "payload" not in form
    c.service.patch_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_reviews_single_field_and_preserves_navigation(screen):
    c = screen
    await open_editor(c)
    await enter(c, "name", " New project ")
    assert c.data[editor.FORM]["stage"] == "review"
    c.service.patch_project.assert_not_awaited()
    await click(c, "save")
    assert c.service.patch_project.await_args.kwargs["payload"] == {
        "expected_row_version": 7, "name": "New project",
    }
    form = c.data[editor.FORM]
    assert form["stage"] == "saved" and "value" not in form
    assert editor.selected(c.data, form, 123, c.data["last_menu_message_id"]) is not None
    if c.origin == "catalog":
        assert c.data["construction_project_catalog_view"]["cursor"] == "original-position"
    elif c.origin == "client":
        assert c.data[areas.CARD]["positions"]["projects"] == "original-position"
    c.service.read_created_project.assert_awaited_once()
    await click(c, "save")
    c.service.patch_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_comment_clear_requires_confirmation(screen):
    c = screen
    await open_editor(c)
    await click(c, "field", editor.FIELDS.index("comment"))
    await click(c, "clear")
    assert c.data[editor.FORM]["stage"] == "review"
    c.service.patch_project.assert_not_awaited()
    await click(c, "save")
    assert c.service.patch_project.await_args.kwargs["payload"] == {
        "expected_row_version": 7, "comment": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status,stage", [(409, "conflict"), (403, "blocked"), (504, "uncertain")])
async def test_failure_never_rebases_or_retries(screen, status, stage):
    c = screen
    await open_editor(c)
    await enter(c, "address_raw", "New address")
    c.service.patch_project.side_effect = ConstructionApiClientError(
        status_code=status, code="project_edit_test_failure",
    )
    await click(c, "save")
    assert c.data[editor.FORM]["stage"] == stage
    assert "value" not in c.data[editor.FORM]
    await click(c, "save")
    c.service.patch_project.assert_awaited_once()
    c.service.read_created_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_tenant_prevents_input_and_write(screen):
    c = screen
    await open_editor(c)
    await click(c, "field", editor.FIELDS.index("comment"))
    c.data[editor.CONTEXT]["tenant_id"] = str(uuid4())
    c.message.text = "Wrong tenant"
    assert not await editor.ProjectFieldInput()(c.message, c.state)
    await click(c, "save")
    c.service.patch_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_discards_draft_without_write(screen):
    c = screen
    await open_editor(c)
    await enter(c, "comment", "Discard this")
    await click(c, "cancel")
    assert c.data[editor.FORM]["stage"] == "cancelled"
    assert "value" not in c.data[editor.FORM]
    c.service.patch_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_clears_editor(screen):
    c = screen
    await open_editor(c)
    c.callback.data = c.data[editor.FORM]["back"]
    next_handler = AsyncMock(return_value="next")
    assert await editor.project_edit_navigation_middleware(
        next_handler, c.callback, {"state": c.state},
    ) == "next"
    assert c.data[editor.FORM] == {}


def test_translations_and_callbacks_are_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    form = {"nonce": "a" * 32}
    for index in range(len(editor.FIELDS)):
        assert len(editor.cb(form, "field", index).encode("utf-8")) <= 64

# project_edit_address_suggestions_v1
