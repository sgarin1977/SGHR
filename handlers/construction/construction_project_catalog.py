"""Direct project catalog and cards, using the shared API."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from handlers.construction.construction_project_areas import button, back_rows
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_project_catalog import tr, project_label
from ui.construction_project_card import project_text
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_project_catalog")
logger = logging.getLogger(__name__)
CONTEXT = "construction_suite_context"
LIST = "construction_project_catalog_view"
CARD = "construction_catalog_project_card"
SNAPSHOT_KEYS = (CONTEXT, LIST, CARD, "last_menu_message_id")


def list_binding(data, sender, menu):
    context, view = data.get(CONTEXT), data.get(LIST)
    if (
        not isinstance(context, dict) or not isinstance(view, dict)
        or context.get("telegram_user_id") != sender
        or view.get("telegram_user_id") != sender
        or view.get("tenant_id") != context.get("tenant_id")
        or view.get("menu_id") != menu or data.get("last_menu_message_id") != menu
        or view.get("screen") not in ("list", "card")
        or not isinstance(view.get("project_ids"), list)
        or len(view["project_ids"]) > 5
        or len(set(view["project_ids"])) != len(view["project_ids"])
    ):
        return None
    UUID(view["actor_user_id"])
    UUID(view["tenant_id"])
    for value in view["project_ids"]:
        if str(UUID(value)) != value:
            return None
    return view


def project_binding(data, *, token, sender, menu):
    listing = list_binding(data, sender, menu)
    card = data.get(CARD)
    if (
        listing is None or listing["screen"] != "card"
        or not isinstance(card, dict) or card.get("nonce") != token
        or card.get("source_nonce") != listing.get("nonce")
        or card.get("menu_id") != menu
        or any(card.get(key) != listing.get(key)
               for key in ("telegram_user_id", "tenant_id", "actor_user_id"))
        or card.get("project_id") not in listing["project_ids"]
    ):
        return None
    return {
        "source_key": CARD, "source_token": token,
        "telegram_user_id": sender, "tenant_id": card["tenant_id"],
        "actor_user_id": card["actor_user_id"], "project_id": card["project_id"],
        "client_id": None, "back": "PL:card:" + token,
    }


async def catalog_navigation_middleware(handler, event, data):
    state = data.get("state")
    if (
        state is not None and private(event)
        and not (event.data or "").startswith(("PJA:", "AT:", "PAV:", "PDE:", "PL:", "PA:", "AC:", "AE:", "PT:", "PG:", "PX:", "TP:", "AP:project:"))
    ):
        saved = (await state.get_data()).get(LIST)
        if isinstance(saved, dict) and saved.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{LIST: {}, CARD: {}})
    return await handler(event, data)


def unchanged(data, snapshot):
    return all(data.get(key) == snapshot.get(key) for key in SNAPSHOT_KEYS)


async def show(callback, state, snapshot, text, rows, listing=None, card=None):
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
        CARD: {**card, "menu_id": sent.message_id} if card else {},
        "last_menu_message_id": sent.message_id,
    })


@router.callback_query(F.data.startswith("PL:"))
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
            snapshot = {key: deepcopy(data.get(key)) for key in SNAPSHOT_KEYS}
            expected_user, cursor, project_id, listing = None, None, None, None
            parts = callback.data.split(":")
            if callback.data == "PL:open":
                from handlers.construction.construction_team import suspend_forms
                from handlers.construction.construction_area_create import suspend_area_form
                await suspend_forms(state)
                await suspend_area_form(state)
                await state.update_data(**{
                    "construction_client_card_view": {},
                    "construction_project_card_view": {},
                    "construction_project_areas_view": {},
                })
            else:
                listing = list_binding(data, callback.from_user.id, callback.message.message_id)
                if listing is None:
                    raise ValueError("Catalog selection unavailable.")
                expected_user = UUID(listing["actor_user_id"])
                if len(parts) == 3 and parts[1] in ("card", "back"):
                    selected = project_binding(
                        data, token=parts[2], sender=callback.from_user.id,
                        menu=callback.message.message_id,
                    )
                    if selected is None:
                        raise ValueError("Catalog project selection unavailable.")
                    if parts[1] == "card":
                        project_id = UUID(selected["project_id"])
                    cursor = listing["cursor"]
                else:
                    if (
                        len(parts) not in (3, 4) or parts[2] != listing["nonce"]
                        or listing["screen"] != "list"
                    ):
                        raise ValueError("Stale catalog command.")
                    action = parts[1]
                    if action == "project" and len(parts) == 4:
                        index = int(parts[3])
                        if str(index) != parts[3] or not 0 <= index < len(listing["project_ids"]):
                            raise ValueError("Invalid project selector.")
                        project_id = UUID(listing["project_ids"][index])
                    elif action == "next" and len(parts) == 3:
                        cursor = listing["next_cursor"]
                        if cursor is None:
                            raise ValueError("No next catalog page.")
                    elif action == "refresh" and len(parts) == 3:
                        cursor = listing["cursor"]
                    elif action == "first" and len(parts) == 3:
                        cursor = None
                    else:
                        raise ValueError("Unknown catalog command.")

            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                if project_id is None:
                    result = await service.project_catalog(
                        telegram_user_id=callback.from_user.id, tenant_id=tenant,
                        cursor=cursor, expected_user_id=expected_user,
                    )
                else:
                    result = await service.project_catalog_card(
                        telegram_user_id=callback.from_user.id, tenant_id=tenant,
                        project_id=project_id, expected_user_id=expected_user,
                    )
            if not unchanged(await state.get_data(), snapshot):
                raise ValueError("Catalog context changed during the read.")
            actor = UUID(result["actor_user_id"])
            if expected_user is not None and actor != expected_user:
                raise ValueError("Canonical catalog actor changed.")
            language = result["language"] or language
            if project_id is None:
                items = result["items"]
                identifiers = [str(UUID(item["id"])) for item in items]
                if len(identifiers) > 5 or len(set(identifiers)) != len(identifiers):
                    raise ValueError("Invalid catalog selectors.")
                listing = {
                    "nonce": uuid4().hex, "screen": "list",
                    "telegram_user_id": callback.from_user.id,
                    "tenant_id": str(tenant), "actor_user_id": str(actor),
                    "project_ids": identifiers, "cursor": cursor,
                    "next_cursor": result["next_cursor"],
                }
                rows = [[button(
                    project_label(item, language),
                    f'PL:project:{listing["nonce"]}:{index}',
                )] for index, item in enumerate(items)]
                if listing["next_cursor"] is not None:
                    rows.append([button(tr("next", language), "PL:next:" + listing["nonce"])])
                rows.append([button(tr("refresh_list", language), "PL:refresh:" + listing["nonce"])])
                if cursor is not None:
                    rows.append([button(tr("first", language), "PL:first:" + listing["nonce"])])
                if result["can_create_project"]:
                    rows.append([button(t("construction_project_form_button", language), "PF:new")])
                rows.extend(back_rows(language))
                text = "\n\n".join([
                    tr("title", language), str(result["organization"])[:120],
                    tr("choose" if items else "empty", language),
                ])
                await show(callback, state, snapshot, text, rows, listing=listing)
            else:
                item = result["item"]
                if item["id"] != str(project_id) or item["tenant_id"] != str(tenant):
                    raise ValueError("Catalog card binding differs.")
                listing = {**listing, "screen": "card"}
                card = {
                    "nonce": uuid4().hex, "source_nonce": listing["nonce"],
                    "telegram_user_id": callback.from_user.id,
                    "tenant_id": str(tenant), "actor_user_id": str(actor),
                    "project_id": str(project_id),
                }
                rows = []
                if result.get("can_open_team") is True:
                    from ui.construction_project_team import tr as team_tr
                    rows.append([button(team_tr("title", language), "PT:open:" + card["nonce"])])
                from ui.construction_project_edit import tr as edit_tr
                if result.get("can_edit_project") is True:
                    rows.append([button(edit_tr("edit", language), "PDE:open:" + card["nonce"])])
                from ui.construction_project_activities import tr as planned_tr
                rows.append([button(planned_tr("title", language), "PAV:open:" + card["nonce"])])
                from ui.construction_client_card import tr as activity_tr
                if result.get("can_plan_activity") is True:
                    rows.append([button(
                        activity_tr("plan_activity", language), "AP:project:" + card["nonce"],
                    )])
                from ui.construction_project_adjustments import tr as adjustment_tr
                if result.get("can_read_pricing_adjustments") is True:
                    rows.append([button(adjustment_tr("title", language),
                                        "PJA:open:" + card["nonce"])])
                from ui.construction_project_areas import tr as area_tr
                rows.extend([
                    [button(area_tr("title", language), "PA:open:" + card["nonce"])],
                    [button(tr("refresh", language), "PL:card:" + card["nonce"])],
                    [button(tr("back", language), "PL:back:" + card["nonce"])],
                    *back_rows(language),
                ])
                await show(
                    callback, state, snapshot, project_text(result, language), rows,
                    listing=listing, card=card,
                )
        except Exception as error:
            logger.warning(
                "construction_bot_project_catalog_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if snapshot is not None and unchanged(await state.get_data(), snapshot):
                key = (
                    "construction_bot_rate_limited" if getattr(error, "status_code", None) == 429
                    else "construction_bot_unavailable" if getattr(error, "status_code", None)
                    in (401, 403, 404) else "construction_bot_error"
                )
                await show(
                    callback, state, snapshot, t(key, language),
                    [[button(tr("refresh_list", language), "PL:open")], *back_rows(language)],
                )
            else:
                await callback.message.answer(t("construction_lead_form_stale", language))

# project_activity_terminal_v1

# universal_area_editor_v1
