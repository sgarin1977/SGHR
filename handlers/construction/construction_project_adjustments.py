"""Project-bound adjustment forms preserving all existing card origins."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import logging
import re
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import private, lock_for
from handlers.construction import construction_project_areas as areas
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_project_adjustment_bot_api import ProjectAdjustmentBotApi
from services.construction.construction_language_bot_api import problem
from ui.construction_project_adjustments import tr, fields_text
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_project_adjustments_bot")
logger = logging.getLogger(__name__)
VIEW = "construction_project_adjustments_view"
PENDING = "construction_project_adjustment_pending_requests"
FIELDS = ("name", "coefficient", "enabled")
SOURCE_FIELDS = (
    "source_key", "source_token", "telegram_user_id", "tenant_id",
    "project_id", "actor_user_id", "client_id", "back",
)
KEYS = (
    VIEW, PENDING, areas.CONTEXT, areas.PROJECT, areas.FORM, areas.CARD,
    areas.CATALOG, "construction_project_catalog_view",
    "construction_client_catalog_view", "last_menu_message_id",
)


def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}


def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)


def selected(data, view, sender, menu):
    if (
        not isinstance(view, dict) or view.get("telegram_user_id") != sender
        or view.get("menu_id") != menu or data.get("last_menu_message_id") != menu
    ):
        return None
    try:
        parent = areas.binding(
            data, key=view["source_key"], token=view["source_token"],
            sender=sender, menu=menu,
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if parent is None or any(view.get(key) != parent[key] for key in SOURCE_FIELDS):
        return None
    return parent


def binding(view):
    return {
        "telegram_user_id": view["telegram_user_id"],
        "tenant_id": UUID(view["tenant_id"]),
        "project_id": UUID(view["project_id"]),
        "expected_user_id": UUID(view["actor_user_id"]),
        "expected_client_id": UUID(view["client_id"]) if view.get("client_id") else None,
    }


def slot(view):
    return ":".join(view[key] for key in ("tenant_id", "actor_user_id", "project_id"))


def pending_map(data):
    value = data.get(PENDING, {})
    if not isinstance(value, dict):
        raise ValueError("Pending adjustment state differs.")
    return deepcopy(value)


def fresh(view, result, phase):
    return {
        **{key: view[key] for key in SOURCE_FIELDS},
        "menu_id": view["menu_id"], "language": result["language"],
        "phase": phase, "nonce": uuid4().hex,
    }


def cb(view, action, index=None):
    value = "PJA:" + action + ":" + view["nonce"]
    return value if index is None else value + ":" + str(index)


def button(view, key, action, index=None):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cb(view, action, index),
    )


def back_rows(view):
    return [
        [button(view, "list", "list")],
        [InlineKeyboardButton(text=tr("project", view["language"]),
                              callback_data=view["back"])],
    ]


async def show(event, state, saved, view, text, rows):
    data = await state.get_data()
    parent = selected(data, view, view["telegram_user_id"], data.get("last_menu_message_id"))
    if not same(data, saved) or parent is None:
        return
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if getattr(event, "message", None) is not None:
        sent = await edit_or_replace_menu_message(
            callback=event, text=text, reply_markup=markup,
        )
    else:
        sent = await event.answer(text, reply_markup=markup)
    current = await state.get_data()
    if not same(current, saved):
        return
    key = parent["source_key"]
    values = {
        VIEW: {**view, "menu_id": sent.message_id},
        key: {**current[key], "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    }
    if key == areas.PROJECT:
        values[areas.CARD] = {**current[areas.CARD], "menu_id": sent.message_id}
        from handlers.construction.construction_client_catalog import sync_origin
        values.update(sync_origin(current, current[areas.CARD], sent.message_id))
    if key == areas.CATALOG:
        values["construction_project_catalog_view"] = {
            **current["construction_project_catalog_view"], "menu_id": sent.message_id,
        }
    await state.update_data(**values)


async def navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("PJA:"):
        if (await state.get_data()).get(VIEW):
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


def parse_coefficient(value):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("Invalid coefficient.")
    text = value.strip()
    if re.fullmatch(r"(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)", text) is None:
        raise ValueError("An exact decimal number is required.")
    text = text.replace(",", ".")
    try:
        number = Decimal(text)
        if not number.is_finite() or number <= 0:
            raise ValueError("A positive coefficient is required.")
    except InvalidOperation:
        raise ValueError("Invalid coefficient.") from None
    return text


async def list_page(event, state, saved, view, api, cursor=None):
    result = await api.list_adjustments(cursor=cursor, **binding(view))
    if not same(await state.get_data(), saved):
        return
    view = fresh(view, result, "list")
    view["adjustment_ids"] = [item["id"] for item in result["items"]]
    view["next_cursor"] = result["next_cursor"]
    text = tr("title", view["language"]) + "\n\n" + result["project_name"][:200]
    if not result["items"]:
        text += "\n\n" + tr("empty", view["language"])
    rows = [
        [InlineKeyboardButton(
            text=item["name"][:60] + " · " + item["coefficient"][:30]
                 + " · " + tr("on" if item["enabled"] else "off", view["language"]),
            callback_data=cb(view, "open", index),
        )]
        for index, item in enumerate(result["items"])
    ]
    if result["next_cursor"]:
        rows.append([button(view, "next", "next")])
    pending = pending_map(await state.get_data()).get(slot(view))
    if pending:
        text += "\n\n" + tr("pending", view["language"])
        rows.append([button(view, "retry", "retry")])
    elif result["can_manage"]:
        rows.append([button(view, "new", "new")])
    rows.extend([
        [button(view, "refresh", "list")],
        [InlineKeyboardButton(text=tr("project", view["language"]),
                              callback_data=view["back"])],
    ])
    await show(event, state, saved, view, text, rows)


async def card(event, state, saved, view, result, notice=None):
    item = result["item"]
    view = {**fresh(view, result, "card"), "adjustment_id": item["id"]}
    text = fields_text({field: item[field] for field in FIELDS}, view["language"])
    text += "\n\n" + tr("note", view["language"])
    if notice:
        text = notice + "\n\n" + text
    rows = []
    if pending_map(await state.get_data()).get(slot(view)):
        rows.append([button(view, "retry", "retry")])
    elif result["can_manage"]:
        rows.extend([
            [InlineKeyboardButton(
                text=tr("edit", view["language"]) + ": " + tr(field, view["language"]),
                callback_data=cb(view, "field", index),
            )]
            for index, field in enumerate(FIELDS)
        ])
    rows.extend(back_rows(view))
    await show(event, state, saved, view, text, rows)


async def form(event, state, saved, view):
    view = {**view, "nonce": uuid4().hex}
    language = view["language"]
    rows = []
    if view["phase"] == "input":
        text = tr("enter_name" if view["field"] == "name" else "enter_coefficient", language)
    elif view["phase"] == "enabled":
        text = tr("enabled", language)
        rows = [[button(view, "on", "enabled", 0)], [button(view, "off", "enabled", 1)]]
    elif view["phase"] == "review":
        text = tr("review", language) + "\n\n" + fields_text(view["fields"], language)
        rows.append([button(view, "save", "save")])
    else:
        raise ValueError("Unknown adjustment form phase.")
    rows.append([button(view, "cancel", "list")])
    await show(event, state, saved, view, text, rows)


async def require_edit(api, view):
    _, result = await api.context(**binding(view))
    if result["can_manage"] is not True:
        raise problem(403, "permission_required")
    return result


async def execute(event, state, view, api, retry):
    operations = pending_map(await state.get_data())
    key = slot(view)
    if retry:
        operation = operations.get(key)
        if not isinstance(operation, dict):
            return
    else:
        if view.get("phase") != "review" or key in operations:
            return
        body = deepcopy(view["fields"])
        if not view["create"]:
            body["expected_row_version"] = view["expected_row_version"]
        operation = {
            "operation": "create" if view["create"] else "patch",
            "project_id": view["project_id"],
            "adjustment_id": view.get("adjustment_id"),
            "body": body, "key": uuid4().hex,
        }
        operations[key] = deepcopy(operation)
    view = {**view, "phase": "pending", "nonce": uuid4().hex}
    await state.update_data(**{VIEW: view, PENDING: operations})
    saved = snapshot(await state.get_data())
    try:
        result = await api.write_adjustment(request=deepcopy(operation), **binding(view))
    except Exception as error:
        logger.warning(
            "construction_bot_adjustment_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not same(await state.get_data(), saved):
            return
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        definite = status in (400, 404, 422) or status == 409 and code != "idempotency_in_progress"
        if definite:
            operations.pop(key, None)
            await state.update_data(**{PENDING: operations})
            saved = snapshot(await state.get_data())
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            text, rows = tr("conflict", view["language"]), back_rows(view)
        else:
            text = tr("pending", view["language"])
            rows = [[button(view, "retry", "retry")]] + back_rows(view)
        await show(event, state, saved, view, text, rows)
        return
    if not same(await state.get_data(), saved):
        return
    operations.pop(key, None)
    await state.update_data(**{PENDING: operations})
    await card(event, state, snapshot(await state.get_data()), view, result,
               tr("saved", result["language"]))


@router.callback_query(F.data.startswith("PJA:"))
async def adjustment_callback(callback, state, bot=None, event_update=None):
    if not private(callback):
        await callback.answer(show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        view, saved = None, None
        try:
            if await state.get_state() is not None:
                return
            data = await state.get_data()
            parts = callback.data.split(":")
            if len(parts) == 3 and parts[1] == "open":
                candidates = [
                    value for key in (areas.PROJECT, areas.FORM, areas.CATALOG)
                    if (value := areas.binding(
                        data, key=key, token=parts[2],
                        sender=callback.from_user.id, menu=callback.message.message_id,
                    )) is not None
                ]
                if len(candidates) != 1:
                    return
                view = {
                    **candidates[0], "menu_id": callback.message.message_id,
                    "language": callback.from_user.language_code or "ru",
                }
                action, index = "list", None
            else:
                view = deepcopy(data.get(VIEW))
                if (
                    len(parts) not in (3, 4)
                    or selected(data, view, callback.from_user.id, callback.message.message_id) is None
                    or view.get("nonce") != parts[2]
                ):
                    return
                action, index = parts[1], None
                if action in ("open", "field", "enabled"):
                    if len(parts) != 4 or parts[3] not in {str(n) for n in range(10)}:
                        return
                    index = int(parts[3])
                elif len(parts) != 3 or action not in ("list", "next", "new", "save", "retry"):
                    return
            saved = snapshot(data)
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = ProjectAdjustmentBotApi(service)
                if action in ("save", "retry"):
                    await execute(callback, state, view, api, action == "retry")
                    return
                if action in ("list", "next"):
                    cursor = view.get("next_cursor") if action == "next" else None
                    if action == "next" and (view["phase"] != "list" or not cursor):
                        return
                    await list_page(callback, state, saved, view, api, cursor)
                    return
                if action == "open":
                    ids = view.get("adjustment_ids", [])
                    if view["phase"] != "list" or not 0 <= index < len(ids):
                        return
                    result = await api.get_adjustment(
                        adjustment_id=UUID(ids[index]), **binding(view),
                    )
                    await card(callback, state, saved, view, result)
                    return
                if pending_map(data).get(slot(view)):
                    await list_page(callback, state, saved, view, api)
                    return
                if action == "new":
                    result = await require_edit(api, view)
                    view = {
                        **fresh(view, result, "input"), "create": True,
                        "adjustment_id": None, "fields": {}, "field": "name",
                    }
                elif action == "field":
                    if view["phase"] != "card" or index >= len(FIELDS):
                        return
                    result = await api.get_adjustment(
                        adjustment_id=UUID(view["adjustment_id"]), **binding(view),
                    )
                    if result["can_manage"] is not True:
                        raise problem(403, "permission_required")
                    view = {
                        **fresh(view, result, "enabled" if index == 2 else "input"),
                        "create": False, "adjustment_id": result["item"]["id"],
                        "expected_row_version": result["item"]["row_version"],
                        "fields": {}, "field": FIELDS[index],
                    }
                elif action == "enabled":
                    if view["phase"] != "enabled" or index not in (0, 1):
                        return
                    result = await require_edit(api, view)
                    view["language"] = result["language"]
                    view["fields"]["enabled"] = index == 0
                    view["phase"] = "review"
                if same(await state.get_data(), saved):
                    await form(callback, state, saved, view)
        except Exception as error:
            logger.warning(
                "construction_bot_adjustment_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if view is not None and saved is not None:
                view = {**view, "phase": "blocked", "nonce": uuid4().hex}
                notice = "denied" if getattr(error, "status_code", None) in (401, 403, 404) else "unavailable"
                await show(callback, state, saved, view,
                           tr(notice, view["language"]), back_rows(view))


class AdjustmentInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(getattr(message, "text", None), str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(VIEW)
        return (
            selected(data, view, message.from_user.id, data.get("last_menu_message_id")) is not None
            and view.get("phase") == "input"
        )


@router.message(AdjustmentInput())
async def adjustment_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await AdjustmentInput()(message, state):
            return
        data = await state.get_data()
        view, saved = deepcopy(data[VIEW]), snapshot(data)
        try:
            if view["field"] == "name":
                value = message.text.strip()
                if not 1 <= len(value) <= 255 or "\x00" in value:
                    raise ValueError("Invalid name.")
            else:
                value = parse_coefficient(message.text)
        except ValueError:
            await message.answer(tr(
                "invalid_name" if view["field"] == "name" else "enter_coefficient",
                view["language"],
            ))
            return
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await require_edit(ProjectAdjustmentBotApi(service), view)
            if not same(await state.get_data(), saved):
                return
            view["language"] = result["language"]
            view["fields"][view["field"]] = value
            if view["create"] and view["field"] == "name":
                view["field"] = "coefficient"
            elif view["create"]:
                view["phase"] = "enabled"
            else:
                view["phase"] = "review"
            await form(message, state, saved, view)
        except Exception as error:
            logger.warning("construction_bot_adjustment_input_failed error_type=%s", type(error).__name__)
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            await show(message, state, saved, view,
                       tr("denied", view["language"]), back_rows(view))
