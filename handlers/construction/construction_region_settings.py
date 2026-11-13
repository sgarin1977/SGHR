"""Explicit organization region form over the existing Platform API."""
from copy import deepcopy
from html import escape
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction import construction_stage2_settings as shell
from handlers.construction.construction_lead_create import private, lock_for
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_region_bot_api import (
    RegionBotApi, country_input, timezone_input,
)
from ui.construction_region_settings import tr, region_text
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_region_settings_bot")
logger = logging.getLogger(__name__)
VIEW = "construction_region_settings_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, shell.VIEW, "last_menu_message_id")


def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}


def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)


def bound(data, view, sender, menu_id):
    return shell.bound(data, view, sender, menu_id)


def binding(view):
    return {
        "telegram_user_id": view["telegram_user_id"],
        "tenant_id": UUID(view["tenant_id"]),
        "expected_user_id": UUID(view["actor_user_id"]),
    }


def fresh(view, result, phase):
    return {
        "telegram_user_id": view["telegram_user_id"],
        "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"],
        "language": result["language"],
        "menu_id": view["menu_id"],
        "phase": phase, "nonce": uuid4().hex,
    }


def cb(view, action):
    return "REG:" + action + ":" + view["nonce"]


def button(view, key, action):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cb(view, action),
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
    if state is not None and private(event) and not (event.data or "").startswith("REG:"):
        if (await state.get_data()).get(VIEW):
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


async def menu(event, state, saved, view, result):
    view = fresh(view, result, "menu")
    text = (
        tr("title", view["language"]) + "\n\n"
        + escape(result["organization"][:200]) + "\n\n"
        + region_text(result["item"], view["language"])
    )
    rows = [[button(view, "edit", "edit")]] if result["can_manage"] is True else []
    await show(event, state, saved, view, text, rows + back_rows(view))


async def form(event, state, saved, view):
    view = {**view, "nonce": uuid4().hex}
    language = view["language"]
    rows = []
    if view["phase"] == "review":
        text = (
            tr("review", language) + "\n\n"
            + tr("country", language) + ": " + view["country_code"]
            + "\n" + tr("timezone", language) + ": " + view["timezone"]
        )
        rows.append([button(view, "save", "save")])
    else:
        text = tr(view["phase"] + "_prompt", language)
    rows.append([button(view, "cancel", "refresh")])
    rows.append(back_rows(view)[1])
    await show(event, state, saved, view, text, rows)


async def blocked(event, state, saved, view, error, *, writing=False):
    status = getattr(error, "status_code", None)
    key = (
        "conflict" if status == 409 else
        "denied" if status in (401, 403, 404) else
        "invalid_region" if status in (400, 422) else
        "unavailable" if status == 429 or not writing else "uncertain"
    )
    logger.warning(
        "construction_bot_region_failed error_type=%s status=%s code=%s",
        type(error).__name__, status, getattr(error, "code", None),
    )
    # Discard the draft. Only a fresh read can start another attempt.
    view = {
        key: view[key] for key in (
            "telegram_user_id", "tenant_id", "actor_user_id", "language", "menu_id",
        )
    }
    view.update(phase="blocked", nonce=uuid4().hex)
    await show(event, state, saved, view, tr(key, view["language"]), back_rows(view))


async def open_settings(callback, state, source, parent, service):
    if not shell.unchanged(await state.get_data(), source):
        return
    view = {
        **parent, "phase": "opening", "nonce": uuid4().hex,
    }
    saved = snapshot(await state.get_data())
    result = await RegionBotApi(service).read(**binding(view))
    await menu(callback, state, saved, view, result)


async def save(callback, state, saved, view, api):
    if view.get("phase") != "review" or not same(await state.get_data(), saved):
        return
    view = {**view, "phase": "sending", "nonce": uuid4().hex}
    await state.update_data(**{VIEW: view})
    saved = snapshot(await state.get_data())
    try:
        result = await api.write(
            **binding(view), country_code=view["country_code"],
            timezone_code=view["timezone"],
            expected_row_version=view["expected_row_version"],
        )
    except Exception as error:
        await blocked(callback, state, saved, view, error, writing=True)
        return
    view = fresh(view, result, "done")
    key = "saved" if result["item"]["changed"] else "unchanged"
    await show(callback, state, saved, view, tr(key, view["language"]), back_rows(view))


@router.callback_query(F.data.startswith("REG:"))
async def region_callback(callback, state, bot=None, event_update=None):
    if not private(callback):
        await callback.answer(show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        if await state.get_state() is not None:
            return
        data = await state.get_data()
        view = deepcopy(data.get(VIEW))
        parts = callback.data.split(":")
        if (
            len(parts) != 3
            or not bound(data, view, callback.from_user.id, callback.message.message_id)
            or view.get("nonce") != parts[2]
            or view.get("phase") == "sending"
            or parts[1] not in ("refresh", "edit", "save")
        ):
            return
        action = parts[1]
        if action == "edit" and view.get("phase") != "menu":
            return
        if action == "save" and view.get("phase") != "review":
            return
        saved = snapshot(data)
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = RegionBotApi(service)
                if action == "save":
                    await save(callback, state, saved, view, api)
                    return
                result = await api.read(**binding(view))
            if action == "refresh":
                await menu(callback, state, saved, view, result)
            else:
                view = {
                    **fresh(view, result, "country"),
                    "expected_row_version": result["item"]["row_version"],
                }
                await form(callback, state, saved, view)
        except Exception as error:
            await blocked(callback, state, saved, view, error)


class RegionInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message)
            or not isinstance(getattr(message, "text", None), str)
            or message.text.startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(VIEW)
        return (
            bound(data, view, message.from_user.id, data.get("last_menu_message_id"))
            and view.get("phase") in ("country", "timezone")
        )


@router.message(RegionInput())
async def region_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await RegionInput()(message, state):
            return
        data = await state.get_data()
        view, saved = deepcopy(data[VIEW]), snapshot(data)
        field = view["phase"]
        try:
            value = (
                country_input(message.text) if field == "country"
                else timezone_input(message.text)
            )
        except ValueError:
            await message.answer(tr(field + "_prompt", view["language"]))
            return
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                _, context = await RegionBotApi(service).context(**binding(view))
            if not same(await state.get_data(), saved):
                return
            view["language"] = context["language"]
            if field == "country":
                view.update(country_code=value, phase="timezone")
            else:
                view.update(timezone=value, phase="review")
            await form(message, state, saved, view)
        except Exception as error:
            await blocked(message, state, saved, view, error)
