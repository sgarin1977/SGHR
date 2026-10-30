"""Project text editing with preserved parent navigation and frozen versions."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from handlers.construction import construction_project_areas as areas
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_project_edit import tr
from ui.texts import t

router = Router(name="construction_project_edit")
logger = logging.getLogger(__name__)
FORM = "construction_project_edit_form"
CONTEXT = "construction_suite_context"
FIELDS = ("name", "address_raw", "comment")
KEYS = (
    FORM, CONTEXT, areas.PROJECT, areas.FORM, areas.CARD, areas.CATALOG,
    "construction_project_catalog_view", "construction_client_catalog_view",
    "last_menu_message_id",
)

def button(text, command):
    return InlineKeyboardButton(text=text, callback_data=command)

def cb(form, action, selector=None):
    command = "PDE:" + action + ":" + form["nonce"]
    return command if selector is None else command + ":" + str(selector)

def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}

def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)

def selected(data, form, sender, menu):
    if (
        not isinstance(form, dict)
        or form.get("telegram_user_id") != sender
        or form.get("menu_id") != menu
        or menu != data.get("last_menu_message_id")
    ):
        return None
    try:
        current = areas.binding(
            data, key=form.get("source_key"), token=form.get("source_token"),
            sender=sender, menu=menu,
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if current is None or any(form.get(key) != value for key, value in current.items()):
        return None
    return current

def valid_value(field, value):
    if field not in FIELDS:
        return False
    if value is None:
        return field == "comment"
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value

async def project_edit_navigation_middleware(handler, event, data):
    state = data.get("state")
    if (
        state is not None and private(event)
        and not (event.data or "").startswith("PDE:")
    ):
        saved = (await state.get_data()).get(FORM)
        if isinstance(saved, dict) and saved.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{FORM: {}})
    return await handler(event, data)

async def render(event, state, form, text, rows, *, expected_snapshot=None):
    data = await state.get_data()
    if expected_snapshot is not None and not same(data, expected_snapshot):
        return
    parent = selected(data, form, event.from_user.id, data.get("last_menu_message_id"))
    if (expected_snapshot is None and data.get(FORM) != form) or parent is None:
        return
    saved = snapshot(data)
    message = getattr(event, "message", None) or event
    chunks = [text[index:index + 3000] for index in range(0, len(text), 3000)] or [text]
    sent = None
    for index, chunk in enumerate(chunks):
        if not same(await state.get_data(), saved):
            return
        markup = (
            InlineKeyboardMarkup(inline_keyboard=rows)
            if index == len(chunks) - 1 else None
        )
        sent = await message.answer(chunk, reply_markup=markup, parse_mode=None)
    if not same(await state.get_data(), saved):
        return
    key = parent["source_key"]
    values = {
        FORM: {**form, "menu_id": sent.message_id},
        key: {**data[key], "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    }
    if key == areas.PROJECT:
        from handlers.construction.construction_client_catalog import sync_origin
        values[areas.CARD] = {**data[areas.CARD], "menu_id": sent.message_id}
        values.update(sync_origin(data, data[areas.CARD], sent.message_id))
    elif key == areas.CATALOG:
        values["construction_project_catalog_view"] = {
            **data["construction_project_catalog_view"], "menu_id": sent.message_id,
        }
    await state.update_data(**values)

async def display(event, state, form, notice=None):
    if form.get("stage") == "address_suggestions":
        from handlers.construction.construction_project_edit_address_suggestions import (
            render as render_address_suggestions,
        )
        return await render_address_suggestions(event, state, form, notice=notice)
    language, stage = form["language"], form["stage"]
    rows = []
    if stage == "choose":
        text = tr("edit", language) + "\n\n" + tr("choose", language)
        rows = [
            [button(tr(field, language), cb(form, "field", index))]
            for index, field in enumerate(FIELDS)
        ]
    elif stage == "input":
        text = tr(form["field"], language) + "\n\n" + tr("enter", language)
        if form["field"] == "comment":
            rows.append([button(tr("clear", language), cb(form, "clear"))])
        if form["field"] == "address_raw":
            text += "\n\n" + tr("address_notice", language)
    elif stage == "review":
        text = (
            tr("review", language) + "\n\n" + tr(form["field"], language)
            + ":\n" + (form["value"] if form["value"] is not None else "—")
        )
        if form["field"] == "address_raw":
            text += "\n\n" + tr("address_notice", language)
        rows.append([button(tr("save", language), cb(form, "save"))])
    elif stage in ("saved", "cancelled", "conflict", "blocked", "uncertain"):
        text = tr(stage, language)
    else:
        raise ValueError("Unknown project edit stage.")
    if stage in ("choose", "input", "review"):
        rows.append([button(tr("cancel", language), cb(form, "cancel"))])
    rows.append([button(tr("back", language), form["back"])])
    if notice:
        text = notice + "\n\n" + text
    await render(event, state, form, text, rows)

async def start(callback, state, bot, event_update):
    data = await state.get_data()
    saved = snapshot(data)
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[1] != "open":
        raise ValueError("Invalid project edit selector.")
    choices = [
        value for key in (areas.PROJECT, areas.FORM, areas.CATALOG)
        if (value := areas.binding(
            data, key=key, token=parts[2], sender=callback.from_user.id,
            menu=callback.message.message_id,
        )) is not None
    ]
    if len(choices) != 1:
        raise ValueError("Project edit source is stale.")
    parent = choices[0]
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.read_created_project(
            telegram_user_id=callback.from_user.id,
            tenant_id=UUID(parent["tenant_id"]),
            project_id=UUID(parent["project_id"]),
            expected_user_id=UUID(parent["actor_user_id"]),
        )
    item = result["item"]
    if (
        not same(await state.get_data(), saved)
        or result["actor_user_id"] != parent["actor_user_id"]
        or item["id"] != parent["project_id"]
        or item["tenant_id"] != parent["tenant_id"]
        or (
            parent["client_id"] is not None
            and item["client_id"] != parent["client_id"]
        )
        or type(item["row_version"]) is not int
        or not 1 <= item["row_version"] <= 2**31-1
    ):
        raise ValueError("Project edit context changed.")
    form = {
        **parent, "expected_row_version": item["row_version"],
        "language": result.get("language") or callback.from_user.language_code or "ru",
        "stage": "choose", "nonce": uuid4().hex,
        "menu_id": callback.message.message_id, "attempted": False,
    }
    await state.update_data(**{FORM: form})
    await display(callback, state, form)

async def submit(callback, state, form, bot, event_update):
    if (
        form["stage"] != "review" or form["attempted"]
        or not valid_value(form.get("field"), form.get("value"))
    ):
        raise ValueError("Project edit is not ready.")
    field, value = form["field"], form["value"]
    payload = {"expected_row_version": form["expected_row_version"], field: value}
    form = {key: item for key, item in form.items() if key != "value"}
    form.update(stage="sending", attempted=True, nonce=uuid4().hex)
    await state.update_data(**{FORM: form})
    saved = snapshot(await state.get_data())
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.patch_project(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                project_id=UUID(form["project_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
                payload=payload,
            )
        item = result["item"]
        if (
            result["actor_user_id"] != form["actor_user_id"]
            or item["id"] != form["project_id"]
            or item["tenant_id"] != form["tenant_id"]
            or type(item["row_version"]) is not int
            or item["row_version"] not in (
                form["expected_row_version"], form["expected_row_version"] + 1,
            )
            or item[field] != value
        ):
            raise ValueError("Project edit response differs.")
        form["stage"] = "saved"
    except Exception as error:
        logger.warning(
            "construction_bot_project_edit_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        status = getattr(error, "status_code", None)
        form["stage"] = (
            "conflict" if status == 409 else
            "blocked" if status in (400, 401, 403, 404, 422, 429) else "uncertain"
        )
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{FORM: form})
    await display(callback, state, form)

@router.callback_query(F.data.startswith("PDE:"))
async def project_edit_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            if callback.data.startswith("PDE:open:"):
                await start(callback, state, bot, event_update)
                return
            data = await state.get_data()
            form = deepcopy(data.get(FORM))
            parts = callback.data.split(":")
            if (
                len(parts) not in (3, 4)
                or selected(data, form, callback.from_user.id, callback.message.message_id) is None
                or parts[2] != form.get("nonce")
            ):
                raise ValueError("Project editor is stale.")
            language = form["language"]
            action = parts[1]
            if form.get("stage") == "address_suggestions":
                from handlers.construction.construction_project_edit_address_suggestions import dispatch
                if len(parts) != 3:
                    raise ValueError("Invalid address suggestion action.")
                if await dispatch(
                    callback, state, form, action, bot=bot, event_update=event_update,
                ):
                    return
            if (
                action == "field" and len(parts) == 4 and form["stage"] == "choose"
                and parts[3] in {str(index) for index in range(len(FIELDS))}
            ):
                form.update(stage="input", field=FIELDS[int(parts[3])])
            elif (
                action == "clear" and len(parts) == 3
                and form["stage"] == "input" and form["field"] == "comment"
            ):
                form.update(stage="review", value=None)
            elif action == "cancel" and len(parts) == 3 and not form["attempted"]:
                form.pop("value", None)
                form["stage"] = "cancelled"
            elif action == "save" and len(parts) == 3:
                await submit(callback, state, form, bot, event_update)
                return
            else:
                raise ValueError("Project edit action is unavailable.")
            form["nonce"] = uuid4().hex
            await state.update_data(**{FORM: form})
            await display(callback, state, form)
        except Exception as error:
            logger.warning(
                "construction_bot_project_edit_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(
                t("construction_lead_form_stale", language), parse_mode=None,
            )

class ProjectFieldInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form = data.get(FORM)
        return (
            selected(data, form, message.from_user.id, data.get("last_menu_message_id")) is not None
            and form.get("stage") == "input" and not form.get("attempted")
            and form.get("field") in FIELDS
        )

@router.message(ProjectFieldInput())
async def project_edit_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ProjectFieldInput()(message, state):
            return
        form = deepcopy((await state.get_data())[FORM])
        value = message.text.strip()
        if not valid_value(form["field"], value):
            await display(message, state, form, tr("invalid", form["language"]))
            return
        form.update(stage="review", value=value, nonce=uuid4().hex)
        await state.update_data(**{FORM: form})
        if form["field"] == "address_raw":
            from handlers.construction.construction_project_edit_address_suggestions import lookup
            await lookup(message, state, form, bot=bot, event_update=event_update)
            return
        await display(message, state, form)

# project_edit_address_suggestions_v1
