"""Original header form; prepared body and retry key remain immutable."""
from copy import deepcopy
from html import escape
from uuid import UUID, uuid4

from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_drafts as drafts
from handlers.construction.construction_lead_create import private, lock_for
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_template_draft_header_bot_api import (
    TemplateDraftHeaderBotApi, text_value,
)
from services.construction.construction_language_bot_api import problem
from ui.construction_template_draft_header import tr


def button(view, key, action):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cards.cb(view, action),
    )


def recovery_rows(view, data):
    if drafts.state_map(data, cards.HEADER_PENDING).get(drafts.slot(view)):
        return [[button(view, "retry", "hdr_retry")]]
    return []


def header_rows(view, data):
    rows = recovery_rows(view, data)
    if rows:
        return rows
    return [
        [button(view, "title", "hdr_title")],
        [button(view, "description", "hdr_description")],
    ]


async def show(event, state, saved, view, text, rows):
    if getattr(event, "message", None) is not None:
        await cards.show(event, state, saved, view, text, rows)
        return
    if not cards.same(await state.get_data(), saved):
        return
    sent = await event.answer(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not cards.same(await state.get_data(), saved):
        return
    await state.update_data(**{
        cards.VIEW: {**view, "menu_id": sent.message_id},
        cards.shell.VIEW: {}, "last_menu_message_id": sent.message_id,
    })


def draft_selector(item):
    return {
        name: item[name] for name in (
            "template_id", "version_id", "version", "version_row_version",
            "template_row_version", "definition_hash",
        )
    }


async def input_form(callback, state, saved, view, api, field, *, kind=None, item_id=None):
    result = await api.read_draft(
        template_id=UUID(view["draft"]["template_id"]),
        version_id=UUID(view["draft"]["version_id"]),
        include_content=True, **cards.binding(view),
    )
    item = result["item"]
    if (
        item["version_row_version"] != view["draft"]["version_row_version"]
        or item["definition_hash"] != view["draft"]["definition_hash"]
    ):
        raise problem(409, "ROW_VERSION_CONFLICT")
    preview = None
    if kind is None:
        original = item[field]
    else:
        if field not in ("label", "sort_order"):
            raise ValueError("An editable item field is required.")
        from services.construction.construction_template_draft_label_bot_api import locate
        target = locate(item, kind, item_id)
        labels = item["definition"]["labels"][item["source_language"]]
        if field == "sort_order":
            original = target["sort_order"]
            siblings = item["definition"]["sections"] if kind == "SECTION" else next(
                section["questions"] for section in item["definition"]["sections"]
                if any(question is target for question in section["questions"])
            )
            position = siblings.index(target)
            nearby = siblings[max(0, position - 3):position + 4]
            preview = "\n".join(
                ("→ " if row is target else "") + str(row["sort_order"])
                + " · " + labels[row["label_key"]]
                for row in nearby
            )
        else:
            original = labels[target["label_key"]]
    view = {
        **cards.fresh(view, result, "header_input"),
        "draft": draft_selector(item),
        "header_edit": {
            "template_id": item["template_id"], "version_id": item["version_id"],
            "expected_row_version": item["version_row_version"],
            "expected_definition_hash": item["definition_hash"], "field": field,
            **({"kind": kind, "item_id": str(item_id)} if kind is not None else {}),
        },
    }
    language = view["language"]
    text = (
        tr(field, language)
        + "\n\n" + tr("original_language", language) + ": " + item["source_language"]
        + "\n\n" + tr("order_current" if field == "sort_order" else "current", language) + ":\n"
        + (tr("empty", language) if original is None else escape(str(original)))
        + "\n\n" + tr("order_enter" if field == "sort_order" else "enter", language)
        + ("" if preview is None else "\n\n" + tr("order_neighbours", language) + ":\n" + escape(preview))
    )
    rows = [[button(view, "clear", "hdr_clear")]] if field == "description" else []
    rows.append([button(view, "cancel", "hdr_back")])
    await show(callback, state, saved, view, text, rows)


async def prepare(event, state, saved, view, api, value):
    edit = view["header_edit"]
    result = await api.prepare_header(
        template_id=UUID(edit["template_id"]), version_id=UUID(edit["version_id"]),
        expected_row_version=edit["expected_row_version"],
        expected_definition_hash=edit["expected_definition_hash"],
        field=edit["field"], value=value,
        **({name: edit[name] for name in ("kind", "item_id")} if "kind" in edit else {}),
        **cards.binding(view),
    )
    next_view = {
        **cards.fresh(view, result, "header_review"),
        "draft": deepcopy(view["draft"]), "header_request": deepcopy(result["request"]),
    }
    language = next_view["language"]
    old = tr("empty", language) if result["original"] is None else escape(str(result["original"]))
    new = tr("empty", language) if result["value"] is None else escape(str(result["value"]))
    is_order = edit["field"] == "sort_order"
    text = (
        tr("order_review" if is_order else "review", language)
        + "\n\n" + tr("original_language", language) + ": " + result["request"]["source_language"]
        + "\n\n" + tr(edit["field"], language) + ":\n" + old + "\n→\n" + new
        + "\n\n" + tr("order_note" if is_order else "translation_note", language)
    )
    await show(event, state, saved, next_view, text, [
        [button(next_view, "save", "hdr_save")],
        [button(next_view, "cancel", "hdr_back")],
    ])


async def execute(callback, state, saved, view, api, *, retry):
    data = await state.get_data()
    if not cards.same(data, saved):
        return
    operations = drafts.state_map(data, cards.HEADER_PENDING)
    key = drafts.slot(view)
    if retry:
        operation = operations.get(key)
        if not isinstance(operation, dict):
            return
    else:
        if view.get("phase") != "header_review" or key in operations:
            return
        operation = {**deepcopy(view["header_request"]), "key": uuid4().hex}
        operations[key] = deepcopy(operation)
    sending = {**view, "phase": "sending", "nonce": uuid4().hex}
    await state.update_data(**{cards.VIEW: sending, cards.HEADER_PENDING: operations})
    saved = {
        **saved, cards.VIEW: deepcopy(sending), cards.HEADER_PENDING: deepcopy(operations),
    }
    if not cards.same(await state.get_data(), saved):
        return
    try:
        result = await api.write_header(request=deepcopy(operation), **cards.binding(view))
    except Exception as error:
        cards.logger.warning(
            "construction_bot_draft_header_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not cards.same(await state.get_data(), saved):
            return
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        definite = status in (400, 422) or code in (
            "ROW_VERSION_CONFLICT", "TEMPLATE_VERSION_CONFLICT",
        )
        view = {
            **cards.fresh(view, {"actor_user_id": view["actor_user_id"], "language": view["language"]},
                          "blocked" if definite else "header_pending"),
            "draft": {
                "template_id": operation["template_id"], "version_id": operation["version_id"],
            },
        }
        if definite:
            operations.pop(key, None)
            await state.update_data(**{cards.HEADER_PENDING: operations})
            saved = {**saved, cards.HEADER_PENDING: deepcopy(operations)}
            invalid = (
                "invalid_order" if set(operation.get("body", {}).get("fields", {})) == {"sort_order"}
                else "invalid_change"
            )
            text = tr(invalid if status in (400, 422) else "conflict", view["language"])
            rows = cards.back_rows(view)
        else:
            text = tr("pending", view["language"])
            rows = [[button(view, "retry", "hdr_retry")], *cards.back_rows(view)]
        await show(callback, state, saved, view, text, rows)
        return
    if not cards.same(await state.get_data(), saved):
        return
    operations.pop(key, None)
    await state.update_data(**{cards.HEADER_PENDING: operations})
    saved = {**saved, cards.HEADER_PENDING: deepcopy(operations)}
    view = {
        **cards.fresh(view, result, "header_saved"),
        "draft": {
            "template_id": result["item"]["template_id"],
            "version_id": result["item"]["version_id"],
        },
    }
    await show(callback, state, saved, view, tr("saved", view["language"]), [
        [button(view, "refresh_draft", "hdr_back")], *cards.back_rows(view),
    ])


async def failure(event, state, saved, view, error):
    cards.logger.warning(
        "construction_bot_draft_header_failed error_type=%s status=%s code=%s",
        type(error).__name__, getattr(error, "status_code", None),
        getattr(error, "code", None),
    )
    view = {
        **cards.fresh(view, {"actor_user_id": view["actor_user_id"], "language": view["language"]},
                      "blocked"),
    }
    notice = (
        "conflict" if getattr(error, "status_code", None) == 409
        else "invalid_order" if getattr(error, "code", None) == "TEMPLATE_ORDER_INVALID"
        else "unavailable"
    )
    await show(event, state, saved, view, tr(notice, view["language"]), cards.back_rows(view))


async def handle_header_action(
    callback, state, saved, view, action, *, bot=None, event_update=None,
):
    phase = view.get("phase")
    if action in ("hdr_title", "hdr_description") and phase != "draft":
        return
    if action == "hdr_clear" and (
        phase != "header_input" or view["header_edit"]["field"] != "description"
    ):
        return
    if action == "hdr_save" and phase != "header_review":
        return
    if action == "hdr_back" and not isinstance(view.get("draft"), dict):
        return
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            api = TemplateDraftHeaderBotApi(service)
            if action in ("hdr_save", "hdr_retry"):
                await execute(callback, state, saved, view, api, retry=action == "hdr_retry")
            elif action == "hdr_back":
                await drafts.open_draft(callback, state, saved, view, api, refresh=True)
            elif drafts.state_map(await state.get_data(), cards.HEADER_PENDING).get(drafts.slot(view)):
                await show(callback, state, saved, view, tr("pending", view["language"]), [
                    [button(view, "retry", "hdr_retry")], *cards.back_rows(view),
                ])
            elif action == "hdr_clear":
                await prepare(callback, state, saved, view, api, None)
            else:
                field = "title" if action == "hdr_title" else "description"
                await input_form(callback, state, saved, view, api, field)
    except Exception as error:
        await failure(callback, state, saved, view, error)


class HeaderInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(getattr(message, "text", None), str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(cards.VIEW)
        return (
            cards.shell.bound(data, view, message.from_user.id, data.get("last_menu_message_id"))
            and view.get("phase") == "header_input"
        )


async def header_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await HeaderInput()(message, state):
            return
        data = await state.get_data()
        view, saved = deepcopy(data[cards.VIEW]), cards.snapshot(data)
        try:
            field = view["header_edit"]["field"]
            if field == "sort_order":
                from services.construction.construction_template_draft_label_bot_api import order_value
                value = order_value(message.text)
            else:
                value = text_value("title" if field == "label" else field, message.text)
        except ValueError:
            await message.answer(tr("order_enter" if field == "sort_order" else "enter", view["language"]))
            return
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                await prepare(
                    message, state, saved, view, TemplateDraftHeaderBotApi(service), value,
                )
        except Exception as error:
            await failure(message, state, saved, view, error)
