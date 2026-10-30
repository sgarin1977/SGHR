"""Read-only lead status filters using server-owned pagination."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_lead_filters import STATUS_ORDER, tr, status_caption
from ui.construction_lead_status import status_label
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_lead_filters")
logger = logging.getLogger(__name__)
VIEW = "construction_lead_filter_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, "last_menu_message_id")


def button(text, value):
    return InlineKeyboardButton(text=text, callback_data=value)


def cb(view, action, index=None):
    value = "LF:" + action + ":" + view["nonce"]
    return value if index is None else value + ":" + str(index)


def same(data, saved):
    return all(data.get(key) == saved.get(key) for key in KEYS)


async def filter_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("LF:"):
        view = (await state.get_data()).get(VIEW)
        if isinstance(view, dict) and view.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{VIEW: {}})
    return await handler(event, data)


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
        "last_menu_message_id": sent.message_id,
    })


@router.callback_query(F.data.startswith("LF:"))
async def filter_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        saved = None
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            data = await state.get_data()
            context = data.get(CONTEXT)
            if (
                not isinstance(context, dict)
                or context.get("telegram_user_id") != callback.from_user.id
                or callback.message.message_id != data.get("last_menu_message_id")
            ):
                raise ValueError("Lead filter context is unavailable.")
            tenant_id = UUID(context["tenant_id"])
            status, cursor, expected_user_id = None, None, None
            mine_only = False
            choosing = callback.data == "LF:open"
            if choosing:
                action = "open"
            else:
                parts = callback.data.split(":")
                old = data.get(VIEW)
                if (
                    len(parts) not in (3, 4) or not isinstance(old, dict)
                    or old.get("nonce") != parts[2]
                    or old.get("telegram_user_id") != callback.from_user.id
                    or old.get("tenant_id") != str(tenant_id)
                    or old.get("menu_id") != callback.message.message_id
                ):
                    raise ValueError("Stale lead filter command.")
                action = parts[1]
                status = old["status"]
                mine_only = old.get("mine_only", False)
                if type(mine_only) is not bool:
                    raise ValueError("Saved responsible filter is invalid.")
                if status is not None and status not in STATUS_ORDER:
                    raise ValueError("Unknown saved lead status.")
                expected_user_id = UUID(old["actor_user_id"])
                if action == "set":
                    if len(parts) != 4 or old["phase"] != "choose":
                        raise ValueError("Choose a status first.")
                    choices = (None, *STATUS_ORDER)
                    if parts[3] not in {str(index) for index in range(len(choices))}:
                        raise ValueError("Invalid status selector.")
                    status = choices[int(parts[3])]
                elif action == "mine":
                    if len(parts) != 3 or old["phase"] not in ("choose", "list"):
                        raise ValueError("Open the lead filters first.")
                    mine_only = not mine_only
                    choosing = old["phase"] == "choose"
                    cursor = None
                elif action in ("choose", "next", "refresh"):
                    if len(parts) != 3 or old["phase"] != "list":
                        raise ValueError("Open the filtered list first.")
                    choosing = action == "choose"
                    if action == "next":
                        cursor = old.get("next_cursor")
                        if not cursor:
                            raise ValueError("Next filtered page is unavailable.")
                else:
                    raise ValueError("Unknown lead filter action.")
            saved = deepcopy({key: data.get(key) for key in KEYS})
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await service.leads(
                    telegram_user_id=callback.from_user.id, tenant_id=tenant_id,
                    cursor=cursor, status=status, expected_user_id=expected_user_id,
                    mine_only=mine_only,
                )
            if not same(await state.get_data(), saved):
                return
            actor = UUID(result["actor_user_id"])
            if expected_user_id is not None and actor != expected_user_id:
                raise ValueError("Canonical lead reader changed.")
            language = result["language"] or language
            view = {
                "telegram_user_id": callback.from_user.id,
                "tenant_id": str(tenant_id), "actor_user_id": str(actor),
                "nonce": uuid4().hex, "status": status, "mine_only": mine_only,
                "phase": "choose" if choosing else "list",
                "next_cursor": None if choosing else result["next_cursor"],
            }
            rows = []
            if choosing:
                text = "\n\n".join([
                    t("construction_bot_leads_title", language),
                    str(result["organization"])[:120], tr("choose", language),
                ])
                for index, value in enumerate((None, *STATUS_ORDER)):
                    rows.append([button(status_caption(value, language), cb(view, "set", index))])
            else:
                text = "\n\n".join([
                    t("construction_bot_leads_title", language),
                    str(result["organization"])[:120],
                    tr("current", language) + ": " + status_caption(status, language),
                ])
                for item in result["items"]:
                    rows.append([button(
                        str(item["name"])[:50] + " · " + status_label(item["status"], language),
                        "CS_LEAD:" + UUID(item["id"]).hex,
                    )])
                if not result["items"]:
                    text += "\n\n" + tr("empty", language)
                if result["next_cursor"]:
                    rows.append([button(
                        t("construction_bot_next_btn", language), cb(view, "next"),
                    )])
                rows.extend([
                    [button(tr("refresh", language), cb(view, "refresh"))],
                    [button(tr("button", language), cb(view, "choose"))],
                ])
            text += "\n\n" + tr("responsible", language) + ": " + tr(
                "me" if mine_only else "any_responsible", language,
            )
            rows.append([button(
                tr("all_responsible" if mine_only else "mine_button", language),
                cb(view, "mine"),
            )])
            rows.extend([
                [button(tr("back", language), "CS_LEADS")],
                [button(t("construction_bot_home_btn", language), "CS_HOME")],
            ])
            await show(callback, state, saved, view, text, rows)
        except Exception as error:
            logger.warning(
                "construction_bot_lead_filter_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if saved is not None and same(await state.get_data(), saved):
                await state.update_data(**{VIEW: {}})
                sent = await edit_or_replace_menu_message(
                    callback=callback, text=t("construction_bot_unavailable", language),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [button(tr("back", language), "CS_LEADS")],
                        [button(t("construction_bot_home_btn", language), "CS_HOME")],
                    ]),
                )
                current = await state.get_data()
                if current.get(CONTEXT) == saved.get(CONTEXT) and current.get(VIEW) == {}:
                    await state.update_data(last_menu_message_id=sent.message_id)
            else:
                await callback.message.answer(t("construction_lead_form_stale", language))
