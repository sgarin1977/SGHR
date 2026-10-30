"""Standalone project address suggestions through the existing read API."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from pydantic import SecretStr

from handlers.construction import construction_project_create as handler
from handlers.construction import construction_project_address_suggestions as suggestions
from services.construction.construction_api_client import ConstructionApiClientError, PlatformApiClientSession
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_bot_project_create import wizard

REAL_SHOW = handler.show


@pytest_asyncio.fixture
async def address_case(wizard, monkeypatch):
    c = wizard
    storage = MemoryStorage()
    c.state = FSMContext(storage, StorageKey(bot_id=19, chat_id=123, user_id=123))
    await c.state.set_data(deepcopy(c.data))
    monkeypatch.setattr(handler, "show", REAL_SHOW)
    c.counter = 10
    async def sent(*args, **kwargs):
        c.counter += 1
        return NS(message_id=c.counter)
    c.sent = AsyncMock(side_effect=sent)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    c.message.answer = c.sent
    c.items = [
        {
            "entity_type": kind, "entity_id": str(uuid4()),
            "label": "Existing private " + kind,
            "address_raw": "Existing address " + kind,
            "match_kind": "exact", "score": 1000,
        }
        for kind in ("project", "client")
    ]
    c.result = {
        **c.service.project_address_suggestions.return_value,
        "items": deepcopy(c.items),
    }
    c.service.project_address_suggestions.side_effect = lambda **kwargs: deepcopy(c.result)
    yield c
    await storage.close()


async def start(c, address="Entered address"):
    c.callback.data = "PF:new"
    await handler.project_create_callback(c.callback, c.state)
    c.message.text = "Project"
    await handler.project_create_message(c.message, c.state)
    c.message.text = address
    await handler.project_create_message(c.message, c.state)


async def click(c, action):
    data = await c.state.get_data()
    c.message.message_id = data["last_menu_message_id"]
    c.callback.data = handler.cb(data[handler.FORM], action)
    await handler.project_create_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_address_read_renders_api_order_and_keeps_no_candidate_cards(address_case):
    c = address_case
    await start(c)
    data = await c.state.get_data()
    form = data[handler.FORM]
    assert form["phase"] == "address_suggestions"
    assert form["address_suggestion_state"] == "matched"
    assert form["address_suggestion_count"] == 2
    args = c.service.project_address_suggestions.await_args.kwargs
    assert args["query"] == "Entered address"
    assert args["expected_user_id"] == UUID(form["actor_user_id"])
    assert args["tenant_id"] == UUID(form["tenant_id"])
    text = c.message.answer.await_args.args[0]
    assert text.index(c.items[0]["label"]) < text.index(c.items[1]["label"])
    assert c.items[0]["label"] not in repr(data)
    assert c.items[0]["entity_id"] not in repr(data)
    c.service.create_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_continue_creates_only_after_existing_review_and_confirmation(address_case):
    c = address_case
    await start(c)
    await click(c, "address_continue")
    await click(c, "skip")
    await click(c, "none")
    c.service.create_project.assert_not_awaited()
    assert (await c.state.get_data())[handler.FORM]["phase"] == "review"
    await click(c, "submit")
    payload = c.service.create_project.await_args.kwargs["payload"]
    assert payload == {"name": "Project", "address_raw": "Entered address", "comment": None}
    assert "client_id" not in payload
    assert (await c.state.get_data())[handler.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_change_address_invalidates_old_buttons_and_checks_new_query(address_case):
    c = address_case
    await start(c)
    old = deepcopy((await c.state.get_data())[handler.FORM])
    await click(c, "address_change")
    form = (await c.state.get_data())[handler.FORM]
    assert form["step"] == 1 and form["phase"] == "input"
    assert form["payload"] == {"name": "Project"}
    c.message.text = "Second address"
    await handler.project_create_message(c.message, c.state)
    assert c.service.project_address_suggestions.await_args.kwargs["query"] == "Second address"
    current = deepcopy((await c.state.get_data())[handler.FORM])
    c.message.message_id = (await c.state.get_data())["last_menu_message_id"]
    c.callback.data = handler.cb(old, "address_continue")
    await handler.project_create_callback(c.callback, c.state)
    assert (await c.state.get_data())[handler.FORM] == current


@pytest.mark.asyncio
async def test_empty_result_is_distinct_from_failed_check(address_case):
    c = address_case
    c.result["items"] = []
    await start(c)
    form = (await c.state.get_data())[handler.FORM]
    assert form["address_suggestion_state"] == "empty"
    assert suggestions.tr("empty", "uk") in c.message.answer.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 503])
async def test_failed_optional_check_allows_explicit_retry_or_continue(address_case, status):
    c = address_case
    c.service.project_address_suggestions.side_effect = ConstructionApiClientError(
        status_code=status, code="unavailable",
    )
    await start(c)
    form = (await c.state.get_data())[handler.FORM]
    assert form["address_suggestion_state"] == "unavailable"
    text = c.message.answer.await_args.args[0]
    assert suggestions.tr("empty", "uk") not in text
    assert suggestions.tr("unavailable", "uk") in text
    c.service.project_address_suggestions.side_effect = lambda **kwargs: deepcopy(c.result)
    await click(c, "address_retry")
    assert (await c.state.get_data())[handler.FORM]["address_suggestion_state"] == "matched"
    await click(c, "address_continue")
    assert (await c.state.get_data())[handler.FORM]["phase"] == "input"
    c.service.create_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_during_read_cannot_restore_old_form(address_case):
    c = address_case
    async def leave(**kwargs):
        await c.state.update_data(**{handler.CONTEXT: {}, handler.FORM: {}})
        return deepcopy(c.result)
    c.service.project_address_suggestions.side_effect = leave
    await start(c)
    data = await c.state.get_data()
    assert data[handler.CONTEXT] == {} and data[handler.FORM] == {}
    assert not any(
        c.items[0]["label"] in str(call)
        for call in c.sent.await_args_list
    )


@pytest.mark.asyncio
async def test_navigation_during_render_cannot_overwrite_new_context(address_case):
    c = address_case
    original = c.sent.side_effect
    async def sent(*args, **kwargs):
        if args and suggestions.tr("title", "uk") in args[0]:
            await c.state.update_data(**{handler.CONTEXT: {}, handler.FORM: {}})
        return await original(*args, **kwargs)
    c.sent.side_effect = sent
    await start(c)
    data = await c.state.get_data()
    assert data[handler.CONTEXT] == {} and data[handler.FORM] == {}


@pytest.mark.asyncio
async def test_query_transport_limit_does_not_truncate_entered_address(address_case):
    c = address_case
    address = "A" * 4097
    await start(c, address)
    c.service.project_address_suggestions.assert_not_awaited()
    form = (await c.state.get_data())[handler.FORM]
    assert form["payload"]["address_raw"] == address
    assert form["address_suggestion_state"] == "unavailable"


@pytest.mark.asyncio
async def test_pending_creation_retry_does_not_repeat_address_lookup(address_case):
    c = address_case
    await start(c)
    await click(c, "address_continue")
    await click(c, "skip")
    await click(c, "none")
    receipt = c.service.create_project.return_value
    c.service.create_project.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"), receipt,
    ]
    await click(c, "submit")
    first = deepcopy(c.service.create_project.await_args.kwargs)
    reads = c.service.project_address_suggestions.await_count
    await click(c, "submit")
    assert c.service.create_project.await_args.kwargs == first
    assert c.service.project_address_suggestions.await_count == reads


@pytest.mark.asyncio
async def test_adapter_pins_actor_tenant_and_uses_existing_api():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test"))
    read = AsyncMock(return_value=NS(data=[]))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(address_suggestions=read)), context=None, sessions=None,
    )
    home = {"name": "Org", "language": "uk", "can_create_project": True}
    entry._presentation = AsyncMock(return_value=(session, home))
    options = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id, query="Address",
    )
    result = await entry.project_address_suggestions(**options)
    read.assert_awaited_once_with(session=session, query="Address")
    assert result["actor_user_id"] == str(session.user_id)
    assert result["tenant_id"] == str(session.tenant_id)
    read.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.project_address_suggestions(**{**options, "expected_user_id": uuid4()})
    read.assert_not_awaited()
    home["can_create_project"] = False
    with pytest.raises(ConstructionApiClientError):
        await entry.project_address_suggestions(**options)
    read.assert_not_awaited()


def test_translations_and_callback_lengths():
    assert all(len(values) == 7 for values in suggestions.LABELS.values())
    form = {"id": "a" * 32, "step": 2}
    for action in ("address_continue", "address_change", "address_retry"):
        assert len(handler.cb(form, action).encode("utf-8")) <= 64
