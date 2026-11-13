"""Private pricing forms with exact values and immutable keyed retries."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import logging
import re
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import private, lock_for
from handlers.construction import construction_stage2_settings as shell
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_pricing_profile_bot_api import PricingProfileBotApi, LEVELS
from services.construction.construction_language_bot_api import problem
from ui.construction_pricing_profiles import tr, fields_text
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_pricing_profiles_bot")
logger = logging.getLogger(__name__)
VIEW = "construction_pricing_profiles_view"
PENDING = "construction_pricing_profile_pending_requests"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, PENDING, CONTEXT, shell.VIEW, "last_menu_message_id")
FIELDS = ("work_coefficient", "material_markup_percent")


def parse_value(value, field):
    if field not in FIELDS or not isinstance(value, str) or len(value) > 256:
        raise ValueError("Invalid pricing input.")
    text = value.strip()
    if re.fullmatch(r"(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)", text) is None:
        raise ValueError("An exact decimal number is required.")
    text = text.replace(",", ".")
    try:
        number = Decimal(text)
        if (
            not number.is_finite()
            or field == "work_coefficient" and number <= 0
            or field == "material_markup_percent" and not Decimal(0) <= number <= Decimal(100)
        ):
            raise ValueError("Pricing value is outside its bounds.")
    except InvalidOperation:
        raise ValueError("Invalid pricing value.") from None
    return text


def binding(view):
    return {
        "telegram_user_id": view["telegram_user_id"],
        "tenant_id": UUID(view["tenant_id"]),
        "expected_user_id": UUID(view["actor_user_id"]),
    }


def slot(view):
    return view["tenant_id"] + ":" + view["actor_user_id"]


def pending_map(data):
    value = data.get(PENDING, {})
    if not isinstance(value, dict):
        raise ValueError("Pending pricing state differs.")
    return deepcopy(value)


def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}


def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)


def bound(data, view, sender, menu_id):
    context = data.get(CONTEXT)
    return (
        isinstance(view, dict) and isinstance(context, dict)
        and view.get("telegram_user_id") == sender
        and context.get("telegram_user_id") == sender
        and view.get("tenant_id") == context.get("tenant_id")
        and view.get("menu_id") == menu_id
        and data.get("last_menu_message_id") == menu_id
    )


def fresh(view, result, phase):
    return {
        "telegram_user_id": view["telegram_user_id"],
        "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"],
        "language": result["language"], "menu_id": view["menu_id"],
        "phase": phase, "nonce": uuid4().hex,
    }


def cb(view, action, index=None):
    value = "PRC:" + action + ":" + view["nonce"]
    return value if index is None else value + ":" + str(index)


def button(view, key, action, index=None):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cb(view, action, index),
    )


def back_rows(view):
    return [
        [button(view, "list", "list")],
        [InlineKeyboardButton(
            text=shell.tr("title", view["language"]), callback_data="C2:open",
        )],
    ]


async def show(event, state, saved, view, text, rows):
    if not same(await state.get_data(), saved):
        return
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if getattr(event, "message", None) is not None:
        sent = await edit_or_replace_menu_message(
            callback=event, text=text, reply_markup=markup,
        )
    else:
        sent = await event.answer(text, reply_markup=markup)
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id},
        shell.VIEW: {}, "last_menu_message_id": sent.message_id,
    })


async def navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("PRC:"):
        if (await state.get_data()).get(VIEW):
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


async def list_profiles(event, state, saved, view, api):
    result = await api.list_profiles(**binding(view))
    if not same(await state.get_data(), saved):
        return
    view = fresh(view, result, "list")
    configured = {item["code"]: item for item in result["items"]}
    text = tr("title", view["language"]) + "\n\n" + tr("note", view["language"])
    rows = []
    pending = pending_map(await state.get_data()).get(slot(view))
    for index, code in enumerate(LEVELS):
        name = tr(code, view["language"])
        if code in configured:
            rows.append([InlineKeyboardButton(text=name, callback_data=cb(view, "level", index))])
        else:
            text += "\n\n" + name + ": " + tr("not_configured", view["language"])
            if result["can_manage"] and not pending:
                rows.append([InlineKeyboardButton(
                    text=tr("configure", view["language"]) + ": " + name,
                    callback_data=cb(view, "level", index),
                )])
    if pending:
        text += "\n\n" + tr("pending", view["language"])
        rows.append([button(view, "retry", "retry")])
    rows.extend([
        [button(view, "refresh", "list")],
        [InlineKeyboardButton(
            text=shell.tr("title", view["language"]), callback_data="C2:open",
        )],
    ])
    await show(event, state, saved, view, text, rows)


async def card(event, state, saved, view, result, notice=None):
    item = result["item"]
    view = {**fresh(view, result, "card"), "profile_id": item["id"]}
    text = fields_text({key: item[key] for key in ("code",) + FIELDS}, view["language"])
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
        text = tr("enter_work" if view["field"] == FIELDS[0] else "enter_markup", language)
    elif view["phase"] == "review":
        text = tr("review", language) + "\n\n" + fields_text(view["fields"], language)
        rows.append([button(view, "save", "save")])
    else:
        raise ValueError("Unknown pricing phase.")
    text += "\n\n" + tr("note", language)
    rows.append([button(view, "cancel", "list")])
    await show(event, state, saved, view, text, rows)


async def require_edit(api, view):
    _, context = await api.context(**binding(view))
    if context["can_manage"] is not True:
        raise problem(403, "permission_required")
    return context


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
            "profile_id": view.get("profile_id"),
            "body": body, "key": uuid4().hex,
        }
        operations[key] = deepcopy(operation)
    view = {**view, "phase": "pending", "nonce": uuid4().hex}
    await state.update_data(**{VIEW: view, PENDING: operations})
    saved = snapshot(await state.get_data())
    try:
        result = await api.write_profile(request=deepcopy(operation), **binding(view))
    except Exception as error:
        logger.warning(
            "construction_bot_pricing_write_failed error_type=%s status=%s code=%s",
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
            notice = "exists" if code == "PRICING_PROFILE_EXISTS" else "conflict"
            text, rows = tr(notice, view["language"]), back_rows(view)
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


async def open_profiles(callback, state, saved, parent, service):
    if not shell.unchanged(await state.get_data(), saved):
        return
    view = {
        key: parent[key] for key in (
            "telegram_user_id", "tenant_id", "actor_user_id", "language", "menu_id",
        )
    }
    await list_profiles(
        callback, state, snapshot(await state.get_data()),
        view, PricingProfileBotApi(service),
    )


@router.callback_query(F.data.startswith("PRC:"))
async def pricing_callback(callback, state, bot=None, event_update=None):
    if not private(callback):
        await callback.answer(show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        saved, view = None, None
        try:
            if await state.get_state() is not None:
                return
            data = await state.get_data()
            view = deepcopy(data.get(VIEW))
            parts = callback.data.split(":")
            if (
                len(parts) not in (3, 4)
                or not bound(data, view, callback.from_user.id, callback.message.message_id)
                or view.get("nonce") != parts[2]
            ):
                return
            action, index = parts[1], None
            if action in ("level", "field"):
                if len(parts) != 4 or parts[3] not in ("0", "1", "2"):
                    return
                index = int(parts[3])
            elif len(parts) != 3 or action not in ("list", "save", "retry"):
                return
            saved = snapshot(data)
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = PricingProfileBotApi(service)
                if action in ("save", "retry"):
                    await execute(callback, state, view, api, action == "retry")
                    return
                if action == "list":
                    await list_profiles(callback, state, saved, view, api)
                    return
                if action == "level":
                    if view["phase"] != "list":
                        return
                    result = await api.list_profiles(**binding(view))
                    item = next((item for item in result["items"] if item["code"] == LEVELS[index]), None)
                    if item is not None:
                        await card(callback, state, saved, view, {**result, "item": item})
                        return
                    if pending_map(data).get(slot(view)):
                        return
                    if result["can_manage"] is not True:
                        raise problem(403, "permission_required")
                    view = {
                        **fresh(view, result, "input"), "create": True,
                        "profile_id": None, "fields": {"code": LEVELS[index]},
                        "field": FIELDS[0],
                    }
                elif action == "field":
                    if view["phase"] != "card" or index >= len(FIELDS):
                        return
                    if pending_map(data).get(slot(view)):
                        return
                    result = await api.get_profile(
                        profile_id=UUID(view["profile_id"]), **binding(view),
                    )
                    if result["can_manage"] is not True:
                        raise problem(403, "permission_required")
                    view = {
                        **fresh(view, result, "input"), "create": False,
                        "profile_id": result["item"]["id"],
                        "expected_row_version": result["item"]["row_version"],
                        "fields": {}, "field": FIELDS[index],
                    }
                if same(await state.get_data(), saved):
                    await form(callback, state, saved, view)
        except Exception as error:
            logger.warning(
                "construction_bot_pricing_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if saved is not None and view is not None:
                view = {**view, "phase": "blocked", "nonce": uuid4().hex}
                notice = "denied" if getattr(error, "status_code", None) in (401, 403, 404) else "unavailable"
                await show(callback, state, saved, view,
                           tr(notice, view["language"]), back_rows(view))


class PricingInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or not isinstance(getattr(message, "text", None), str)
            or message.text.startswith("/") or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(VIEW)
        return (
            bound(data, view, message.from_user.id, data.get("last_menu_message_id"))
            and view.get("phase") == "input"
        )


@router.message(PricingInput())
async def pricing_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await PricingInput()(message, state):
            return
        data = await state.get_data()
        view, saved = deepcopy(data[VIEW]), snapshot(data)
        try:
            value = parse_value(message.text, view["field"])
        except ValueError:
            await message.answer(tr(
                "enter_work" if view["field"] == FIELDS[0] else "enter_markup",
                view["language"],
            ))
            return
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                context = await require_edit(PricingProfileBotApi(service), view)
            if not same(await state.get_data(), saved):
                return
            view["language"] = context["language"]
            view["fields"][view["field"]] = value
            if view["create"] and view["field"] == FIELDS[0]:
                view["field"] = FIELDS[1]
            else:
                view["phase"] = "review"
            await form(message, state, saved, view)
        except Exception as error:
            logger.warning("construction_bot_pricing_input_failed error_type=%s", type(error).__name__)
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            await show(message, state, saved, view,
                       tr("denied", view["language"]), back_rows(view))
