"""Client catalog and CRM hub through the shared API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_client_catalog import tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_client_catalog")
logger = logging.getLogger(__name__)
LIST = "construction_client_catalog_view"
CARD = "construction_client_card_view"
CONTEXT = "construction_suite_context"
KEYS = (LIST, CARD, CONTEXT, "last_menu_message_id")


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def back_rows(language):
    return [
        [button(tr("hub", language), "CM:open")],
        [button(t("construction_bot_home_btn", language), "CS_HOME")],
    ]


def binding(data, sender, menu):
    context, view = data.get(CONTEXT), data.get(LIST)
    if (
        not isinstance(context, dict) or not isinstance(view, dict)
        or context.get("telegram_user_id") != sender
        or view.get("telegram_user_id") != sender
        or view.get("tenant_id") != context.get("tenant_id")
        or view.get("menu_id") != menu or data.get("last_menu_message_id") != menu
        or view.get("screen") not in ("list", "card")
        or not isinstance(view.get("client_ids"), list)
        or len(view["client_ids"]) > 5
        or len(set(view["client_ids"])) != len(view["client_ids"])
    ):
        return None
    UUID(view["actor_user_id"])
    for value in view["client_ids"]:
        if str(UUID(value)) != value:
            return None
    return view


def select_client(data, *, token, index, sender, menu):
    view = binding(data, sender, menu)
    if view is None or view["screen"] != "list" or view["nonce"] != token:
        raise ValueError("Client catalog selection is stale.")
    number = int(index)
    if str(number) != index or not 0 <= number < len(view["client_ids"]):
        raise ValueError("Invalid client selector.")
    return {
        "client_id": view["client_ids"][number],
        "actor_user_id": view["actor_user_id"], "catalog_nonce": view["nonce"],
    }


def sync_origin(data, client_view, menu_id):
    """Carry only the catalog position across existing card/project screens."""
    if not isinstance(client_view, dict) or not client_view.get("catalog_nonce"):
        return {LIST: {}} if data.get(LIST) else {}
    listing = binding(
        data, client_view["telegram_user_id"], data.get("last_menu_message_id"),
    )
    if (
        listing is None or listing["nonce"] != client_view["catalog_nonce"]
        or client_view.get("client_id") not in listing["client_ids"]
        or any(client_view.get(key) != listing.get(key)
               for key in ("telegram_user_id", "tenant_id", "actor_user_id"))
    ):
        raise ValueError("Client catalog origin changed.")
    return {LIST: {**listing, "screen": "card", "menu_id": menu_id}}


async def client_catalog_navigation_middleware(handler, event, data):
    state = data.get("state")
    if (
        state is not None and private(event)
        and not (event.data or "").startswith(("PJA:", "AT:", "PAV:", "PDE:", "CDE:", "CLO:", "CL:", "CC:", "PC:", "PA:", "AC:", "AE:", "PT:", "PG:", "PX:", "TP:", "AP:project:"))
    ):
        saved = (await state.get_data()).get(LIST)
        if isinstance(saved, dict) and saved.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{LIST: {}})
    return await handler(event, data)


def unchanged(data, snapshot):
    return all(data.get(key) == snapshot.get(key) for key in KEYS)


async def show(callback, state, snapshot, text, rows, listing=None):
    if not unchanged(await state.get_data(), snapshot):
        return
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not unchanged(await state.get_data(), snapshot):
        return
    await state.update_data(**{
        LIST: {**listing, "menu_id": sent.message_id} if listing else {},
        CARD: {}, "last_menu_message_id": sent.message_id,
    })


@router.callback_query((F.data == "CM:open") | F.data.startswith("CL:"))
async def catalog_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        snapshot = None
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
                raise ValueError("Stale Construction context.")
            tenant = UUID(context["tenant_id"])
            cursor, expected_user = None, None
            hub = callback.data == "CM:open"
            if hub or callback.data == "CL:open":
                from handlers.construction.construction_team import suspend_forms
                from handlers.construction.construction_area_create import suspend_area_form
                await suspend_forms(state)
                await suspend_area_form(state)
                await state.update_data(**{
                    CARD: {}, "construction_project_card_view": {},
                    "construction_project_areas_view": {},
                })
            else:
                parts = callback.data.split(":")
                listing = binding(data, callback.from_user.id, callback.message.message_id)
                if len(parts) != 3 or listing is None or parts[2] != listing["nonce"]:
                    raise ValueError("Stale client catalog command.")
                expected_user = UUID(listing["actor_user_id"])
                action = parts[1]
                if action == "back":
                    card = data.get(CARD)
                    sync_origin(data, card, callback.message.message_id)
                    if (
                        listing["screen"] != "card" or not isinstance(card, dict)
                        or card.get("catalog_nonce") != listing["nonce"]
                    ):
                        raise ValueError("Client card origin unavailable.")
                    cursor = listing["cursor"]
                elif listing["screen"] != "list":
                    raise ValueError("Open the client list first.")
                elif action == "next":
                    cursor = listing["next_cursor"]
                    if cursor is None:
                        raise ValueError("No next client page.")
                elif action == "refresh":
                    cursor = listing["cursor"]
                elif action != "first":
                    raise ValueError("Unknown client catalog command.")
            data = await state.get_data()
            snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = (
                    await service.home(
                        telegram_user_id=callback.from_user.id, tenant_id=tenant,
                    ) if hub else await service.client_catalog(
                        telegram_user_id=callback.from_user.id, tenant_id=tenant,
                        cursor=cursor, expected_user_id=expected_user,
                    )
                )
            if not unchanged(await state.get_data(), snapshot):
                raise ValueError("Client catalog context changed.")
            language = result["language"] or language
            if hub:
                rows = []
                if result.get("can_read_leads") is True:
                    rows.append([button(t("construction_bot_leads_btn", language), "CS_LEADS")])
                if result.get("can_read_clients") is True:
                    rows.append([button(tr("title", language), "CL:open")])
                if result.get("can_read_projects") is True:
                    from ui.construction_project_catalog import tr as project_tr
                    rows.append([button(project_tr("title", language), "PL:open")])
                rows.append([button(t("construction_bot_home_btn", language), "CS_HOME")])
                await show(
                    callback, state, snapshot,
                    tr("hub", language) + "\n\n" + str(result["name"])[:120], rows,
                )
                return
            actor = UUID(result["actor_user_id"])
            if expected_user is not None and actor != expected_user:
                raise ValueError("Canonical client catalog actor changed.")
            items = result["items"]
            identifiers = [str(UUID(item["id"])) for item in items]
            if len(identifiers) > 5 or len(set(identifiers)) != len(identifiers):
                raise ValueError("Invalid client catalog selectors.")
            listing = {
                "nonce": uuid4().hex, "screen": "list",
                "telegram_user_id": callback.from_user.id,
                "tenant_id": str(tenant), "actor_user_id": str(actor),
                "client_ids": identifiers, "cursor": cursor,
                "next_cursor": result["next_cursor"],
            }
            rows = [[button(
                str(item["display_name"])[:90],
                f'CC:catalog:{listing["nonce"]}:{index}',
            )] for index, item in enumerate(items)]
            if listing["next_cursor"] is not None:
                rows.append([button(tr("next", language), "CL:next:" + listing["nonce"])])
            rows.append([button(tr("refresh_list", language), "CL:refresh:" + listing["nonce"])])
            if cursor is not None:
                rows.append([button(tr("first", language), "CL:first:" + listing["nonce"])])
            if result["can_create_client"]:
                rows.append([button(t("construction_client_form_button", language), "CF:new")])
            rows.extend(back_rows(language))
            await show(
                callback, state, snapshot,
                "\n\n".join([
                    tr("title", language), str(result["organization"])[:120],
                    tr("choose" if items else "empty", language),
                ]),
                rows, listing,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_client_catalog_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if snapshot is not None and unchanged(await state.get_data(), snapshot):
                key = (
                    "construction_bot_rate_limited" if getattr(error, "status_code", None) == 429
                    else "construction_bot_unavailable" if getattr(error, "status_code", None)
                    in (401, 403, 404) else "construction_bot_error"
                )
                await show(callback, state, snapshot, t(key, language), back_rows(language))
            else:
                await callback.message.answer(t("construction_lead_form_stale", language))

# project_activity_terminal_v1

# universal_area_editor_v1

# client_link_operator_bot_v1
