"""Lead address comparison: optional input, no linking, stable create replay."""
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

from handlers.construction import construction_lead_create as parent
from handlers.construction import construction_lead_address_suggestions as suggestions
from services.construction.construction_api_client import ConstructionApiClientError, PlatformApiClientSession
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_bot_lead_create import wizard

REAL_SHOW = parent.show
VALUES = ("Contact", "+123", "a@example.test", "Repair", "custom_channel")


@pytest_asyncio.fixture
async def address_case(wizard, monkeypatch):
    c = wizard
    storage = MemoryStorage()
    c.state = FSMContext(storage, StorageKey(bot_id=23, chat_id=123, user_id=123))
    await c.state.set_data(deepcopy(c.data))
    monkeypatch.setattr(parent, "show", REAL_SHOW)
    c.serial = 100
    async def sent(*args, **kwargs):
        c.serial += 1
        return NS(message_id=c.serial)
    c.sent = AsyncMock(side_effect=sent)
    c.message.answer = c.sent
    monkeypatch.setattr(parent, "edit_or_replace_menu_message", c.sent)
    c.items = [
        {
            "entity_type": kind, "entity_id": str(uuid4()),
            "label": "Existing private " + kind,
            "address_raw": "Matched address " + kind,
            "match_kind": "exact", "score": 1000,
        }
        for kind in ("project", "client")
    ]
    c.result = {
        **c.service.lead_address_suggestions.return_value,
        "items": deepcopy(c.items),
    }
    c.service.lead_address_suggestions.side_effect = lambda **kwargs: deepcopy(c.result)
    yield c
    await storage.close()


async def click(c, action):
    data = await c.state.get_data()
    c.message.message_id = data["last_menu_message_id"]
    c.callback.data = parent.cb(data[parent.FORM], action)
    await parent.lead_create_callback(c.callback, c.state)


async def before_address(c):
    c.callback.data = "LC:new"
    await parent.lead_create_callback(c.callback, c.state)
    for value in VALUES:
        c.message.text = value
        await parent.lead_create_message(c.message, c.state)
    assert (await c.state.get_data())[parent.FORM]["step"] == suggestions.ADDRESS_INDEX


async def begin(c, address="Entered address"):
    await before_address(c)
    c.message.text = address
    await parent.lead_create_message(c.message, c.state)


async def finish_review(c):
    await click(c, "address_continue")
    await click(c, "skip")
    await click(c, "none")
    assert (await c.state.get_data())[parent.FORM]["phase"] == "review"


@pytest.mark.asyncio
async def test_entered_address_uses_bound_api_and_stores_no_candidates(address_case):
    c = address_case
    await begin(c)
    data = await c.state.get_data()
    form = data[parent.FORM]
    assert form["phase"] == "address_suggestions"
    assert form["address_suggestion_count"] == 2
    args = c.service.lead_address_suggestions.await_args.kwargs
    assert args["query"] == "Entered address"
    assert args["tenant_id"] == UUID(form["tenant_id"])
    assert args["expected_user_id"] == UUID(form["actor_user_id"])
    text = c.message.answer.await_args.args[0]
    assert text.index(c.items[0]["label"]) < text.index(c.items[1]["label"])
    assert c.items[0]["label"] not in repr(data)
    assert c.items[0]["entity_id"] not in repr(data)
    c.service.create_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_skipped_address_never_runs_lookup(address_case):
    c = address_case
    await before_address(c)
    await click(c, "skip")
    form = (await c.state.get_data())[parent.FORM]
    assert form["payload"]["address_raw"] is None
    assert form["phase"] == "input" and form["step"] == suggestions.NEXT_INDEX
    c.service.lead_address_suggestions.assert_not_awaited()
    await click(c, "skip")
    await click(c, "none")
    await click(c, "submit")
    assert c.service.create_lead.await_args.kwargs["payload"]["address_raw"] is None


@pytest.mark.asyncio
async def test_creation_requires_final_confirmation_and_does_not_link_matches(address_case):
    c = address_case
    await begin(c)
    await click(c, "submit")
    c.service.create_lead.assert_not_awaited()
    await finish_review(c)
    c.service.create_lead.assert_not_awaited()
    await click(c, "submit")
    payload = c.service.create_lead.await_args.kwargs["payload"]
    assert payload == {
        "name": "Contact", "phone": "+123", "email": "a@example.test",
        "work_type_description": "Repair", "source": "custom_channel",
        "address_raw": "Entered address", "comment": None,
    }
    assert not {"client_id", "project_id", "linked_user_id"} & set(payload)
    assert (await c.state.get_data())[parent.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_changed_address_preserves_contact_and_invalidates_old_controls(address_case):
    c = address_case
    await begin(c)
    old = deepcopy((await c.state.get_data())[parent.FORM])
    await click(c, "address_change")
    form = (await c.state.get_data())[parent.FORM]
    assert form["step"] == suggestions.ADDRESS_INDEX
    assert form["payload"]["phone"] == "+123"
    assert form["payload"]["source"] == "custom_channel"
    assert "address_raw" not in form["payload"]
    c.message.text = "Second address"
    await parent.lead_create_message(c.message, c.state)
    assert c.service.lead_address_suggestions.await_args.kwargs["query"] == "Second address"
    current = deepcopy((await c.state.get_data())[parent.FORM])
    c.message.message_id = (await c.state.get_data())["last_menu_message_id"]
    c.callback.data = parent.cb(old, "address_continue")
    await parent.lead_create_callback(c.callback, c.state)
    assert (await c.state.get_data())[parent.FORM] == current


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 503])
async def test_failed_optional_check_is_distinct_from_empty_and_can_retry(address_case, status):
    c = address_case
    c.service.lead_address_suggestions.side_effect = ConstructionApiClientError(
        status_code=status, code="unavailable",
    )
    await begin(c)
    form = (await c.state.get_data())[parent.FORM]
    assert form["address_suggestion_state"] == "unavailable"
    text = c.message.answer.await_args.args[0]
    assert suggestions.address_tr("unavailable", "uk") in text
    assert suggestions.address_tr("empty", "uk") not in text
    c.service.lead_address_suggestions.side_effect = lambda **kwargs: deepcopy(c.result)
    await click(c, "address_retry")
    assert (await c.state.get_data())[parent.FORM]["address_suggestion_state"] == "matched"
    await click(c, "address_continue")
    assert (await c.state.get_data())[parent.FORM]["phase"] == "input"
    c.service.create_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_api_result_is_reported_as_empty(address_case):
    c = address_case
    c.result["items"] = []
    await begin(c)
    form = (await c.state.get_data())[parent.FORM]
    assert form["address_suggestion_state"] == "empty"
    assert form["address_suggestion_count"] == 0


@pytest.mark.asyncio
async def test_navigation_during_lookup_cannot_restore_old_form(address_case):
    c = address_case
    async def leave(**kwargs):
        await c.state.update_data(**{parent.CONTEXT: {}, parent.FORM: {}})
        return deepcopy(c.result)
    c.service.lead_address_suggestions.side_effect = leave
    await begin(c)
    data = await c.state.get_data()
    assert data[parent.CONTEXT] == {} and data[parent.FORM] == {}
    assert not any(c.items[0]["label"] in str(call) for call in c.sent.await_args_list)


@pytest.mark.asyncio
async def test_legacy_state_started_during_lookup_is_preserved(address_case):
    c = address_case
    async def leave(**kwargs):
        await c.state.set_state("legacy:input")
        return deepcopy(c.result)
    c.service.lead_address_suggestions.side_effect = leave
    await begin(c)
    assert await c.state.get_state() == "legacy:input"
    assert not any(c.items[0]["label"] in str(call) for call in c.sent.await_args_list)


@pytest.mark.asyncio
async def test_navigation_during_render_cannot_restore_old_context(address_case):
    c = address_case
    original = c.sent.side_effect
    async def sent(*args, **kwargs):
        if args and c.items[0]["label"] in args[0]:
            await c.state.update_data(**{parent.CONTEXT: {}, parent.FORM: {}})
        return await original(*args, **kwargs)
    c.sent.side_effect = sent
    await begin(c)
    data = await c.state.get_data()
    assert data[parent.CONTEXT] == {} and data[parent.FORM] == {}


@pytest.mark.asyncio
async def test_pending_creation_replays_original_command_without_new_lookup(address_case):
    c = address_case
    await begin(c)
    await finish_review(c)
    receipt = c.service.create_lead.return_value
    c.service.create_lead.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"), receipt,
    ]
    await click(c, "submit")
    first = deepcopy(c.service.create_lead.await_args.kwargs)
    reads = c.service.lead_address_suggestions.await_count
    await click(c, "submit")
    assert c.service.create_lead.await_args.kwargs == first
    assert c.service.lead_address_suggestions.await_count == reads
    assert (await c.state.get_data())[parent.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_cancel_does_not_create_any_lead(address_case):
    c = address_case
    await begin(c)
    await click(c, "cancel")
    assert (await c.state.get_data())[parent.FORM] == {}
    c.service.create_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_limit_does_not_truncate_lead_address(address_case):
    c = address_case
    address = "A" * 4097
    await begin(c, address)
    c.service.lead_address_suggestions.assert_not_awaited()
    form = (await c.state.get_data())[parent.FORM]
    assert form["payload"]["address_raw"] == address
    assert form["address_suggestion_state"] == "unavailable"


@pytest.mark.asyncio
async def test_adapter_uses_existing_authorized_read_and_pins_identity():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test"))
    read = AsyncMock(return_value=NS(data=[]))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(address_suggestions=read)), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(session, {"language": "uk"}))
    options = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id, query="Address",
    )
    result = await entry.lead_address_suggestions(**options)
    read.assert_awaited_once_with(session=session, query="Address")
    assert result["actor_user_id"] == str(session.user_id)
    assert result["tenant_id"] == str(session.tenant_id)
    read.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.lead_address_suggestions(**{**options, "expected_user_id": uuid4()})
    read.assert_not_awaited()
    with pytest.raises(ConstructionApiClientError):
        await entry.lead_address_suggestions(**{**options, "tenant_id": uuid4()})
    read.assert_not_awaited()


def test_translations_and_callbacks_fit_telegram():
    assert all(len(values) == 7 for values in suggestions.LABELS.values())
    form = {"id": "a" * 32, "step": suggestions.NEXT_INDEX}
    for action in ("address_continue", "address_change", "address_retry"):
        assert len(parent.cb(form, action).encode("utf-8")) <= 64
