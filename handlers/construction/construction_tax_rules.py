"""Tax-rule forms sharing the established tax navigation and retry protocol."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import logging
import re
from uuid import UUID, uuid4

from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton

from handlers.construction import construction_tax_profiles as tax
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_tax_rule_bot_api import TaxRuleBotApi
from services.construction.construction_language_bot_api import problem
from ui.construction_tax_rules import tr, fields_text, rule_text

logger = logging.getLogger(__name__)
CATEGORIES = ("WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER")
ACTIONS = {"rules", "ropen", "rnew", "rfield", "rcat", "rprofile"}


def parse_rate(value):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("Invalid rate text.")
    text = value.strip()
    if re.fullmatch(r"(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)", text) is None:
        raise ValueError("A decimal rate is required.")
    text = text.replace(",", ".")
    try:
        number = Decimal(text)
        if not number.is_finite() or not Decimal(0) <= number <= Decimal(100):
            raise ValueError("Rate is outside the allowed range.")
    except InvalidOperation:
        raise ValueError("Invalid rate.") from None
    return text


def button(view, key, action, value=None):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=tax.cb(view, action, value),
    )


def back_rows(view):
    return [
        [button(view, "title", "rules")],
        [button(view, "profile", "rprofile")],
    ]


def fresh(view, result, phase):
    return {
        **tax.fresh(view, result, phase),
        "profile_id": view["profile_id"], "kind": "rule",
    }


async def list_rules(event, state, saved, view, api):
    result = await api.list_rules(
        profile_id=UUID(view["profile_id"]), **tax.binding(view),
    )
    if not tax.same(await state.get_data(), saved):
        return
    view = fresh(view, result, "rlist")
    view["rule_ids"] = [item["id"] for item in result["items"]]
    view["used_categories"] = [item["category"] for item in result["items"]]
    text = tr("title", view["language"])
    if not result["items"]:
        text += "\n\n" + tr("empty_rules", view["language"])
    rows = [
        [InlineKeyboardButton(
            text=tr(item["category"], view["language"]) + " · " + item["rate_percent"] + "%",
            callback_data=tax.cb(view, "ropen", index),
        )]
        for index, item in enumerate(result["items"])
    ]
    pending = tax.pending_map(await state.get_data()).get(tax.slot(view))
    if pending:
        text += "\n\n" + tr("pending", view["language"])
        rows.append([button(view, "retry", "retry")])
    elif result["can_manage"] and len(view["used_categories"]) < len(CATEGORIES):
        rows.append([button(view, "new_rule", "rnew")])
    rows.extend([
        [button(view, "refresh", "rules")],
        [button(view, "profile", "rprofile")],
    ])
    await tax.show(event, state, saved, view, text, rows)


async def rule_card(event, state, saved, view, result, notice=None):
    item = result["item"]
    view = {
        **fresh({**view, "profile_id": item["tax_profile_id"]}, result, "rcard"),
        "rule_id": item["id"],
    }
    text = rule_text(item, view["language"])
    if notice:
        text = notice + "\n\n" + text
    rows = []
    pending = tax.pending_map(await state.get_data()).get(tax.slot(view))
    if pending:
        rows.append([button(view, "retry", "retry")])
    elif result["can_manage"]:
        rows.extend([
            [InlineKeyboardButton(
                text=tr("edit", view["language"]) + ": " + tr(field, view["language"]),
                callback_data=tax.cb(view, "rfield", index),
            )]
            for index, field in enumerate(("category", "rate_percent"))
        ])
    rows.extend(back_rows(view))
    await tax.show(event, state, saved, view, text, rows)


async def form(event, state, saved, view):
    view = {**view, "nonce": uuid4().hex}
    phase, language = view["phase"], view["language"]
    rows = []
    if phase == "rcategory":
        text = tr("category", language)
        rows.extend([
            [button(view, category, "rcat", index)]
            for index, category in enumerate(view["category_codes"])
        ])
    elif phase == "rrate":
        text = tr("enter_rate", language)
    elif phase == "review":
        text = tr("review", language) + "\n\n" + fields_text(view["fields"], language)
        rows.append([button(view, "save", "save")])
    else:
        raise ValueError("Unknown rule form phase.")
    rows.append([button(view, "cancel", "rules")])
    await tax.show(event, state, saved, view, text, rows)


def make_request(view):
    body = deepcopy(view["fields"])
    if not view["create"]:
        body["expected_row_version"] = view["expected_row_version"]
    return {
        "kind": "rule", "operation": "create" if view["create"] else "patch",
        "profile_id": view["profile_id"], "rule_id": view.get("rule_id"),
        "body": body, "key": uuid4().hex,
    }


async def handle(callback, state, saved, view, service, action, index):
    api = TaxRuleBotApi(service)
    if action in ("save", "retry"):
        await tax.execute(callback, state, view, api, retry=action == "retry")
        return
    if action == "rprofile":
        result = await api.get_profile(
            profile_id=UUID(view["profile_id"]), **tax.binding(view),
        )
        await tax.card(callback, state, saved, view, result)
        return
    if action == "rules":
        await list_rules(callback, state, saved, view, api)
        return
    if action == "ropen":
        ids = view.get("rule_ids", [])
        if view["phase"] != "rlist" or not 0 <= index < len(ids):
            return
        result = await api.get_rule(
            profile_id=UUID(view["profile_id"]),
            rule_id=UUID(ids[index]), **tax.binding(view),
        )
        await rule_card(callback, state, saved, view, result)
        return

    if tax.pending_map(await state.get_data()).get(tax.slot(view)):
        await list_rules(callback, state, saved, view, api)
        return

    if action == "rnew":
        if view["phase"] != "rlist":
            return
        result = await api.list_rules(
            profile_id=UUID(view["profile_id"]), **tax.binding(view),
        )
        if result["can_manage"] is not True:
            raise problem(403, "permission_required")
        used = {item["category"] for item in result["items"]}
        choices = [category for category in CATEGORIES if category not in used]
        if not choices:
            await list_rules(callback, state, saved, view, api)
            return
        view = {
            **fresh(view, result, "rcategory"), "create": True,
            "fields": {}, "rule_id": None, "category_codes": choices,
        }
    elif action == "rfield":
        if view["phase"] != "rcard" or index not in (0, 1):
            return
        result = await api.get_rule(
            profile_id=UUID(view["profile_id"]),
            rule_id=UUID(view["rule_id"]), **tax.binding(view),
        )
        if result["can_manage"] is not True:
            raise problem(403, "permission_required")
        item = result["item"]
        view = {
            **fresh(view, result, "rcategory" if index == 0 else "rrate"),
            "create": False, "fields": {}, "rule_id": item["id"],
            "expected_row_version": item["row_version"],
        }
        if index == 0:
            page = await api.list_rules(
                profile_id=UUID(view["profile_id"]), **tax.binding(view),
            )
            used = {
                rule["category"] for rule in page["items"]
                if rule["id"] != view["rule_id"]
            }
            view["category_codes"] = [
                category for category in CATEGORIES if category not in used
            ]
    elif action == "rcat":
        codes = view.get("category_codes", [])
        if view["phase"] != "rcategory" or not 0 <= index < len(codes):
            return
        context = await tax.require_edit(api, view)
        view["language"] = context["language"]
        view["fields"]["category"] = codes[index]
        view["phase"] = "rrate" if view["create"] else "review"
    else:
        return
    if tax.same(await state.get_data(), saved):
        await form(callback, state, saved, view)


class RateInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not tax.private(message)
            or not isinstance(getattr(message, "text", None), str)
            or message.text.startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(tax.VIEW)
        return (
            tax.bound(data, view, message.from_user.id, data.get("last_menu_message_id"))
            and view.get("kind") == "rule" and view.get("phase") == "rrate"
        )


async def rate_message(message, state, bot=None, event_update=None):
    async with tax.lock_for(bot, message.from_user.id):
        if not await RateInput()(message, state):
            return
        data = await state.get_data()
        view, saved = deepcopy(data[tax.VIEW]), tax.snapshot(data)
        try:
            rate = parse_rate(message.text)
        except ValueError:
            await message.answer(tr("invalid_rate", view["language"]))
            return
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                context = await tax.require_edit(TaxRuleBotApi(service), view)
            if not tax.same(await state.get_data(), saved):
                return
            view["language"] = context["language"]
            view["fields"]["rate_percent"] = rate
            view["phase"] = "review"
            await form(message, state, saved, view)
        except Exception as error:
            logger.warning("construction_bot_tax_rate_input_failed error_type=%s", type(error).__name__)
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            await tax.show(message, state, saved, view,
                           tr("denied", view["language"]), back_rows(view))


def register_handlers(router):
    router.message.register(rate_message, RateInput())
