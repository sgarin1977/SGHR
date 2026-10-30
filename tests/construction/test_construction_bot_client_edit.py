"""Client editor API transport, frozen versions and private form binding."""
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

from handlers.construction import construction_client_edit as editor
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_client_edit import LABELS

@pytest.fixture
def client_item():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid4()), "tenant_id": str(uuid4()),
        "display_name": "Private client name", "client_type": "person",
        "phone": "private-phone-value", "email": "private@example.test",
        "notes": "Private old note", "linked_user_id": None, "row_version": 7,
        "created_at": now, "updated_at": now,
    }

@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"phone": None}, {"notes": "New note"},
    {"client_type": "company"}, {"display_name": " New name "},
])
async def test_transport_sends_sparse_versioned_patch_without_replay_key(client_item, fields):
    item = deepcopy(client_item)
    item.update(fields)
    if "display_name" in fields:
        item["display_name"] = fields["display_name"].strip()
    item["row_version"] = 8
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "client-edit"})
    session = PlatformApiClientSession(
        uuid4(), UUID(item["tenant_id"]), SecretStr("local-client-test"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handle),
    ) as http:
        result = await ConstructionApiClient(http=http).patch_client(
            session=session, client_id=UUID(item["id"]),
            payload={"expected_row_version": 7, **fields},
        )
    # UTC may serialize as Z or +00:00; compare the complete response
    # with timestamps represented as aware instants, and require UTC.
    actual = deepcopy(result.data)
    expected_item = deepcopy(item)
    for name in ("created_at", "updated_at"):
        assert isinstance(actual[name], str)
        actual_time = datetime.fromisoformat(actual[name].replace("Z", "+00:00"))
        expected_time = datetime.fromisoformat(expected_item[name].replace("Z", "+00:00"))
        assert actual_time.tzinfo is not None
        assert actual_time.utcoffset() == timezone.utc.utcoffset(None)
        actual[name] = actual_time
        expected_item[name] = expected_time
    assert actual == expected_item
    assert len(calls) == 1 and calls[0].method == "PATCH"
    assert calls[0].url.path == "/api/v1/construction/clients/" + item["id"]
    expected = {"expected_row_version": 7, **fields}
    if "display_name" in expected:
        expected["display_name"] = expected["display_name"].strip()
    assert json.loads(calls[0].content) == expected
    assert calls[0].headers["authorization"] == "Bearer local-client-test"
    assert "idempotency-key" not in calls[0].headers

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "id", "version", "field"])
async def test_transport_rejects_unbound_or_wrong_receipt(client_item, failure):
    item = {**client_item, "notes": "New note", "row_version": 8}
    if failure in ("tenant", "id"):
        item["tenant_id" if failure == "tenant" else "id"] = str(uuid4())
    elif failure == "version":
        item["row_version"] = 9
    else:
        item["notes"] = "Different value"
    session = PlatformApiClientSession(
        uuid4(), UUID(client_item["tenant_id"]), SecretStr("local-client-test"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": item, "meta": {}, "request_id": "client-edit"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).patch_client(
                session=session, client_id=UUID(client_item["id"]),
                payload={"expected_row_version": 7, "notes": "New note"},
            )

@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["actor", "tenant"])
async def test_entry_pins_session_and_does_not_rebase_client_version(client_item, mismatch):
    session = PlatformApiClientSession(
        uuid4(), UUID(client_item["tenant_id"]), SecretStr("local-client-test"),
    )
    transport = NS(patch_client=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=transport), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(session, {"language": "uk"}))
    with pytest.raises(ConstructionApiClientError):
        await entry.patch_client(
            telegram_user_id=123,
            tenant_id=uuid4() if mismatch == "tenant" else session.tenant_id,
            client_id=UUID(client_item["id"]),
            expected_user_id=uuid4() if mismatch == "actor" else session.user_id,
            payload={"expected_row_version": 7, "notes": "New note"},
        )
    transport.patch_client.assert_not_awaited()

@pytest.fixture
def screen(monkeypatch, client_item):
    c = NS(item=client_item, actor=uuid4(), serial=1)
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.data = {
        editor.CONTEXT: {"telegram_user_id": 123, "tenant_id": client_item["tenant_id"]},
        editor.CARD: {
            "telegram_user_id": 123, "tenant_id": client_item["tenant_id"],
            "client_id": client_item["id"], "actor_user_id": str(c.actor),
            "nonce": "a" * 32, "menu_id": 1, "section": "summary",
        },
        "last_menu_message_id": 1,
    }
    async def update(**values):
        c.data.update(deepcopy(values))
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: deepcopy(c.data)),
        update_data=AsyncMock(side_effect=update), get_state=AsyncMock(return_value=None),
    )
    async def answer(*args, **kwargs):
        c.serial += 1
        return NS(message_id=c.serial)
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"),
        text=None, answer=AsyncMock(side_effect=answer),
    )
    c.callback = NS(
        from_user=sender, message=NS(
            message_id=1, chat=c.message.chat, answer=c.message.answer,
        ),
        data="CDE:open:" + "a" * 32, answer=AsyncMock(),
    )
    async def change(**kwargs):
        fields = {key: value for key, value in kwargs["payload"].items()
                  if key != "expected_row_version"}
        return {
            "actor_user_id": str(c.actor),
            "item": {**client_item, **fields, "row_version": 8},
        }
    c.service = NS(
        client_card=AsyncMock(return_value={
            "actor_user_id": str(c.actor), "language": "uk", "item": deepcopy(client_item),
        }),
        patch_client=AsyncMock(side_effect=change),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(editor, "construction_bot_api_entry", entry)
    return c

async def open_editor(c):
    await editor.client_edit_callback(c.callback, c.state)
    assert c.data[editor.FORM]["stage"] == "choose"

async def click(c, action, selector=None):
    c.callback.data = editor.cb(c.data[editor.FORM], action, selector)
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await editor.client_edit_callback(c.callback, c.state)

async def select(c, field):
    await click(c, "field", editor.FIELDS.index(field))

@pytest.mark.asyncio
async def test_editor_keeps_only_metadata_until_user_enters_a_value(screen):
    c = screen
    await open_editor(c)
    form = c.data[editor.FORM]
    assert form["expected_row_version"] == 7
    for field in ("display_name", "phone", "email", "notes"):
        assert c.item[field] not in repr(form)
    assert "item" not in form and "payload" not in form
    c.service.patch_client.assert_not_awaited()

@pytest.mark.asyncio
async def test_review_and_save_use_original_version_and_one_field(screen):
    c = screen
    await open_editor(c)
    await select(c, "notes")
    c.message.text = " New note "
    await editor.client_edit_message(c.message, c.state)
    assert c.data[editor.FORM]["stage"] == "review"
    c.service.patch_client.assert_not_awaited()
    await click(c, "save")
    assert c.service.patch_client.await_args.kwargs["payload"] == {
        "expected_row_version": 7, "notes": "New note",
    }
    assert c.data[editor.FORM]["stage"] == "saved"
    assert "value" not in c.data[editor.FORM]
    c.service.client_card.assert_awaited_once()
    await click(c, "save")
    c.service.patch_client.assert_awaited_once()

@pytest.mark.asyncio
async def test_optional_clear_is_explicit_null(screen):
    c = screen
    await open_editor(c)
    await select(c, "phone")
    await click(c, "clear")
    assert c.data[editor.FORM]["stage"] == "review"
    assert c.data[editor.FORM]["value"] is None
    await click(c, "save")
    assert c.service.patch_client.await_args.kwargs["payload"] == {
        "expected_row_version": 7, "phone": None,
    }

@pytest.mark.asyncio
async def test_type_uses_existing_person_company_values(screen):
    c = screen
    await open_editor(c)
    await select(c, "client_type")
    assert not await editor.ClientFieldInput()(c.message, c.state)
    await click(c, "type", "company")
    await click(c, "save")
    assert c.service.patch_client.await_args.kwargs["payload"] == {
        "expected_row_version": 7, "client_type": "company",
    }

@pytest.mark.asyncio
@pytest.mark.parametrize("status,stage", [(409, "conflict"), (403, "blocked"), (504, "uncertain")])
async def test_failure_never_rebases_or_retries_write(screen, status, stage):
    c = screen
    await open_editor(c)
    await select(c, "notes")
    c.message.text = "New note"
    await editor.client_edit_message(c.message, c.state)
    c.service.patch_client.side_effect = ConstructionApiClientError(
        status_code=status, code="test_failure",
    )
    await click(c, "save")
    assert c.data[editor.FORM]["stage"] == stage
    assert "value" not in c.data[editor.FORM]
    await click(c, "save")
    c.service.patch_client.assert_awaited_once()
    c.service.client_card.assert_awaited_once()

@pytest.mark.asyncio
async def test_cancel_and_changed_tenant_prevent_mutation(screen):
    c = screen
    await open_editor(c)
    await select(c, "notes")
    c.data[editor.CONTEXT]["tenant_id"] = str(uuid4())
    c.message.text = "Wrong tenant"
    assert not await editor.ClientFieldInput()(c.message, c.state)
    await click(c, "save")
    c.service.patch_client.assert_not_awaited()

@pytest.mark.asyncio
async def test_cancel_discards_draft(screen):
    c = screen
    await open_editor(c)
    await select(c, "notes")
    c.message.text = "Discard this note"
    await editor.client_edit_message(c.message, c.state)
    await click(c, "cancel")
    assert c.data[editor.FORM]["stage"] == "cancelled"
    assert "value" not in c.data[editor.FORM]
    c.service.patch_client.assert_not_awaited()

@pytest.mark.asyncio
async def test_navigation_clears_editor_before_other_handlers(screen):
    c = screen
    await open_editor(c)
    c.callback.data = "CS_HOME"
    next_handler = AsyncMock(return_value="next")
    result = await editor.client_edit_navigation_middleware(
        next_handler, c.callback, {"state": c.state},
    )
    assert result == "next" and c.data[editor.FORM] == {}
    c.service.patch_client.assert_not_awaited()

def test_translations_and_callback_lengths():
    assert all(len(values) == 7 for values in LABELS.values())
    form = {"nonce": "a" * 32}
    for index in range(len(editor.FIELDS)):
        assert len(editor.cb(form, "field", index).encode("utf-8")) <= 64
    for kind in ("person", "company"):
        assert len(editor.cb(form, "type", kind).encode("utf-8")) <= 64
