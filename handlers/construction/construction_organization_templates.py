"""Published organization template list and exact-version cards."""
from copy import deepcopy
from html import escape
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction import construction_stage2_settings as shell
from handlers.construction.construction_lead_create import private, lock_for
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_organization_templates_bot_api import OrganizationTemplatesBotApi
from services.construction.construction_language_bot_api import problem
from ui.construction_organization_templates import tr
from ui.construction_template_translation import template_heading
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_organization_templates_bot")
logger = logging.getLogger(__name__)
VIEW = "construction_organization_templates_bot_view"
CONTEXT = "construction_suite_context"
PENDING = "construction_template_draft_pending_creates"
DRAFTS = "construction_template_draft_known_selectors"
HEADER_PENDING = "construction_template_draft_header_pending"
PUBLISH_PENDING = "construction_template_publication_pending"
PUBLISH_ACTIONS = ("pubreview", "pubconfirm", "pubretry", "pubcancel", "pubopen")
KEYS = (VIEW, PENDING, DRAFTS, HEADER_PENDING, PUBLISH_PENDING, CONTEXT, shell.VIEW, "last_menu_message_id")
HEADER_ACTIONS = ("hdr_title", "hdr_description", "hdr_clear", "hdr_save", "hdr_retry", "hdr_back")
DRAFT_ACTIONS = ("draftnew", "draftcreate", "draftretry", "draftopen", "draftrefresh", "draftcheck")
STRUCTURE_ACTIONS = (
    "str_sections", "str_sp", "str_section", "str_qp",
    "str_question", "str_refresh", "str_draft", "str_label",
    "str_active", "str_required", "str_order",
)


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


def fresh(view, result, phase):
    return {
        "telegram_user_id": view["telegram_user_id"], "tenant_id": view["tenant_id"],
        "actor_user_id": result["actor_user_id"], "language": result["language"],
        "menu_id": view["menu_id"], "phase": phase, "nonce": uuid4().hex,
    }


def cb(view, action, index=None):
    value = "OTC:" + action + ":" + view["nonce"]
    return value if index is None else value + ":" + str(index)


def button(view, key, action):
    return InlineKeyboardButton(
        text=tr(key, view["language"]), callback_data=cb(view, action),
    )


def back_rows(view):
    return [
        [button(view, "list", "list")],
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
    if state is not None and private(event) and not (event.data or "").startswith("OTC:"):
        if (await state.get_data()).get(VIEW):
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


async def listing(callback, state, saved, view, api, cursor=None):
    result = await api.list_templates(cursor=cursor, **binding(view))
    view = fresh(view, result, "list")
    view["choices"] = [
        {name: item[name] for name in ("template_id", "version_id", "content_hash")}
        for item in result["items"]
    ]
    view["next_cursor"] = result["next_cursor"]
    text = tr("title", view["language"])
    if not result["items"]:
        text += "\n\n" + tr("empty", view["language"])
    rows = [
        [InlineKeyboardButton(
            text=("⏸ " if not item["active"] else "") + item["display_title"][:85],
            callback_data=cb(view, "open", index),
        )]
        for index, item in enumerate(result["items"])
    ]
    if result["next_cursor"] is not None:
        rows.append([button(view, "next", "next")])
    from handlers.construction.construction_template_drafts import recovery_rows
    rows.extend(recovery_rows(view, await state.get_data()))
    from handlers.construction.construction_template_draft_header import recovery_rows as header_recovery
    rows.extend(header_recovery(view, await state.get_data()))
    from handlers.construction.construction_template_publish import recovery_rows as publication_recovery
    rows.extend(publication_recovery(view, await state.get_data()))
    await show(callback, state, saved, view, text, rows + back_rows(view))


async def card(callback, state, saved, view, api, choice):
    result = await api.get_template(
        version_id=UUID(choice["version_id"]), **binding(view),
    )
    item = result["item"]
    if any(item[name] != choice[name] for name in choice):
        raise problem(409, "SOURCE_CONTEXT_CHANGED")
    view = fresh(view, result, "card")
    view["choice"] = deepcopy(choice)
    language = view["language"]
    text = template_heading(item, language)
    text += (
        "\n\n" + tr("active" if item["active"] else "inactive", language)
        + "\n" + tr("version", language) + ": " + str(item["version"])
        + "\n" + tr("sections", language) + ": " + str(item["section_count"])
        + "\n" + tr("questions", language) + ": " + str(item["question_count"])
    )
    from handlers.construction.construction_template_drafts import (
        attach_navigation, draft_card_rows,
    )
    data = await state.get_data()
    view = attach_navigation(view, item, data)
    from handlers.construction.construction_template_draft_header import recovery_rows as header_recovery
    from handlers.construction.construction_template_publish import recovery_rows as publication_recovery
    await show(callback, state, saved, view, text, [
        *draft_card_rows(view, data),
        *header_recovery(view, data),
        *publication_recovery(view, data),
        [button(view, "refresh_version", "refresh")],
        *back_rows(view),
    ])


async def open_templates(callback, state, source, parent, service):
    if not shell.unchanged(await state.get_data(), source):
        return
    await listing(
        callback, state, snapshot(await state.get_data()),
        parent, OrganizationTemplatesBotApi(service),
    )


@router.callback_query(F.data.startswith("OTC:"))
async def templates_callback(callback, state, bot=None, event_update=None):
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
        action, index = parts[1], None
        if action in ("open", "str_section", "str_question"):
            if len(parts) != 4 or parts[3] not in {str(n) for n in range(6)}:
                return
            index = int(parts[3])
        elif action in ("str_sp", "str_qp"):
            if (
                len(parts) != 4 or not parts[3].isascii()
                or not parts[3].isdecimal() or len(parts[3]) > 3
                or str(int(parts[3])) != parts[3] or int(parts[3]) >= 334
            ):
                return
            index = int(parts[3])
        elif len(parts) != 3 or action not in ("list", "next", "refresh") + DRAFT_ACTIONS + HEADER_ACTIONS + STRUCTURE_ACTIONS + PUBLISH_ACTIONS:
            return
        if action in ("open", "next") and view.get("phase") != "list":
            return
        if action == "refresh" and view.get("phase") != "card":
            return
        saved = snapshot(data)
        try:
            if action in PUBLISH_ACTIONS:
                from handlers.construction.construction_template_publish import handle_publication_action
                await handle_publication_action(
                    callback, state, saved, view, action,
                    bot=bot, event_update=event_update,
                )
                return
            if action in STRUCTURE_ACTIONS:
                from handlers.construction.construction_template_draft_structure import handle_structure_action
                await handle_structure_action(
                    callback, state, saved, view, action, index,
                    bot=bot, event_update=event_update,
                )
                return
            if action in HEADER_ACTIONS:
                from handlers.construction.construction_template_draft_header import handle_header_action
                await handle_header_action(
                    callback, state, saved, view, action,
                    bot=bot, event_update=event_update,
                )
                return
            if action in DRAFT_ACTIONS:
                from handlers.construction.construction_template_drafts import handle_draft_action
                await handle_draft_action(
                    callback, state, saved, view, action,
                    bot=bot, event_update=event_update,
                )
                return
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                api = OrganizationTemplatesBotApi(service)
                if action == "open":
                    choices = view.get("choices", [])
                    if index >= len(choices):
                        return
                    choice = deepcopy(choices[index])
                    await card(callback, state, saved, view, api, choice)
                elif action == "refresh":
                    await card(callback, state, saved, view, api, deepcopy(view["choice"]))
                elif action == "next":
                    if view.get("next_cursor") is not None:
                        await listing(callback, state, saved, view, api, view["next_cursor"])
                else:
                    await listing(callback, state, saved, view, api)
        except Exception as error:
            logger.warning(
                "construction_bot_organization_templates_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            view = {
                key: view[key] for key in (
                    "telegram_user_id", "tenant_id", "actor_user_id", "language", "menu_id",
                )
            }
            view.update(phase="blocked", nonce=uuid4().hex)
            notice = "denied" if getattr(error, "status_code", None) in (401, 403, 404) else "unavailable"
            await show(callback, state, saved, view,
                       tr(notice, view["language"]), back_rows(view))


# CON_STAGE2_DRAFT_HEADER_INPUT_V1
from handlers.construction.construction_template_draft_header import HeaderInput, header_message
router.message.register(header_message, HeaderInput())
