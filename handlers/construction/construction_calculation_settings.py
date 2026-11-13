"""Calculation settings forms with explicit values and atomic paired edits."""
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
from services.construction.construction_calculation_settings_bot_api import CalculationSettingsBotApi
from services.construction.construction_language_bot_api import problem
from ui.construction_calculation_settings import tr, fields_text, settings_text
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_calculation_settings_bot")
logger = logging.getLogger(__name__)
VIEW = "construction_calculation_settings_view"
PENDING = "construction_calculation_settings_pending_requests"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, PENDING, CONTEXT, shell.VIEW, "last_menu_message_id")
FIELDS = (
    "money_input_precision", "quantity_precision", "supplier_stale_price_days",
    "minimum_job", "default_technological_waste_percent", "default_local_repair_percent",
)
PERCENT_FIELDS = FIELDS[4:]
CLEARABLE = ("minimum_job", "minimum_job_currency") + PERCENT_FIELDS


def parse_value(field, value):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("Invalid settings input.")
    text = value.strip()
    if field in ("money_input_precision", "quantity_precision", "supplier_stale_price_days"):
        if not text.isascii() or not text.isdecimal() or len(text) > 6:
            raise ValueError("An integer is required.")
        number = int(text)
        maximum = 3650 if field == "supplier_stale_price_days" else 6
        if not 0 <= number <= maximum:
            raise ValueError("Integer outside its range.")
        return number
    if field == "minimum_job_currency":
        code = text.upper()
        if re.fullmatch(r"[A-Z]{3}", code) is None:
            raise ValueError("A currency code is required.")
        return code
    if field not in ("minimum_job",) + PERCENT_FIELDS:
        raise ValueError("Unsupported settings field.")
    if re.fullmatch(r"(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)", text) is None:
        raise ValueError("An exact decimal number is required.")
    text = text.replace(",", ".")
    try:
        number = Decimal(text)
        if not number.is_finite() or number < 0 or field in PERCENT_FIELDS and number > 100:
            raise ValueError("Decimal outside its range.")
    except InvalidOperation:
        raise ValueError("Invalid decimal.") from None
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
    result = data.get(PENDING, {})
    if not isinstance(result, dict):
        raise ValueError("Pending calculation state differs.")
    return deepcopy(result)


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
        "tenant_id": view["tenant_id"], "actor_user_id": result["actor_user_id"],
        "language": result["language"], "menu_id": view["menu_id"],
        "phase": phase, "nonce": uuid4().hex,
    }


def cb(view, action, index=None):
    value = "CSET:" + action + ":" + view["nonce"]
    return value if index is None else value + ":" + str(index)


def button(view, key, action, index=None):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cb(view, action, index),
    )


def back_rows(view):
    return [
        [button(view, "refresh", "refresh")],
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
    if state is not None and private(event) and not (event.data or "").startswith("CSET:"):
        if (await state.get_data()).get(VIEW):
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


async def menu(event, state, saved, view, result, notice=None):
    view = fresh(view, result, "menu")
    text = tr("title", view["language"]) + "\n\n" + settings_text(result["item"], view["language"])
    text += "\n\n" + tr("money_note", view["language"])
    if notice:
        text = notice + "\n\n" + text
    rows = []
    pending = pending_map(await state.get_data()).get(slot(view))
    if pending:
        text += "\n\n" + tr("pending", view["language"])
        rows.append([button(view, "retry", "retry")])
    elif result["can_manage"]:
        if result["item"] is None:
            rows.append([button(view, "create", "new")])
        else:
            rows.extend([[button(view, field, "field", index)]]
                        for index, field in enumerate(FIELDS))
            rows = [row[0] for row in rows]
    rows.extend(back_rows(view))
    await show(event, state, saved, view, text, rows)


def help_text(field, language):
    if field == "money_input_precision":
        return tr("money_note", language)
    if field == "quantity_precision":
        return tr("quantity_note", language)
    if field == "supplier_stale_price_days":
        return tr("days_note", language)
    if field == "minimum_job_currency":
        return tr("currency_prompt", language)
    if field == "minimum_job":
        return tr("minimum_note", language) + "\n\n" + "≥ 0"
    return tr("percent_note", language)


async def form(event, state, saved, view):
    view = {**view, "nonce": uuid4().hex}
    language = view["language"]
    rows = []
    if view["phase"] == "review":
        text = tr("review", language) + "\n\n" + fields_text(view["fields"], language)
        rows.append([button(view, "save", "save")])
    else:
        field = view["field"]
        text = tr(field, language) + "\n\n" + help_text(field, language)
        if view["phase"] == "precision":
            rows.extend([
                [InlineKeyboardButton(text=str(index), callback_data=cb(view, "number", index))]
                for index in range(7)
            ])
        elif view["phase"] != "input":
            raise ValueError("Unknown calculation form phase.")
        if not view["create"] and field in CLEARABLE:
            rows.append([button(view, "clear", "clear")])
    rows.append([button(view, "cancel", "refresh")])
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
        operation = {"create": view["create"], "body": body, "key": uuid4().hex}
        operations[key] = deepcopy(operation)
    view = {**view, "phase": "pending", "nonce": uuid4().hex}
    await state.update_data(**{VIEW: view, PENDING: operations})
    saved = snapshot(await state.get_data())
    try:
        result = await api.write(request=deepcopy(operation), **binding(view))
    except Exception as error:
        logger.warning(
            "construction_bot_calculation_write_failed error_type=%s status=%s code=%s",
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
    await menu(event, state, snapshot(await state.get_data()), view, result,
               tr("saved", result["language"]))


async def open_settings(callback, state, saved, parent, service):
    if not shell.unchanged(await state.get_data(), saved):
        return
    view = {
        key: parent[key] for key in (
            "telegram_user_id", "tenant_id", "actor_user_id", "language", "menu_id",
        )
    }
    own_saved = snapshot(await state.get_data())
    result = await CalculationSettingsBotApi(service).read(**binding(view))
    await menu(callback, state, own_saved, view, result)


@router.callback_query(F.data.startswith("CSET:"))
async def calculation_callback(callback, state, bot=None, event_update=None):
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
            if action in ("field", "number"):
                if len(parts) != 4 or parts[3] not in {str(n) for n in range(7)}:
                    return
                index = int(parts[3])
            elif len(parts) != 3 or action not in ("refresh", "new", "clear", "save", "retry"):
                return
            saved = snapshot(data)
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = CalculationSettingsBotApi(service)
                if action in ("save", "retry"):
                    await execute(callback, state, view, api, action == "retry")
                    return
                if action == "refresh":
                    result = await api.read(**binding(view))
                    await menu(callback, state, saved, view, result)
                    return
                if pending_map(data).get(slot(view)):
                    result = await api.read(**binding(view))
                    await menu(callback, state, saved, view, result)
                    return
                if action in ("new", "field"):
                    result = await api.read(**binding(view))
                    if result["can_manage"] is not True:
                        raise problem(403, "permission_required")
                    if action == "new":
                        if result["item"] is not None:
                            await menu(callback, state, saved, view, result)
                            return
                        view = {
                            **fresh(view, result, "precision"),
                            "create": True, "fields": {}, "field": FIELDS[0],
                        }
                    else:
                        if view["phase"] != "menu" or index >= len(FIELDS) or result["item"] is None:
                            return
                        field = FIELDS[index]
                        view = {
                            **fresh(view, result, "precision" if index < 2 else "input"),
                            "create": False, "fields": {}, "field": field,
                            "expected_row_version": result["item"]["row_version"],
                        }
                elif action == "number":
                    if view["phase"] != "precision":
                        return
                    context = await require_edit(api, view)
                    view["language"] = context["language"]
                    field = view["field"]
                    if field not in FIELDS[:2]:
                        return
                    view["fields"][field] = index
                    if view["create"]:
                        view["field"] = (
                            "quantity_precision" if field == "money_input_precision"
                            else "supplier_stale_price_days"
                        )
                        view["phase"] = "precision" if field == "money_input_precision" else "input"
                    else:
                        view["phase"] = "review"
                elif action == "clear":
                    if (
                        view["create"] or view["phase"] != "input"
                        or view.get("field") not in CLEARABLE
                    ):
                        return
                    context = await require_edit(api, view)
                    view["language"] = context["language"]
                    if view["field"] in ("minimum_job", "minimum_job_currency"):
                        view["fields"] = {
                            "minimum_job_amount": None, "minimum_job_currency": None,
                        }
                    else:
                        view["fields"] = {view["field"]: None}
                    view["phase"] = "review"
                if same(await state.get_data(), saved):
                    await form(callback, state, saved, view)
        except Exception as error:
            logger.warning(
                "construction_bot_calculation_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if saved is not None and view is not None:
                view = {**view, "phase": "blocked", "nonce": uuid4().hex}
                notice = "denied" if getattr(error, "status_code", None) in (401, 403, 404) else "unavailable"
                await show(callback, state, saved, view,
                           tr(notice, view["language"]), back_rows(view))


class CalculationInput(BaseFilter):
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


@router.message(CalculationInput())
async def calculation_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await CalculationInput()(message, state):
            return
        data = await state.get_data()
        view, saved = deepcopy(data[VIEW]), snapshot(data)
        field = view["field"]
        try:
            value = parse_value(field, message.text)
        except ValueError:
            await message.answer(tr("invalid", view["language"]))
            return
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = CalculationSettingsBotApi(service)
                context = await require_edit(api, view)
                if field == "minimum_job_currency":
                    codes = await api.currency_codes(**binding(view))
                    if not same(await state.get_data(), saved):
                        return
                    if value not in codes:
                        await message.answer(tr("currency_invalid", context["language"]))
                        return
            if not same(await state.get_data(), saved):
                return
            view["language"] = context["language"]
            if field == "minimum_job":
                view["fields"]["minimum_job_amount"] = value
                view["field"] = "minimum_job_currency"
            else:
                view["fields"][field] = value
                view["phase"] = "review"
            await form(message, state, saved, view)
        except Exception as error:
            logger.warning("construction_bot_calculation_input_failed error_type=%s", type(error).__name__)
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            await show(message, state, saved, view,
                       tr("denied", view["language"]), back_rows(view))
