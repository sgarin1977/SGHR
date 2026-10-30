"""Address comparisons preserve the editor's original version and parent."""
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

from handlers.construction import construction_project_edit as editor
from handlers.construction import construction_project_edit_address_suggestions as suggestions
from services.construction.construction_api_client import ConstructionApiClientError, PlatformApiClientSession
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_bot_project_edit import screen, project_item


@pytest_asyncio.fixture
async def address_case(screen):
    c = screen
    storage = MemoryStorage()
    c.state = FSMContext(storage, StorageKey(bot_id=21, chat_id=123, user_id=123))
    await c.state.set_data(deepcopy(c.data))
    c.items = [
        {
            "entity_type": "project", "entity_id": c.item["id"],
            "label": "Current object", "address_raw": "Current address",
            "match_kind": "exact", "score": 1000,
        },
        {
            "entity_type": "project", "entity_id": str(uuid4()),
            "label": "Another private object", "address_raw": "Another address",
            "match_kind": "prefix_token", "score": 800,
        },
        {
            "entity_type": "client", "entity_id": str(uuid4()),
            "label": "Another private client", "address_raw": "Client project address",
            "match_kind": "fuzzy", "score": 600,
        },
    ]
    c.result = {
        **c.service.project_edit_address_suggestions.return_value,
        "items": deepcopy(c.items),
    }
    c.service.project_edit_address_suggestions.side_effect = lambda **kwargs: deepcopy(c.result)
    yield c
    await storage.close()


async def click(c, action, selector=None):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = editor.cb(data[editor.FORM], action, selector)
    await editor.project_edit_callback(c.callback, c.state)


async def begin(c, address="New address"):
    await editor.project_edit_callback(c.callback, c.state)
    assert (await c.state.get_data())[editor.FORM]["stage"] == "choose"
    await click(c, "field", editor.FIELDS.index("address_raw"))
    c.message.text = address
    await editor.project_edit_message(c.message, c.state)


@pytest.mark.asyncio
async def test_comparisons_are_read_only_exclude_self_and_preserve_origin(address_case):
    c = address_case
    await begin(c)
    data = await c.state.get_data()
    form = data[editor.FORM]
    assert form["stage"] == "address_suggestions"
    assert form["expected_row_version"] == 7
    assert form["address_suggestion_count"] == 2
    args = c.service.project_edit_address_suggestions.await_args.kwargs
    assert args["project_id"] == UUID(c.item["id"])
    assert args["expected_user_id"] == c.actor
    assert args["query"] == "New address"
    text = c.message.answer.await_args.args[0]
    assert "Current object" not in text
    assert text.index(c.items[1]["label"]) < text.index(c.items[2]["label"])
    assert c.items[1]["label"] not in repr(data)
    assert c.items[1]["entity_id"] not in repr(data)
    assert editor.selected(data, form, 123, data["last_menu_message_id"]) is not None
    c.service.patch_project.assert_not_awaited()
    c.service.read_created_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_requires_review_and_uses_original_sparse_versioned_patch(address_case):
    c = address_case
    await begin(c)
    await click(c, "save")
    c.service.patch_project.assert_not_awaited()
    await click(c, "address_continue")
    form = (await c.state.get_data())[editor.FORM]
    assert form["stage"] == "review" and form["expected_row_version"] == 7
    c.service.patch_project.assert_not_awaited()
    await click(c, "save")
    assert c.service.patch_project.await_args.kwargs["payload"] == {
        "expected_row_version": 7, "address_raw": "New address",
    }
    data = await c.state.get_data()
    assert data[editor.FORM]["stage"] == "saved"
    assert editor.selected(data, data[editor.FORM], 123, data["last_menu_message_id"]) is not None
    c.service.read_created_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_and_retry_preserve_original_version_and_invalidate_buttons(address_case):
    c = address_case
    await begin(c)
    old = deepcopy((await c.state.get_data())[editor.FORM])
    await click(c, "address_change")
    form = (await c.state.get_data())[editor.FORM]
    assert form["stage"] == "input" and "value" not in form
    assert form["expected_row_version"] == 7
    c.message.text = "Second address"
    await editor.project_edit_message(c.message, c.state)
    assert c.service.project_edit_address_suggestions.await_args.kwargs["query"] == "Second address"
    current = deepcopy((await c.state.get_data())[editor.FORM])
    c.callback.message.message_id = (await c.state.get_data())["last_menu_message_id"]
    c.callback.data = editor.cb(old, "address_continue")
    await editor.project_edit_callback(c.callback, c.state)
    assert (await c.state.get_data())[editor.FORM] == current
    await click(c, "address_retry")
    assert (await c.state.get_data())[editor.FORM]["expected_row_version"] == 7
    c.service.read_created_project.assert_awaited_once()
    c.service.patch_project.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 503])
async def test_unavailable_check_is_not_reported_as_no_matches(address_case, status):
    c = address_case
    c.service.project_edit_address_suggestions.side_effect = ConstructionApiClientError(
        status_code=status, code="unavailable",
    )
    await begin(c)
    form = (await c.state.get_data())[editor.FORM]
    assert form["address_suggestion_state"] == "unavailable"
    assert form["expected_row_version"] == 7
    text = c.message.answer.await_args.args[0]
    assert suggestions.address_tr("unavailable", "uk") in text
    assert suggestions.address_tr("empty", "uk") not in text
    await click(c, "address_continue")
    assert (await c.state.get_data())[editor.FORM]["stage"] == "review"
    c.service.patch_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_current_project_is_an_empty_comparison_result(address_case):
    c = address_case
    c.result["items"] = [deepcopy(c.items[0])]
    await begin(c)
    form = (await c.state.get_data())[editor.FORM]
    assert form["address_suggestion_state"] == "empty"
    assert form["address_suggestion_count"] == 0
    c.service.patch_project.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,stage", [(409, "conflict"), (504, "uncertain")])
async def test_patch_failure_is_not_rebased_or_repeated(address_case, status, stage):
    c = address_case
    await begin(c)
    await click(c, "address_continue")
    c.service.patch_project.side_effect = ConstructionApiClientError(
        status_code=status, code="write_failure",
    )
    await click(c, "save")
    assert (await c.state.get_data())[editor.FORM]["stage"] == stage
    assert c.service.patch_project.await_args.kwargs["payload"]["expected_row_version"] == 7
    await click(c, "save")
    c.service.patch_project.assert_awaited_once()
    c.service.read_created_project.assert_awaited_once()
    c.service.project_edit_address_suggestions.assert_awaited_once()


@pytest.mark.asyncio
async def test_navigation_during_read_cannot_restore_editor(address_case):
    c = address_case
    async def leave(**kwargs):
        await c.state.update_data(**{editor.CONTEXT: {}, editor.FORM: {}})
        return deepcopy(c.result)
    c.service.project_edit_address_suggestions.side_effect = leave
    await begin(c)
    data = await c.state.get_data()
    assert data[editor.CONTEXT] == {} and data[editor.FORM] == {}
    assert not any(c.items[1]["label"] in str(call) for call in c.message.answer.await_args_list)
    c.service.patch_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_during_render_cannot_restore_editor(address_case):
    c = address_case
    original = c.message.answer.side_effect
    async def answer(*args, **kwargs):
        if args and suggestions.address_tr("title", "uk") in args[0]:
            await c.state.update_data(**{editor.CONTEXT: {}, editor.FORM: {}})
        return await original(*args, **kwargs)
    c.message.answer.side_effect = answer
    await begin(c)
    data = await c.state.get_data()
    assert data[editor.CONTEXT] == {} and data[editor.FORM] == {}


@pytest.mark.asyncio
async def test_oversized_optional_query_preserves_draft_and_original_version(address_case):
    c = address_case
    address = "A" * 4097
    await begin(c, address)
    c.service.project_edit_address_suggestions.assert_not_awaited()
    form = (await c.state.get_data())[editor.FORM]
    assert form["value"] == address
    assert form["expected_row_version"] == 7
    assert form["address_suggestion_state"] == "unavailable"


@pytest.mark.asyncio
async def test_cancel_discards_address_without_patch(address_case):
    c = address_case
    await begin(c)
    await click(c, "cancel")
    form = (await c.state.get_data())[editor.FORM]
    assert form["stage"] == "cancelled" and "value" not in form
    c.service.patch_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_allows_existing_project_reader_without_creation_capability():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test"))
    project = uuid4()
    read = AsyncMock(return_value=NS(data=[]))
    api = NS(address_suggestions=read, project_details=AsyncMock(), patch_project=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        session, {"language": "uk", "can_create_project": False},
    ))
    options = dict(
        telegram_user_id=123, tenant_id=session.tenant_id, project_id=project,
        expected_user_id=session.user_id, query="New address",
    )
    result = await entry.project_edit_address_suggestions(**options)
    read.assert_awaited_once_with(session=session, query="New address")
    assert result["project_id"] == str(project)
    api.project_details.assert_not_awaited()
    api.patch_project.assert_not_awaited()
    read.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.project_edit_address_suggestions(**{**options, "expected_user_id": uuid4()})
    read.assert_not_awaited()
    with pytest.raises(ConstructionApiClientError):
        await entry.project_edit_address_suggestions(**{**options, "tenant_id": uuid4()})
    read.assert_not_awaited()


def test_translations_and_callbacks_are_bounded():
    assert all(len(values) == 7 for values in suggestions.LABELS.values())
    form = {"nonce": "a" * 32}
    for action in ("address_continue", "address_change", "address_retry"):
        assert len(editor.cb(form, action).encode("utf-8")) <= 64
