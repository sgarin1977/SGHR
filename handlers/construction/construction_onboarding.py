"""Saved onboarding progress; canonical completion stays in the API."""
from copy import deepcopy
from html import escape
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction import construction_stage2_settings as shell
from handlers.construction.construction_lead_create import private, lock_for
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_onboarding_bot_api import (
    OnboardingBotApi, STEPS, SKIPPABLE,
)
from ui.construction_onboarding import tr
from ui.construction_starter_templates import tr as starter_tr
from ui.construction_onboarding_tax import tr as tax_tr
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_onboarding_bot")
logger = logging.getLogger(__name__)
VIEW = "construction_onboarding_bot_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, shell.VIEW, "last_menu_message_id")
TAX_ACTIONS = ("tax", "taxnext", "taxpick", "taxsave")


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


def cb(view, action, index=None):
    value = "OBG:" + action + ":" + view["nonce"]
    return value if index is None else value + ":" + str(index)


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


async def show(callback, state, saved, view, text, rows):
    if not same(await state.get_data(), saved):
        return
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not same(await state.get_data(), saved):
        return
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id},
        shell.VIEW: {}, "last_menu_message_id": sent.message_id,
    })


async def navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith(("OBG:", "STP:", "OBC:")):
        if (await state.get_data()).get(VIEW):
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


async def overview(callback, state, saved, view, result):
    item = result["item"]
    view = {
        "telegram_user_id": view["telegram_user_id"], "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"], "language": result["language"],
        "menu_id": view["menu_id"], "phase": "overview", "nonce": uuid4().hex,
        "progress": deepcopy(item),
    }
    language = view["language"]
    text = tr("title", language) + "\n\n" + escape(result["organization"][:200])
    rows = []
    if not item["started"]:
        text += "\n\n" + tr("not_started", language)
        rows.append([button(view, "start", "start")])
    else:
        states = item["step_states"]
        for index, step in enumerate(STEPS):
            text += "\n\n" + str(index + 1) + ". " + tr(step, language)
            text += " — " + tr(states[step], language)
            rows.append([InlineKeyboardButton(
                text=str(index + 1) + ". " + tr(step, language),
                callback_data=cb(view, "step", index),
            )])
        current = item["current_step"]
        text += "\n\n" + tr("current", language) + ": " + tr(current, language)
        if current == "TAXES":
            rows.append([InlineKeyboardButton(
                text=tax_tr("choose", language), callback_data=cb(view, "tax"),
            )])
        from handlers.construction.construction_onboarding_settings import configuration_rows
        rows.extend(configuration_rows(view, current))
        if states[current] != "COMPLETED":
            rows.append([button(view, "complete", "complete")])
        if current == "MEASUREMENT_TEMPLATES":
            rows.append([InlineKeyboardButton(
                text=starter_tr("title", language), callback_data="STP:open",
            )])
        if current in SKIPPABLE and states[current] != "SKIPPED":
            rows.append([button(view, "skip", "skip")])
    text += "\n\n" + tr("note", language)
    await show(callback, state, saved, view, text, rows + back_rows(view))


async def review(callback, state, saved, view, request):
    view = {
        **view, "phase": "review", "nonce": uuid4().hex,
        "request": deepcopy(request),
    }
    text = tr(request["action"], view["language"])
    if request["step"] is not None:
        text += "\n\n" + tr(request["step"], view["language"])
    await show(callback, state, saved, view, text, [
        [button(view, "confirm", "confirm")],
        [button(view, "cancel", "refresh")],
        back_rows(view)[1],
    ])


async def blocked(callback, state, saved, view, error, *, writing=False):
    code, status = getattr(error, "code", None), getattr(error, "status_code", None)
    key = (
        "not_ready" if code == "ONBOARDING_STEP_NOT_READY" else
        "step_unavailable" if code == "ONBOARDING_STEP_UNAVAILABLE" else
        "conflict" if status == 409 else
        "denied" if status in (401, 403, 404) else
        "unavailable" if status in (400, 422, 429) or not writing else "uncertain"
    )
    logger.warning(
        "construction_bot_onboarding_failed error_type=%s status=%s code=%s",
        type(error).__name__, status, code,
    )
    view = {
        key: view[key] for key in (
            "telegram_user_id", "tenant_id", "actor_user_id", "language", "menu_id",
        )
    }
    view.update(phase="blocked", nonce=uuid4().hex)
    await show(callback, state, saved, view, tr(key, view["language"]), back_rows(view))


async def open_guide(callback, state, source, parent, service):
    if not shell.unchanged(await state.get_data(), source):
        return
    saved = snapshot(await state.get_data())
    result = await OnboardingBotApi(service).read(**binding(parent))
    await overview(callback, state, saved, parent, result)


@router.callback_query(F.data.startswith("OBG:"))
async def onboarding_callback(callback, state, bot=None, event_update=None):
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
            len(parts) not in (3, 4)
            or not shell.bound(data, view, callback.from_user.id, callback.message.message_id)
            or view.get("nonce") != parts[2]
            or view.get("phase") == "sending"
        ):
            return
        action = parts[1]
        index = None
        if action in ("step", "taxpick"):
            if len(parts) != 4 or parts[3] not in {
                str(n) for n in range(7 if action == "step" else 10)
            }:
                return
            index = int(parts[3])
        elif len(parts) != 3 or action not in (
            "refresh", "start", "complete", "skip", "confirm",
            *TAX_ACTIONS,
        ):
            return
        if action == "confirm" and view.get("phase") != "review":
            return
        if action not in ("refresh", "confirm") + TAX_ACTIONS and view.get("phase") != "overview":
            return
        saved = snapshot(data)
        writing = False
        try:
            if action in TAX_ACTIONS:
                from handlers.construction.construction_onboarding_tax import handle_tax_action
                await handle_tax_action(
                    callback, state, saved, view, action, index,
                    bot=bot, event_update=event_update,
                )
                return
            if action not in ("refresh", "confirm"):
                item = view["progress"]
                if action == "start":
                    if item["started"]:
                        return
                    operation, step = "start", None
                else:
                    if not item["started"]:
                        return
                    operation = "open" if action == "step" else action
                    step = STEPS[index] if action == "step" else item["current_step"]
                    if operation == "skip" and step not in SKIPPABLE:
                        return
                await review(callback, state, saved, view, {
                    "action": operation, "step": step,
                    "expected_row_version": item["row_version"],
                })
                return

            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = OnboardingBotApi(service)
                if action == "refresh":
                    result = await api.read(**binding(view))
                else:
                    request = deepcopy(view["request"])
                    view = {**view, "phase": "sending", "nonce": uuid4().hex}
                    await state.update_data(**{VIEW: view})
                    saved = snapshot(await state.get_data())
                    writing = True
                    # No preread, rebasing or automatic retry.
                    result = await api.write(request=request, **binding(view))
            await overview(callback, state, saved, view, result)
        except Exception as error:
            await blocked(callback, state, saved, view, error, writing=writing)
