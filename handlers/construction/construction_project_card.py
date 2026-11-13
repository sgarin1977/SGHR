"""Project details opened from a current authorized client-card page."""
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_project_card import project_text, tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_project_card")
logger = logging.getLogger(__name__)
VIEW = "construction_project_card_view"
CARD = "construction_client_card_view"
CONTEXT = "construction_suite_context"

def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)

def back_rows(language, card=None):
    rows = []
    if card is not None:
        rows.extend([
            [button(tr("back", language), "CC:projects:" + card["nonce"])],
            [button(
                tr("client", language),
                "CC:summary:" + card["nonce"] if card.get("catalog_nonce")
                else "CC:open:" + UUID(card["client_id"]).hex,
            )],
        ])
    rows.extend([
        [button(t("construction_bot_leads_btn", language), "CS_LEADS")],
        [button(t("construction_bot_home_btn", language), "CS_HOME")],
    ])
    return rows

async def show(callback, state, text, rows, *, view=None, card=None):
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    values = {VIEW: {}, "last_menu_message_id": sent.message_id}
    if view is not None:
        values[VIEW] = {**view, "menu_id": sent.message_id}
    if card is not None:
        current = (await state.get_data()).get(CARD)
        if (
            isinstance(current, dict)
            and current.get("nonce") == card["nonce"]
            and current.get("telegram_user_id") == card["telegram_user_id"]
            and current.get("tenant_id") == card["tenant_id"]
        ):
            # Keep the same client-list positions even when Telegram
            # replaces the menu message rather than editing it.
            values[CARD] = {**current, "menu_id": sent.message_id}
            from handlers.construction.construction_client_catalog import sync_origin
            values.update(sync_origin(await state.get_data(), current, sent.message_id))
        else:
            values[VIEW] = {}
    await state.update_data(**values)

@router.callback_query(F.data.startswith("PC:"))
async def project_callback(callback, state, bot=None, event_update=None):
    language = (callback.from_user.language_code or "ru").lower()
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        card = None
        try:
            if await state.get_state() is not None:
                await callback.message.answer(t("construction_lead_form_stale", language))
                return
            data = await state.get_data()
            context = data.get(CONTEXT, {})
            source = data.get(CARD)
            if (
                not isinstance(context, dict)
                or context.get("telegram_user_id") != callback.from_user.id
            ):
                raise ValueError("Construction context unavailable.")
            tenant_id = UUID(context["tenant_id"])
            if (
                not isinstance(source, dict)
                or source.get("telegram_user_id") != callback.from_user.id
                or source.get("tenant_id") != str(tenant_id)
                or source.get("section") != "projects"
                or source.get("menu_id") != data.get("last_menu_message_id")
                or source.get("menu_id") != callback.message.message_id
            ):
                await callback.message.answer(t("construction_lead_form_stale", language))
                return
            parts = callback.data.split(":")
            if len(parts) == 4 and parts[1] == "open":
                if parts[2] != source["nonce"]:
                    await callback.message.answer(t("construction_lead_form_stale", language))
                    return
                index = int(parts[3])
                ids = source.get("project_ids", [])
                if not 0 <= index < len(ids) or str(index) != parts[3]:
                    raise ValueError("Invalid project selector.")
                project_id = UUID(ids[index])
            elif len(parts) == 3 and parts[1] == "refresh":
                stored = data.get(VIEW)
                if (
                    not isinstance(stored, dict)
                    or stored.get("nonce") != parts[2]
                    or stored.get("source_nonce") != source["nonce"]
                    or stored.get("telegram_user_id") != callback.from_user.id
                    or stored.get("tenant_id") != str(tenant_id)
                    or stored.get("client_id") != source["client_id"]
                    or stored.get("actor_user_id") != source["actor_user_id"]
                    or stored.get("menu_id") != callback.message.message_id
                ):
                    await callback.message.answer(t("construction_lead_form_stale", language))
                    return
                project_id = UUID(stored["project_id"])
                if str(project_id) not in source.get("project_ids", []):
                    raise ValueError("Project selection no longer belongs to this page.")
            else:
                raise ValueError("Invalid project command.")

            card = dict(source)
            client_id = UUID(card["client_id"])
            actor_id = UUID(card["actor_user_id"])
            async with construction_bot_api_entry(
                bot=bot, event_update=event_update,
            ) as service:
                result = await service.project_card(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=tenant_id, project_id=project_id,
                    client_id=client_id, expected_user_id=actor_id,
                )
            current_data = await state.get_data()
            current_context = current_data.get(CONTEXT, {})
            current_card = current_data.get(CARD, {})
            if (
                current_context.get("telegram_user_id") != callback.from_user.id
                or current_context.get("tenant_id") != str(tenant_id)
                or current_card.get("nonce") != card["nonce"]
                or result["actor_user_id"] != str(actor_id)
                or result["item"]["id"] != str(project_id)
                or result["item"]["tenant_id"] != str(tenant_id)
                or result["item"]["client_id"] != str(client_id)
            ):
                raise ValueError("Project context changed.")
            language = result["language"] or language
            view = {
                "nonce": uuid4().hex,
                "source_nonce": card["nonce"],
                "telegram_user_id": callback.from_user.id,
                "tenant_id": str(tenant_id),
                "client_id": str(client_id),
                "project_id": str(project_id),
                "actor_user_id": str(actor_id),
            }
            rows = [[button(
                tr("refresh", language), "PC:refresh:" + view["nonce"],
            )]]
            from ui.construction_project_edit import tr as edit_tr
            if result.get("can_edit_project") is True:
                rows.append([button(edit_tr("edit", language), "PDE:open:" + view["nonce"])])
            from ui.construction_project_activities import tr as planned_tr
            rows.append([button(planned_tr("title", language), "PAV:open:" + view["nonce"])])
            from ui.construction_client_card import tr as activity_tr
            if result.get("can_plan_activity") is True:
                rows.append([button(
                    activity_tr("plan_activity", language), "AP:project:" + view["nonce"],
                )])
            from ui.construction_project_areas import tr as area_tr
            rows.append([button(
                area_tr("title", language), "PA:open:" + view["nonce"],
            )])
            from ui.construction_project_adjustments import tr as adjustment_tr
            if result.get("can_read_pricing_adjustments") is True:
                rows.append([button(adjustment_tr("title", language),
                                    "PJA:open:" + view["nonce"])])
            from ui.construction_project_team import tr as project_team_tr
            if result.get("can_open_team") is True:
                rows.insert(0, [button(
                    project_team_tr("title", language), "PT:open:" + view["nonce"],
                )])
            rows.extend(back_rows(language, card))
            await show(
                callback, state, project_text(result, language), rows,
                view=view, card=card,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_project_card_failed error_type=%s status=%s code=%s",
                type(error).__name__,
                getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if isinstance(error, ConstructionApiClientError) and error.status_code == 429:
                key = "construction_bot_rate_limited"
            elif isinstance(error, ConstructionApiClientError) and error.status_code in (401, 403, 404, 422):
                key = "construction_bot_unavailable"
            else:
                key = "construction_bot_error"
            await show(
                callback, state, t(key, language),
                back_rows(language, card), card=card,
            )
