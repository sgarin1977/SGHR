"""Explicit starter copying with recoverable, tenant/actor-bound requests."""
from copy import deepcopy
from html import escape
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction import construction_onboarding as guide
from handlers.construction import construction_stage2_settings as shell
from handlers.construction.construction_lead_create import private, lock_for
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_starter_templates_bot_api import StarterTemplatesBotApi
from services.construction.construction_language_bot_api import problem
from ui.construction_starter_templates import tr
from ui.construction_template_translation import template_heading
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_starter_templates_bot")
logger = logging.getLogger(__name__)
VIEW = "construction_starter_templates_bot_view"
PENDING = "construction_starter_template_copy_pending"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, PENDING, CONTEXT, guide.VIEW, shell.VIEW, "last_menu_message_id")


def snapshot(data):
    return {key: deepcopy(data.get(key)) for key in KEYS}


def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)


def slot(view):
    return view["tenant_id"] + ":" + view["actor_user_id"]


def pending(data):
    value = data.get(PENDING, {})
    if not isinstance(value, dict):
        raise ValueError("Pending copy state differs.")
    return deepcopy(value)


def fresh(view, result, phase):
    return {
        "telegram_user_id": view["telegram_user_id"], "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"], "language": result["language"],
        "menu_id": view["menu_id"], "phase": phase, "nonce": uuid4().hex,
    }


def cb(view, action, index=None):
    value = "STP:" + action + ":" + view["nonce"]
    return value if index is None else value + ":" + str(index)


def button(view, key, action):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cb(view, action),
    )


def back_rows(view):
    return [
        [button(view, "list", "list")],
        [button(view, "guide", "guide")],
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
        guide.VIEW: {}, shell.VIEW: {}, "last_menu_message_id": sent.message_id,
    })


async def navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("STP:"):
        if (await state.get_data()).get(VIEW):
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


async def recovery(callback, state, saved, view):
    view = {**view, "phase": "recovery", "nonce": uuid4().hex}
    await show(callback, state, saved, view, tr("pending", view["language"]), [
        [button(view, "retry", "retry")],
        back_rows(view)[1], back_rows(view)[2],
    ])


async def catalog(callback, state, saved, view, api, cursor=None):
    if pending(await state.get_data()).get(slot(view)):
        await recovery(callback, state, saved, view)
        return
    result = await api.list_starters(cursor=cursor, **guide.binding(view))
    view = fresh(view, result, "list")
    view["choices"] = [
        {name: item[name] for name in ("template_id", "version_id", "content_hash", "code")}
        for item in result["items"]
    ]
    view["next_cursor"] = result["next_cursor"]
    rows = [
        [InlineKeyboardButton(
            text=item["display_title"][:90], callback_data=cb(view, "pick", index),
        )]
        for index, item in enumerate(result["items"])
    ]
    text = tr("title", view["language"])
    if not rows:
        text += "\n\n" + tr("empty", view["language"])
    if result["next_cursor"] is not None:
        rows.append([button(view, "next", "next")])
    await show(callback, state, saved, view, text, rows + back_rows(view))


async def preview(callback, state, saved, view, api, index):
    choices = view.get("choices", [])
    if index is None or not 0 <= index < len(choices):
        return
    choice = deepcopy(choices[index])
    result = await api.get_starter(
        version_id=UUID(choice["version_id"]), **guide.binding(view),
    )
    item = result["item"]
    if any(item[name] != choice[name] for name in choice):
        raise problem(409, "SOURCE_CONTEXT_CHANGED")
    view = fresh(view, result, "review")
    view["source"] = {
        "source_version_id": item["version_id"],
        "source_template_id": item["template_id"],
        "source_code": item["code"], "content_hash": item["content_hash"],
        "section_count": item["section_count"], "question_count": item["question_count"],
    }
    language = view["language"]
    text = template_heading(item, language)
    text += (
        "\n\n" + tr("version", language) + ": " + str(item["version"])
        + "\n" + tr("sections", language) + ": " + str(item["section_count"])
        + "\n" + tr("questions", language) + ": " + str(item["question_count"])
        + "\n\n" + tr("note", language)
    )
    await show(callback, state, saved, view, text, [
        [button(view, "copy", "copy")],
        *back_rows(view),
    ])


async def execute(callback, state, saved, view, api, *, retry):
    data = await state.get_data()
    if not same(data, saved):
        return
    operations = pending(data)
    key = slot(view)
    if retry:
        operation = operations.get(key)
        if not isinstance(operation, dict):
            return
    else:
        if view.get("phase") != "review" or key in operations:
            return
        operation = {**deepcopy(view["source"]), "key": uuid4().hex}
        operations[key] = deepcopy(operation)
    sending = {**view, "phase": "sending", "nonce": uuid4().hex}
    await state.update_data(**{VIEW: sending, PENDING: operations})
    saved = {**saved, VIEW: deepcopy(sending), PENDING: deepcopy(operations)}
    if not same(await state.get_data(), saved):
        return
    try:
        result = await api.copy(request=deepcopy(operation), **guide.binding(view))
    except Exception as error:
        logger.warning(
            "construction_bot_starter_copy_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not same(await state.get_data(), saved):
            return
        status, code = getattr(error, "status_code", None), getattr(error, "code", None)
        definite = status in (400, 422) or code in (
            "TEMPLATE_COPY_CONFLICT", "TEMPLATE_BINDING_UNAVAILABLE",
        )
        if definite:
            operations.pop(key, None)
            await state.update_data(**{PENDING: operations})
            saved = {**saved, PENDING: deepcopy(operations)}
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            notice = (
                "exists" if code == "TEMPLATE_COPY_CONFLICT"
                else "binding" if code == "TEMPLATE_BINDING_UNAVAILABLE"
                else "unavailable"
            )
            await show(callback, state, saved, view,
                       tr(notice, view["language"]), back_rows(view))
        else:
            await recovery(callback, state, saved, view)
        return
    if not same(await state.get_data(), saved):
        return
    operations.pop(key, None)
    await state.update_data(**{PENDING: operations})
    saved = {**saved, PENDING: deepcopy(operations)}
    view = fresh(view, result, "done")
    await show(callback, state, saved, view,
               tr("copied", view["language"]), back_rows(view))


@router.callback_query(F.data.startswith("STP:"))
async def templates_callback(callback, state, bot=None, event_update=None):
    if not private(callback):
        await callback.answer(show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        if await state.get_state() is not None:
            return
        data = await state.get_data()
        view = None
        if callback.data == "STP:open":
            parent = data.get(guide.VIEW)
            if (
                not shell.bound(data, parent, callback.from_user.id, callback.message.message_id)
                or parent.get("phase") != "overview"
                or not parent.get("progress", {}).get("started")
                or parent["progress"]["current_step"] != "MEASUREMENT_TEMPLATES"
            ):
                return
            view = deepcopy(parent)
            action, index = "open", None
        else:
            view = deepcopy(data.get(VIEW))
            parts = callback.data.split(":")
            if (
                len(parts) not in (3, 4)
                or not shell.bound(data, view, callback.from_user.id, callback.message.message_id)
                or view.get("nonce") != parts[2] or view.get("phase") == "sending"
            ):
                return
            action, index = parts[1], None
            if action == "pick":
                if len(parts) != 4 or parts[3] not in {str(n) for n in range(6)}:
                    return
                index = int(parts[3])
            elif len(parts) != 3 or action not in ("list", "next", "copy", "retry", "guide"):
                return
            if action in ("pick", "next") and view.get("phase") != "list":
                return
            if action == "copy" and view.get("phase") != "review":
                return
        saved = snapshot(data)
        try:
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = StarterTemplatesBotApi(service)
                if action == "open":
                    result = await api.read(**guide.binding(view))
                    if (
                        not result["item"]["started"]
                        or result["item"]["current_step"] != "MEASUREMENT_TEMPLATES"
                    ):
                        raise problem(409, "ONBOARDING_STATE_CONFLICT")
                    view = fresh(view, result, "opening")
                    await catalog(callback, state, saved, view, api)
                elif action in ("copy", "retry"):
                    await execute(callback, state, saved, view, api, retry=action == "retry")
                elif action == "guide":
                    result = await api.read(**guide.binding(view))
                    if not same(await state.get_data(), saved):
                        return
                    await state.update_data(**{VIEW: {}})
                    expected = {
                        name: deepcopy(saved.get(name)) for name in guide.KEYS
                    }
                    if not guide.same(await state.get_data(), expected):
                        return
                    await guide.overview(callback, state, expected, view, result)
                elif action == "pick":
                    await preview(callback, state, saved, view, api, index)
                elif action == "next":
                    if view.get("next_cursor") is not None:
                        await catalog(callback, state, saved, view, api, view["next_cursor"])
                else:
                    await catalog(callback, state, saved, view, api)
        except Exception as error:
            logger.warning(
                "construction_bot_starter_read_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            view = {**view, "phase": "blocked", "nonce": uuid4().hex}
            notice = "denied" if getattr(error, "status_code", None) in (401, 403, 404) else "unavailable"
            await show(callback, state, saved, view,
                       tr(notice, view["language"]), back_rows(view))
