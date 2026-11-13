"""Private tax-profile forms over the existing versioned, idempotent API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import private, lock_for
from handlers.construction import construction_stage2_settings as shell
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_tax_profile_bot_api import TaxProfileBotApi
from services.construction.construction_language_bot_api import problem
from ui.construction_tax_profiles import tr, fields_text, profile_text
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_tax_profiles_bot")
logger = logging.getLogger(__name__)
VIEW = "construction_tax_profiles_view"
PENDING = "construction_tax_profile_pending_requests"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, PENDING, CONTEXT, shell.VIEW, "last_menu_message_id")
FIELDS = ("name", "client_price_tax_mode", "material_cost_basis")
CLIENTS = ("TAX_EXCLUSIVE", "TAX_INCLUSIVE")
BASES = ("NET", "GROSS")


def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}


def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)


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
        raise ValueError("Pending operation state differs.")
    return deepcopy(value)


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


def cb(view, action, value=None):
    result = "TX:" + action + ":" + view["nonce"]
    return result if value is None else result + ":" + str(value)


def button(view, key, action, value=None):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cb(view, action, value),
    )


def fresh(view, result, phase):
    return {
        "telegram_user_id": view["telegram_user_id"],
        "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"],
        "language": result["language"], "menu_id": view["menu_id"],
        "phase": phase, "nonce": uuid4().hex,
    }


def back_rows(view):
    return [
        [button(view, "list", "list")],
        [InlineKeyboardButton(text=tr("title", view["language"]),
                              callback_data="C2:open")],
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
    if state is not None and private(event) and not (event.data or "").startswith("TX:"):
        if (await state.get_data()).get(VIEW):
            await state.update_data(**{VIEW: {}})
    # Pending immutable requests survive navigation.
    return await handler(event, data)


async def list_page(event, state, saved, view, api, cursor=None):
    result = await api.list_profiles(cursor=cursor, **binding(view))
    if not same(await state.get_data(), saved):
        return
    view = fresh(view, result, "list")
    view["profile_ids"] = [item["id"] for item in result["items"]]
    view["next_cursor"] = result["next_cursor"]
    rows = [
        [InlineKeyboardButton(
            text=item["name"][:90], callback_data=cb(view, "open", index),
        )]
        for index, item in enumerate(result["items"])
    ]
    pending = pending_map(await state.get_data()).get(slot(view))
    text = tr("title", view["language"])
    if not result["items"]:
        text += "\n\n" + tr("empty", view["language"])
    if result["next_cursor"]:
        rows.append([button(view, "next", "next")])
    if pending:
        text += "\n\n" + tr("pending", view["language"])
        rows.append([button(view, "retry", "retry")])
    elif result["can_manage"] is True:
        rows.append([button(view, "new", "new")])
    rows.extend([
        [button(view, "refresh", "list")],
        [InlineKeyboardButton(
            text=shell.tr("title", view["language"]), callback_data="C2:open",
        )],
    ])
    await show(event, state, saved, view, text, rows)


async def card(event, state, saved, view, result, notice=None):
    view = fresh(view, result, "card")
    view["profile_id"] = result["item"]["id"]
    text = profile_text(result["item"], view["language"])
    text += "\n\n" + tr("note", view["language"])
    if notice:
        text = notice + "\n\n" + text
    rows = []
    pending = pending_map(await state.get_data()).get(slot(view))
    if pending:
        rows.append([button(view, "retry", "retry")])
    elif result["can_manage"] is True:
        rows.extend([
            [InlineKeyboardButton(
                text=tr("edit", view["language"]) + ": " + tr(field, view["language"]),
                callback_data=cb(view, "field", index),
            )]
            for index, field in enumerate(FIELDS)
        ])
    rows.append([button(view, "rules", "rules")])
    rows.extend(back_rows(view))
    await show(event, state, saved, view, text, rows)


async def form(event, state, saved, view):
    view = {**view, "nonce": uuid4().hex}
    phase, language = view["phase"], view["language"]
    rows = []
    if phase == "name":
        text = tr("enter_name", language)
    elif phase in ("client", "material"):
        field = "client_price_tax_mode" if phase == "client" else "material_cost_basis"
        values = CLIENTS if phase == "client" else BASES
        text = tr(field, language)
        rows.extend([[button(view, value, "mode", index)]]
                    for index, value in enumerate(values))
        rows = [row[0] for row in rows]
    elif phase == "review":
        text = tr("review", language) + "\n\n" + fields_text(view["fields"], language)
        text += "\n\n" + tr("note", language)
        rows.append([button(view, "save", "save")])
    else:
        raise ValueError("Unknown profile form phase.")
    rows.append([button(view, "cancel", "list")])
    await show(event, state, saved, view, text, rows)


async def require_edit(api, view):
    _, context = await api.context(**binding(view))
    if context["can_manage"] is not True:
        raise problem(403, "permission_required")
    return context


async def execute(event, state, view, api, *, retry):
    data = await state.get_data()
    operations = pending_map(data)
    key = slot(view)
    if retry:
        operation = operations.get(key)
        if not isinstance(operation, dict):
            raise ValueError("No pending operation.")
    else:
        if view.get("phase") != "review" or key in operations:
            raise ValueError("Profile is not ready for a new operation.")
        if view.get("kind") == "rule":
            from handlers.construction.construction_tax_rules import make_request
            operation = make_request(view)
        else:
            body = deepcopy(view["fields"])
            if not view["create"]:
                body["expected_row_version"] = view["expected_row_version"]
            operation = {
                "operation": "create" if view["create"] else "patch",
                "profile_id": view.get("profile_id"),
                "body": body, "key": uuid4().hex,
            }
        operations[key] = deepcopy(operation)
    operation_back_rows = back_rows
    if operation.get("kind") == "rule":
        from handlers.construction.construction_tax_rules import back_rows as rule_back_rows
        operation_back_rows = rule_back_rows
        view = {**view, "kind": "rule", "profile_id": operation["profile_id"]}
    view = {**view, "phase": "pending", "nonce": uuid4().hex}
    await state.update_data(**{VIEW: view, PENDING: operations})
    saved = snapshot(await state.get_data())
    try:
        writer = api.write_rule if operation.get("kind") == "rule" else api.write_profile
        result = await writer(request=deepcopy(operation), **binding(view))
    except Exception as error:
        logger.warning(
            "construction_bot_tax_profile_write_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not same(await state.get_data(), saved):
            return
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        definite = (
            status in (400, 404, 422)
            or status == 409 and code != "idempotency_in_progress"
        )
        if definite:
            operations.pop(key, None)
            await state.update_data(**{PENDING: operations})
            saved = snapshot(await state.get_data())
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            if code == "TAX_RULE_CATEGORY_EXISTS":
                from ui.construction_tax_rules import tr as rule_tr
                text = rule_tr("category_exists", view["language"])
            else:
                text = tr("conflict" if status == 409 else "unavailable", view["language"])
            rows = operation_back_rows(view)
        else:
            text = tr("pending", view["language"])
            rows = [[button(view, "retry", "retry")]] + operation_back_rows(view)
        await show(event, state, saved, view, text, rows)
        return
    if not same(await state.get_data(), saved):
        return
    operations.pop(key, None)
    await state.update_data(**{PENDING: operations})
    saved = snapshot(await state.get_data())
    if operation.get("kind") == "rule":
        from handlers.construction.construction_tax_rules import rule_card
        from ui.construction_tax_rules import tr as rule_tr
        await rule_card(event, state, saved, view, result,
                        notice=rule_tr("rule_saved", result["language"]))
    else:
        await card(event, state, saved, view, result,
                   notice=tr("saved", result["language"]))


async def open_profiles(callback, state, saved, parent, service):
    if not shell.unchanged(await state.get_data(), saved):
        return
    view = {
        "telegram_user_id": parent["telegram_user_id"],
        "tenant_id": parent["tenant_id"], "actor_user_id": parent["actor_user_id"],
        "language": parent["language"], "menu_id": parent["menu_id"],
    }
    own_saved = snapshot(await state.get_data())
    await list_page(callback, state, own_saved, view, TaxProfileBotApi(service))


@router.callback_query(F.data.startswith("TX:"))
async def tax_callback(callback, state, bot=None, event_update=None):
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
            action = parts[1]
            index = None
            if action in ("open", "field", "mode", "ropen", "rfield", "rcat"):
                if len(parts) != 4 or parts[3] not in {str(n) for n in range(10)}:
                    return
                index = int(parts[3])
            elif len(parts) != 3 or action not in (
                "list", "next", "new", "save", "retry",
                "rules", "rnew", "rprofile",
            ):
                return
            saved = snapshot(data)
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = TaxProfileBotApi(service)
                pending = pending_map(data).get(slot(view), {})
                if (
                    action in {"rules", "ropen", "rnew", "rfield", "rcat", "rprofile"}
                    or action == "save" and view.get("kind") == "rule"
                    or action == "retry" and pending.get("kind") == "rule"
                ):
                    from handlers.construction.construction_tax_rules import handle
                    await handle(callback, state, saved, view, service, action, index)
                    return
                if action in ("save", "retry"):
                    await execute(callback, state, view, api, retry=action == "retry")
                    return
                if action in ("list", "next"):
                    cursor = view.get("next_cursor") if action == "next" else None
                    if action == "next" and (view["phase"] != "list" or not cursor):
                        return
                    await list_page(callback, state, saved, view, api, cursor)
                    return
                if action == "open":
                    ids = view.get("profile_ids", [])
                    if view["phase"] != "list" or not 0 <= index < len(ids):
                        return
                    result = await api.get_profile(profile_id=UUID(ids[index]), **binding(view))
                    await card(callback, state, saved, view, result)
                    return
                if pending_map(data).get(slot(view)):
                    await list_page(callback, state, saved, view, api)
                    return
                if action == "new":
                    result = await require_edit(api, view)
                    view = fresh(view, result, "name")
                    view.update(create=True, fields={}, profile_id=None)
                elif action == "field":
                    if view["phase"] != "card" or index >= len(FIELDS):
                        return
                    result = await api.get_profile(
                        profile_id=UUID(view["profile_id"]), **binding(view),
                    )
                    if result["can_manage"] is not True:
                        raise problem(403, "permission_required")
                    profile_id = view["profile_id"]
                    view = fresh(view, result, ("name", "client", "material")[index])
                    view.update(
                        create=False, fields={}, profile_id=profile_id,
                        expected_row_version=result["item"]["row_version"],
                    )
                elif action == "mode":
                    phase = view["phase"]
                    if phase not in ("client", "material") or index >= 2:
                        return
                    result = await require_edit(api, view)
                    if not same(await state.get_data(), saved):
                        return
                    view["language"] = result["language"]
                    field = "client_price_tax_mode" if phase == "client" else "material_cost_basis"
                    view["fields"][field] = (CLIENTS if phase == "client" else BASES)[index]
                    view["phase"] = "material" if view["create"] and phase == "client" else "review"
                if not same(await state.get_data(), saved):
                    return
                await form(callback, state, saved, view)
        except Exception as error:
            logger.warning(
                "construction_bot_tax_profiles_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if saved is not None and view is not None:
                view = {**view, "phase": "blocked", "nonce": uuid4().hex}
                notice = "denied" if getattr(error, "status_code", None) in (401, 403, 404) else "unavailable"
                await show(callback, state, saved, view,
                           tr(notice, view["language"]), back_rows(view))


class ProfileNameInput(BaseFilter):
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
            and view.get("phase") == "name"
        )


@router.message(ProfileNameInput())
async def name_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await ProfileNameInput()(message, state):
            return
        data = await state.get_data()
        view, saved = deepcopy(data[VIEW]), snapshot(data)
        name = message.text.strip()
        if not 1 <= len(name) <= 255 or "\x00" in name:
            await message.answer(tr("invalid", view["language"]))
            return
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await require_edit(TaxProfileBotApi(service), view)
            if not same(await state.get_data(), saved):
                return
            view["language"] = result["language"]
            view["fields"]["name"] = name
            view["phase"] = "client" if view["create"] else "review"
            await form(message, state, saved, view)
        except Exception as error:
            logger.warning("construction_bot_tax_profile_name_failed error_type=%s", type(error).__name__)
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            await show(message, state, saved, view,
                       tr("denied", view["language"]), back_rows(view))

# CON_STAGE2_TELEGRAM_TAX_RULES_V1
from handlers.construction.construction_tax_rules import register_handlers as register_tax_rule_handlers
register_tax_rule_handlers(router)
