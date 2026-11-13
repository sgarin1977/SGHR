"""Private Stage 2 settings navigation using the existing HTTP boundary."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from unicodedata import normalize, category
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, problem,
)
from services.construction.construction_currency_bot_api import ConstructionCurrencyBotApi
from ui.construction_stage2_settings import tr, language_name
from ui.construction_unit_catalog import tr as unit_catalog_tr
from ui.construction_tax_profiles import tr as tax_profiles_tr
from ui.construction_pricing_profiles import tr as pricing_profiles_tr
from ui.construction_calculation_settings import tr as calculation_tr
from ui.construction_region_settings import tr as region_tr
from ui.construction_onboarding import tr as onboarding_tr
from ui.construction_organization_templates import tr as org_templates_tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_stage2_settings")
logger = logging.getLogger(__name__)
VIEW = "construction_stage2_settings_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, "last_menu_message_id")
PAGE_SIZE = 8


def resource(view):
    value = view.get("resource", "language")
    if value not in ("language", "currency"):
        raise ValueError("Unknown settings resource.")
    return value


def protocol(service, view):
    cls = (
        ConstructionCurrencyBotApi if resource(view) == "currency"
        else ConstructionLanguageBotApi
    )
    return cls(service)


def word(view, key):
    if resource(view) == "currency" and key in {
        "language", "explanation", "choose", "review",
        "saved", "unchanged", "uncertain",
    }:
        key = "currency" if key == "language" else "currency_" + key
    return tr(key, view["language"])


def setting_options(result, view):
    return result["item"][
        "currencies" if resource(view) == "currency" else "languages"
    ]


def setting_code(result, view):
    return result["item"][
        "organization_default_currency" if resource(view) == "currency"
        else "organization_default_language"
    ]


def option_label(option, view):
    if resource(view) == "currency":
        return option["code"] + " · " + option["name"]
    return option["label"]


def setting_name(result, view):
    return next(
        option_label(option, view)
        for option in setting_options(result, view)
        if option["code"] == setting_code(result, view)
    )


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(view, action, value=None):
    result = "C2:" + action + ":" + view["nonce"]
    return result if value is None else result + ":" + str(value)


def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}


def unchanged(data, saved):
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


async def navigation_middleware(handler, event, data):
    state = data.get("state")
    if (
        state is not None and private(event)
        and not (event.data or "").startswith("C2:")
    ):
        current = (await state.get_data()).get(VIEW)
        if current:
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


async def publish(callback, state, saved, view, text, rows):
    if not unchanged(await state.get_data(), saved):
        return
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not unchanged(await state.get_data(), saved):
        return
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    })


def back_rows(view):
    language = view["language"]
    return [
        [button(tr("refresh", language), cb(view, "refresh"))],
        [button(t("construction_bot_home_btn", language), "CS_HOME")],
    ]


def menu_view(view, result, *, phase="menu"):
    return {
        "telegram_user_id": view["telegram_user_id"],
        "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"],
        "language": result["language"],
        "menu_id": view["menu_id"],
        "row_version": result["item"]["row_version"],
        "phase": phase, "nonce": uuid4().hex,
        "resource": resource(view),
    }


async def read(service, view):
    return await protocol(service, view).read(
        telegram_user_id=view["telegram_user_id"],
        tenant_id=UUID(view["tenant_id"]),
        expected_user_id=(
            UUID(view["actor_user_id"]) if view.get("actor_user_id") else None
        ),
    )


async def menu(callback, state, saved, view, result):
    view = menu_view(view, result)
    language = view["language"]
    text = (
        tr("title", language) + "\n\n" + result["organization"][:200]
        + "\n\n" + word(view, "language") + ": "
        + setting_name(result, view)[:160]
        + "\n\n" + word(view, "explanation")
    )
    rows = [
        [button(onboarding_tr("title", language), cb(view, "onboarding"))],
        [button(tr("company", language), "CP:open")],
        [button(tr("language", language), cb(view, "languages"))],
        [button(region_tr("title", language), cb(view, "region"))],
        [button(unit_catalog_tr("title", language), cb(view, "units"))],
        [button(tax_profiles_tr("title", language), cb(view, "tax"))],
        [button(pricing_profiles_tr("title", language), cb(view, "pricing"))],
        [button(org_templates_tr("title", language), cb(view, "orgtemplates"))],
        [button(calculation_tr("title", language), cb(view, "calc"))],
        [button(tr("currency", language), cb(view, "currency"))],
    ]
    rows.extend(back_rows(view))
    await publish(callback, state, saved, view, text, rows)


def normalize_currency_query(value, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("Currency search text is invalid.")
    query = " ".join(normalize("NFKC", value).casefold().split())
    if (
        len(query) > 80
        or any(category(char).startswith("C") for char in query)
        or not query and not allow_empty
    ):
        raise ValueError("Currency search text is invalid.")
    return query


def filter_currencies(options, query):
    query = normalize_currency_query(query, allow_empty=True)
    if not query:
        return options
    tokens = query.split()
    matches = [
        item for item in options
        if all(
            token in normalize(
                "NFKC", item["code"] + " " + item["name"],
            ).casefold()
            for token in tokens
        )
    ]
    # Exact code first; otherwise keep the server catalog order.
    return sorted(matches, key=lambda item: item["code"].casefold() != query)


def catalog_screen(view, result, page):
    is_currency = resource(view) == "currency"
    query = normalize_currency_query(
        view.get("currency_query", "") if is_currency else "",
        allow_empty=True,
    )
    options = setting_options(result, view)
    if is_currency:
        options = filter_currencies(options, query)
    pages = max(1, (len(options) + PAGE_SIZE - 1) // PAGE_SIZE)
    if type(page) is not int or not 0 <= page < pages:
        raise ValueError("Settings page is unavailable.")

    view = menu_view(view, result, phase="catalog")
    view["page"] = page
    if query:
        view["currency_query"] = query
    visible = options[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    view["choice_codes"] = [item["code"] for item in visible]
    language = view["language"]
    text = (
        word(view, "language") + ": " + setting_name(result, view)[:160]
        + "\n\n" + word(view, "explanation")
    )
    if query:
        text += "\n\n" + tr("currency_search_query", language) + ": " + query

    rows = []
    if result["can_manage"] is True:
        if options:
            text += "\n\n" + word(view, "choose")
        elif is_currency:
            text += "\n\n" + tr("currency_search_empty", language)
        rows.extend([
            [button(
                ("✓ " if item["code"] == setting_code(result, view) else "")
                + option_label(item, view)[:90],
                cb(view, "c", index),
            )]
            for index, item in enumerate(visible)
        ])
        if page:
            rows.append([button(tr("previous", language), cb(view, "p", page - 1))])
        if page + 1 < pages:
            rows.append([button(tr("next", language), cb(view, "p", page + 1))])
        if is_currency:
            rows.append([button(
                tr("currency_search", language), cb(view, "search"),
            )])
            if query:
                rows.append([button(
                    tr("currency_search_all", language), cb(view, "all"),
                )])
    else:
        text += "\n\n" + tr("readonly", language)
    rows.extend(back_rows(view))
    return view, text, rows


async def choices(callback, state, saved, view, result, page):
    view, text, rows = catalog_screen(view, result, page)
    await publish(callback, state, saved, view, text, rows)


async def save(callback, state, saved, view, service):
    if view.get("phase") != "review":
        raise ValueError("Settings change is not ready.")
    code_argument = (
        "currency_code" if resource(view) == "currency" else "language_code"
    )
    view = {**view, "phase": "sending", "nonce": uuid4().hex}
    await state.update_data(**{VIEW: view})
    saved = snapshot(await state.get_data())
    try:
        result = await protocol(service, view).write(
            telegram_user_id=view["telegram_user_id"],
            tenant_id=UUID(view["tenant_id"]),
            expected_user_id=UUID(view["actor_user_id"]),
            expected_row_version=view["row_version"],
            **{code_argument: view["selected_code"]},
        )
        view = {
            **view, "phase": "done", "nonce": uuid4().hex,
            "language": result["language"],
        }
        text = word(
            view, "saved" if result["item"]["changed"] else "unchanged",
        )
    except Exception as error:
        logger.warning(
            "construction_bot_settings_save_failed resource=%s error_type=%s status=%s code=%s",
            resource(view), type(error).__name__,
            getattr(error, "status_code", None), getattr(error, "code", None),
        )
        status = getattr(error, "status_code", None)
        notice = (
            "conflict" if status == 409 else
            "denied" if status in (401, 403, 404) else
            "unavailable" if status in (400, 422, 429) else "uncertain"
        )
        view = {**view, "phase": "blocked", "nonce": uuid4().hex}
        text = word(view, notice)
    # After an uncertain write, only reread/navigation is offered.
    await publish(callback, state, saved, view, text, back_rows(view))


@router.callback_query(F.data.startswith("C2:"))
async def settings_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        saved = None
        view = None
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            data = await state.get_data()
            context = data.get(CONTEXT)
            if (
                not isinstance(context, dict)
                or context.get("telegram_user_id") != callback.from_user.id
                or data.get("last_menu_message_id") != callback.message.message_id
            ):
                raise ValueError("Stale settings context.")
            tenant = UUID(context["tenant_id"])
            if callback.data == "C2:open":
                from handlers.construction.construction_team import suspend_forms
                from handlers.construction.construction_area_create import suspend_area_form
                await suspend_forms(state)
                await suspend_area_form(state)
                view = {
                    "telegram_user_id": callback.from_user.id,
                    "tenant_id": str(tenant),
                    "actor_user_id": None, "language": language,
                    "menu_id": callback.message.message_id,
                    "phase": "opening", "nonce": uuid4().hex,
                }
                await state.update_data(**{VIEW: view})
                action, argument = "refresh", None
            else:
                view = deepcopy(data.get(VIEW))
                parts = callback.data.split(":")
                if (
                    len(parts) not in (3, 4)
                    or not bound(
                        data, view, callback.from_user.id,
                        callback.message.message_id,
                    )
                    or view.get("nonce") != parts[2]
                    or view.get("phase") == "sending"
                ):
                    raise ValueError("Stale settings callback.")
                action = parts[1]
                argument = None
                if action in ("p", "c", "up"):
                    if (
                        len(parts) != 4
                        or not parts[3].isascii()
                        or not parts[3].isdecimal()
                        or len(parts[3]) > 6
                        or str(int(parts[3])) != parts[3]
                    ):
                        raise ValueError("Invalid catalog position.")
                    argument = int(parts[3])
                elif len(parts) != 3 or action not in (
                    "refresh", "languages", "currency", "save", "units", "tax", "pricing", "calc", "region",
                    "search", "all", "onboarding", "orgtemplates",
                ):
                    raise ValueError("Unsupported settings action.")
            saved = snapshot(await state.get_data())
            language = view["language"]
            async with construction_bot_api_entry(
                bot=bot, event_update=event_update,
            ) as service:
                if action in ("languages", "currency"):
                    view = {
                        **view,
                        "resource": "currency" if action == "currency" else "language",
                        "currency_query": "",
                    }
                if action == "calc":
                    from handlers.construction.construction_calculation_settings import open_settings
                    await open_settings(callback, state, saved, view, service)
                    return
                if action == "region":
                    from handlers.construction.construction_region_settings import open_settings
                    await open_settings(callback, state, saved, view, service)
                    return
                if action == "onboarding":
                    from handlers.construction.construction_onboarding import open_guide
                    await open_guide(callback, state, saved, view, service)
                    return
                if action == "orgtemplates":
                    from handlers.construction.construction_organization_templates import open_templates
                    await open_templates(callback, state, saved, view, service)
                    return
                if action == "pricing":
                    from handlers.construction.construction_pricing_profiles import open_profiles
                    await open_profiles(callback, state, saved, view, service)
                    return
                if action == "tax":
                    from handlers.construction.construction_tax_profiles import open_profiles
                    await open_profiles(callback, state, saved, view, service)
                    return
                if action in ("units", "up"):
                    if action == "up" and view.get("phase") != "units":
                        raise ValueError("Unit catalog is not active.")
                    from handlers.construction.construction_unit_catalog import show_catalog
                    await show_catalog(
                        callback, state, saved, view, service,
                        page=argument if action == "up" else 0,
                    )
                    return
                if action == "save":
                    await save(callback, state, saved, view, service)
                    return
                if action in ("p", "c") and view.get("phase") != "catalog":
                    raise ValueError("Language catalog is not active.")
                if action == "c":
                    codes = view.get("choice_codes", [])
                    if not 0 <= argument < len(codes):
                        raise ValueError("Unavailable language choice.")
                    selected_code = codes[argument]
                if action in ("search", "all"):
                    if resource(view) != "currency" or view.get("phase") != "catalog":
                        raise ValueError("Currency catalog is not active.")
                    if action == "all":
                        view = {**view, "currency_query": ""}
                result = await read(service, view)
                if not unchanged(await state.get_data(), saved):
                    return
                if action == "refresh":
                    await menu(callback, state, saved, view, result)
                elif action == "search":
                    await search_prompt(callback, state, saved, view, result)
                elif action in ("languages", "currency", "p", "all"):
                    await choices(
                        callback, state, saved, view, result,
                        0 if action in ("languages", "currency", "all") else argument,
                    )
                elif action == "c":
                    if result["can_manage"] is not True:
                        raise problem(403, "permission_required")
                    if result["item"]["row_version"] != view["row_version"]:
                        raise problem(409, "ORGANIZATION_SETTINGS_CONFLICT")
                    option = next((
                        item for item in setting_options(result, view)
                        if item["code"] == selected_code
                    ), None)
                    if option is None:
                        raise problem(409, "LANGUAGE_CONFIGURATION_REQUIRED")
                    view = menu_view(view, result, phase="review")
                    view["selected_code"] = selected_code
                    language = view["language"]
                    text = (
                        word(view, "review") + "\n\n"
                        + setting_name(result, view)[:160]
                        + " → " + option_label(option, view)[:160]
                        + ("\n\n" + word(view, "explanation")
                           if resource(view) == "currency" else "")
                    )
                    rows = [
                        [button(tr("save", language), cb(view, "save"))],
                        [button(tr("cancel", language), cb(view, "refresh"))],
                    ]
                    await publish(callback, state, saved, view, text, rows)
        except Exception as error:
            logger.warning(
                "construction_bot_stage2_settings_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if saved is None or view is None:
                await callback.message.answer(t("construction_lead_form_stale", language))
                return
            status = getattr(error, "status_code", None)
            notice = (
                "conflict" if status == 409 else
                "denied" if status in (401, 403, 404) else "unavailable"
            )
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            await publish(
                callback, state, saved, view,
                tr(notice, language), back_rows(view),
            )

# CON_STAGE2_TELEGRAM_CURRENCY_V1

# CON_STAGE2_TELEGRAM_UNITS_V1

async def search_prompt(callback, state, saved, view, result):
    if result["can_manage"] is not True:
        raise problem(403, "permission_required")
    view = menu_view(view, result, phase="currency_search")
    language = view["language"]
    rows = [[button(tr("cancel", language), cb(view, "currency"))]]
    await publish(
        callback, state, saved, view,
        tr("currency_search_prompt", language), rows,
    )


async def currency_search_view(message, state):
    if (
        not private(message)
        or not isinstance(getattr(message, "text", None), str)
        or message.text.startswith("/")
        or await state.get_state() is not None
    ):
        return None
    data = await state.get_data()
    view = data.get(VIEW)
    if (
        not bound(
            data, view, message.from_user.id,
            data.get("last_menu_message_id"),
        )
        or view.get("phase") != "currency_search"
        or resource(view) != "currency"
    ):
        return None
    return deepcopy(view)


class CurrencySearchInput(BaseFilter):
    async def __call__(self, message, state):
        return await currency_search_view(message, state) is not None


async def publish_search_message(message, state, saved, view, text, rows):
    if not unchanged(await state.get_data(), saved):
        return
    sent = await message.answer(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not unchanged(await state.get_data(), saved):
        return
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    })


@router.message(CurrencySearchInput())
async def currency_search_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        view = await currency_search_view(message, state)
        if view is None:
            return
        saved = snapshot(await state.get_data())
        try:
            query = normalize_currency_query(message.text)
        except ValueError:
            if unchanged(await state.get_data(), saved):
                await message.answer(tr("currency_search_invalid", view["language"]))
            return

        try:
            async with construction_bot_api_entry(
                bot=bot, event_update=event_update,
            ) as service:
                result = await read(service, view)
            if not unchanged(await state.get_data(), saved):
                return
            if result["can_manage"] is not True:
                raise problem(403, "permission_required")

            view = {**view, "currency_query": query}
            view, text, rows = catalog_screen(view, result, 0)
            await publish_search_message(
                message, state, saved, view, text, rows,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_currency_search_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            notice = (
                "denied" if getattr(error, "status_code", None) in (401, 403, 404)
                else "unavailable"
            )
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            await publish_search_message(
                message, state, saved, view,
                tr(notice, view["language"]), back_rows(view),
            )


# CON_STAGE2_CURRENCY_SEARCH_V1
