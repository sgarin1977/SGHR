"""Explicit single-field organizational edits through the existing area API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardMarkup

from handlers.construction import construction_project_areas as areas
from handlers.construction.construction_area_create import (
    selected_for, suspend_area_form, suspend_others,
)
from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_area_edit import tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_area_edit")
logger = logging.getLogger(__name__)
FORM = "construction_area_edit_form"
FIELDS = {"name": "name", "order": "sort_order"}
KEYS = (
    FORM, areas.VIEW, areas.CONTEXT, areas.PROJECT, areas.FORM,
    areas.CARD, areas.CATALOG, "construction_project_catalog_view",
    "construction_client_catalog_view", "last_menu_message_id",
)

def snapshot(data):
    return deepcopy({key: data.get(key) for key in KEYS})

def unchanged(data, before):
    return all(data.get(key) == value for key, value in before.items())

def cb(form, action):
    return f'AE:{action}:{form["id"]}:{form["revision"]}'

def live(form, data, sender):
    if (
        not isinstance(form, dict) or not form
        or data.get(FORM) != form
        or form.get("telegram_user_id") != sender
        or form.get("menu_id") != data.get("last_menu_message_id")
    ):
        return False
    view = data.get(areas.VIEW, {})
    return (
        isinstance(view, dict)
        and view.get("nonce") == form.get("area_nonce")
        and selected_for(data, sender, form.get("menu_id")) == form.get("selected")
    )

def parse_value(field, text):
    if not isinstance(text, str):
        raise ValueError("Text is required.")
    value = text.strip()
    if field == "name":
        if not value or len(value) > 4096:
            raise ValueError("Area name is invalid.")
        return value
    if field == "sort_order":
        number = int(value)
        if str(number) != value or not -2147483648 <= number <= 2147483647:
            raise ValueError("Area order is invalid.")
        return number
    raise ValueError("Area field is invalid.")

async def render(event, state, form, *, notice=None, details=None):
    data = await state.get_data()
    if not live(form, data, event.from_user.id):
        return
    before = snapshot(data)
    selected = form["selected"]
    view = {**data[areas.VIEW], "nonce": uuid4().hex}
    updated = {
        **form, "revision": form["revision"] + 1,
        "area_nonce": view["nonce"],
    }
    language = form["language"]
    lines = [tr("title", language)]
    if notice:
        lines.append(notice)
    if details is not None:
        order = details["sort_order"]
        lines.extend([
            tr("name", language) + ": " + details["name"],
            tr("order", language) + ": "
            + (tr("unset", language) if order is None else str(order)),
        ])
    rows = []
    phase = updated["phase"]
    if phase == "choose":
        lines.append(tr("choose", language))
        for field in FIELDS:
            rows.append([areas.button(tr(field, language), cb(updated, field))])
    elif phase == "input":
        lines.append(tr(
            "enter_name" if updated["field"] == "name" else "enter_order",
            language,
        ))
        if updated["field"] == "sort_order":
            rows.append([areas.button(tr("clear", language), cb(updated, "clear"))])
    elif phase == "review":
        value = updated["value"]
        lines.extend([
            tr("review", language),
            tr("name" if updated["field"] == "name" else "order", language)
            + ": " + (tr("unset", language) if value is None else str(value)),
        ])
        rows.extend([
            [areas.button(tr("save", language), cb(updated, "save"))],
            [areas.button(tr("fields", language), cb(updated, "fields"))],
        ])
    elif phase == "done":
        lines.append(tr("saved", language))
    elif phase == "cancelled":
        lines.append(tr("cancelled", language))
    elif phase != "blocked":
        raise ValueError("Invalid area editor phase.")
    if phase in ("choose", "input", "review"):
        rows.append([areas.button(tr("cancel", language), cb(updated, "cancel"))])
    rows.append([areas.button(
        tr("list", language),
        f'PA:page:{view["nonce"]}:{view["offset"]}',
    )])
    rows.append([areas.button(
        t("construction_bot_home_btn", language), "CS_HOME",
    )])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "\n\n".join(lines)
    if not unchanged(await state.get_data(), before):
        return
    if getattr(event, "message", None) is not None and len(text) <= 3800:
        sent = await edit_or_replace_menu_message(
            callback=event, text=text, reply_markup=markup,
        )
    else:
        message = getattr(event, "message", None) or event
        chunks = [text[index:index + 1800] for index in range(0, len(text), 1800)]
        for index, chunk in enumerate(chunks):
            if not unchanged(await state.get_data(), before):
                return
            sent = await message.answer(
                chunk, parse_mode=None,
                reply_markup=markup if index == len(chunks) - 1 else None,
            )
    current = await state.get_data()
    if not unchanged(current, before):
        return
    key = selected["source_key"]
    values = {
        FORM: {} if phase == "cancelled" else {
            **updated, "menu_id": sent.message_id,
        },
        areas.VIEW: {**view, "menu_id": sent.message_id},
        key: {**current[key], "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    }
    if key == areas.PROJECT:
        values[areas.CARD] = {**current[areas.CARD], "menu_id": sent.message_id}
        from handlers.construction.construction_client_catalog import sync_origin
        values.update(sync_origin(current, current[areas.CARD], sent.message_id))
    if key == areas.CATALOG:
        values["construction_project_catalog_view"] = {
            **current["construction_project_catalog_view"],
            "menu_id": sent.message_id,
        }
    await state.update_data(**values)

async def save(callback, state, form, *, bot, event_update):
    payload = {
        "expected_row_version": form["expected_row_version"],
        form["field"]: form["value"],
    }
    saving = {**form, "phase": "saving"}
    await state.update_data(**{FORM: saving})
    try:
        selected = saving["selected"]
        async with construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            result = await service.patch_project_area(
                telegram_user_id=callback.from_user.id,
                tenant_id=UUID(selected["tenant_id"]),
                project_id=UUID(selected["project_id"]),
                area_id=UUID(saving["area_id"]),
                expected_user_id=UUID(selected["actor_user_id"]),
                payload=payload,
            )
        if not live(saving, await state.get_data(), callback.from_user.id):
            return
        item = result["item"]
        if (
            result["actor_user_id"] != selected["actor_user_id"]
            or result["project_id"] != selected["project_id"]
            or item["id"] != saving["area_id"]
            or item["tenant_id"] != selected["tenant_id"]
            or item["project_id"] != selected["project_id"]
            or type(item["row_version"]) is not int
            or item["row_version"] not in (
                saving["expected_row_version"], saving["expected_row_version"] + 1,
            )
            or item[saving["field"]] != saving["value"]
        ):
            raise ValueError("Area edit response binding differs.")
        done = {
            **saving, "phase": "done",
            "field": None, "value": None,
        }
        await state.update_data(**{FORM: done})
        await render(callback, state, done)
    except Exception as error:
        logger.warning(
            "construction_bot_area_edit_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not live(saving, await state.get_data(), callback.from_user.id):
            return
        status = getattr(error, "status_code", None)
        if status == 409:
            notice = tr("conflict", saving["language"])
        elif status in (401, 403, 404):
            notice = t("construction_bot_unavailable", saving["language"])
        elif status == 429:
            notice = t("construction_bot_rate_limited", saving["language"])
        else:
            notice = tr("uncertain", saving["language"])
        blocked = {
            **saving, "phase": "blocked",
            "field": None, "value": None,
        }
        await state.update_data(**{FORM: blocked})
        await render(callback, state, blocked, notice=notice)

@router.callback_query(F.data.startswith("AE:"))
async def edit_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                return
            data = await state.get_data()
            parts = callback.data.split(":")
            if len(parts) != 4:
                raise ValueError("Invalid area editor selector.")
            if parts[1] == "open":
                view = data.get(areas.VIEW, {})
                selected = selected_for(
                    data, callback.from_user.id, callback.message.message_id,
                )
                index = int(parts[3])
                editable = view.get("editable_areas", [])
                if (
                    selected is None or view.get("nonce") != parts[2]
                    or str(index) != parts[3]
                    or not isinstance(editable, list)
                    or not 0 <= index < len(editable) <= 5
                ):
                    raise ValueError("Area selection is stale.")
                selector = deepcopy(editable[index])
                if (
                    set(selector) != {"id", "row_version"}
                    or str(UUID(selector["id"])) != selector["id"]
                    or type(selector["row_version"]) is not int
                    or selector["row_version"] < 1
                ):
                    raise ValueError("Invalid area selection.")
                before = snapshot(data)
                async with construction_bot_api_entry(
                    bot=bot, event_update=event_update,
                ) as service:
                    result = await service.project_areas(
                        telegram_user_id=callback.from_user.id,
                        tenant_id=UUID(selected["tenant_id"]),
                        project_id=UUID(selected["project_id"]),
                        expected_user_id=UUID(selected["actor_user_id"]),
                        client_id=UUID(selected["client_id"])
                        if selected["client_id"] else None,
                    )
                if not unchanged(await state.get_data(), before):
                    return
                if (
                    result["actor_user_id"] != selected["actor_user_id"]
                    or result["project_id"] != selected["project_id"]
                    or result.get("can_edit_area") is not True
                ):
                    raise ConstructionApiClientError(
                        status_code=403, code="forbidden",
                    )
                matches = [
                    item for item in result["items"]
                    if item["id"] == selector["id"]
                ]
                if len(matches) != 1:
                    raise ValueError("Area is no longer available.")
                item = matches[0]
                if item["row_version"] != selector["row_version"]:
                    await callback.message.answer(tr("conflict", language))
                    return
                if (
                    item["tenant_id"] != selected["tenant_id"]
                    or item["project_id"] != selected["project_id"]
                ):
                    raise ValueError("Area context differs.")
                await suspend_area_form(state)
                await suspend_others(state, selected)
                # Recheck navigation after suspending unrelated unfinished forms.
                current = await state.get_data()
                if (
                    selected_for(
                        current, callback.from_user.id, callback.message.message_id,
                    ) != selected
                    or current.get(areas.VIEW) != data.get(areas.VIEW)
                ):
                    return
                form = {
                    "id": uuid4().hex, "revision": 0,
                    "telegram_user_id": callback.from_user.id,
                    "selected": selected, "area_id": selector["id"],
                    "expected_row_version": selector["row_version"],
                    "area_nonce": view["nonce"],
                    "menu_id": callback.message.message_id,
                    "language": result["language"] or language,
                    "phase": "choose", "field": None, "value": None,
                }
                await state.update_data(**{FORM: form})
                await render(callback, state, form, details=item)
                return

            form = data.get(FORM)
            if (
                not live(form, data, callback.from_user.id)
                or form["id"] != parts[2]
                or str(form["revision"]) != parts[3]
                or callback.message.message_id != form["menu_id"]
            ):
                raise ValueError("Area editor is stale.")
            action, phase = parts[1], form["phase"]
            if action == "cancel" and phase in ("choose", "input", "review"):
                changed = {**form, "phase": "cancelled", "field": None, "value": None}
            elif action in FIELDS and phase == "choose":
                changed = {
                    **form, "phase": "input",
                    "field": FIELDS[action], "value": None,
                }
            elif action == "clear" and phase == "input" and form["field"] == "sort_order":
                changed = {**form, "phase": "review", "value": None}
            elif action == "fields" and phase == "review":
                changed = {**form, "phase": "choose", "field": None, "value": None}
            elif action == "save" and phase == "review":
                await save(
                    callback, state, form, bot=bot, event_update=event_update,
                )
                return
            else:
                raise ValueError("Area editor action is stale.")
            await state.update_data(**{FORM: changed})
            await render(callback, state, changed)
        except Exception as error:
            logger.warning(
                "construction_bot_area_editor_denied error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            key = (
                "construction_bot_unavailable"
                if getattr(error, "status_code", None) in (401, 403, 404)
                else "construction_lead_form_stale"
            )
            await callback.message.answer(t(key, language))

class AreaEditInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(message.text, str)
            or message.text.startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        form = data.get(FORM)
        return (
            live(form, data, message.from_user.id)
            and form.get("phase") == "input"
        )

@router.message(AreaEditInput())
async def edit_input(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await AreaEditInput()(message, state):
            return
        form = (await state.get_data())[FORM]
        try:
            value = parse_value(form["field"], message.text)
        except (ValueError, TypeError):
            await render(message, state, form, notice=tr("invalid", form["language"]))
            return
        changed = {**form, "phase": "review", "value": value}
        await state.update_data(**{FORM: changed})
        await render(message, state, changed)

# universal_area_editor_v1
