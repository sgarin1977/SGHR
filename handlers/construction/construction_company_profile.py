"""Private company card using the existing Platform profile GET."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_company_profile import tr, profile_text
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_company_profile")
logger = logging.getLogger(__name__)
VIEW = "construction_company_profile_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, "last_menu_message_id")


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def back_rows(language):
    return [[button(t("construction_bot_home_btn", language), "CS_HOME")]]


async def company_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith(("CP:", "CE:", "CI:", "CBK:", "CLG:", "CST:")):
        saved = (await state.get_data()).get(VIEW)
        if isinstance(saved, dict) and saved.get("telegram_user_id") == event.from_user.id:
            await state.update_data(**{VIEW: {}})
        from handlers.construction.construction_company_edit import suspend_company_form
        await suspend_company_form(state)
    return await handler(event, data)


def unchanged(data, snapshot):
    return all(data.get(key) == snapshot.get(key) for key in KEYS)


async def show(callback, state, snapshot, text, rows, view=None):
    if not unchanged(await state.get_data(), snapshot):
        return
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not unchanged(await state.get_data(), snapshot):
        return
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id} if view else {},
        "last_menu_message_id": sent.message_id,
    })


@router.callback_query(F.data.startswith("CP:"))
async def company_callback(callback, state, bot=None, event_update=None):
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
            expected = None
            if callback.data == "CP:open":
                from handlers.construction.construction_team import suspend_forms
                from handlers.construction.construction_area_create import suspend_area_form
                await suspend_forms(state)
                await suspend_area_form(state)
                from handlers.construction.construction_company_edit import suspend_company_form
                await suspend_company_form(state)
            else:
                parts = callback.data.split(":")
                saved = data.get(VIEW)
                if (
                    len(parts) != 3 or parts[1] != "refresh"
                    or not isinstance(saved, dict) or saved.get("nonce") != parts[2]
                    or saved.get("telegram_user_id") != callback.from_user.id
                    or saved.get("tenant_id") != str(tenant)
                    or saved.get("menu_id") != callback.message.message_id
                ):
                    raise ValueError("Stale company profile selection.")
                expected = UUID(saved["actor_user_id"])
            data = await state.get_data()
            snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                result = await service.company_profile(
                    telegram_user_id=callback.from_user.id, tenant_id=tenant,
                    expected_user_id=expected,
                )
            if not unchanged(await state.get_data(), snapshot):
                raise ValueError("Company profile context changed.")
            actor = UUID(result["actor_user_id"])
            if (
                (expected is not None and actor != expected)
                or (result["item"] is not None
                    and result["item"]["tenant_id"] != str(tenant))
            ):
                raise ValueError("Company profile actor or tenant differs.")
            language = result["language"] or language
            view = {
                "nonce": uuid4().hex, "telegram_user_id": callback.from_user.id,
                "tenant_id": str(tenant), "actor_user_id": str(actor),
            }
            rows = []
            if result.get("can_manage_profile") is True:
                rows.append([button(
                    tr("create" if result["item"] is None else "edit", language),
                    "CE:open:" + view["nonce"],
                )])
            if result["item"] is not None:
                from ui.construction_company_identifiers import tr as identifier_tr
                rows.append([button(
                    identifier_tr("button", language), "CI:open:" + view["nonce"],
                )])
            if result["item"] is not None:
                from ui.construction_company_banks import tr as bank_tr
                rows.append([button(
                    bank_tr("button", language), "CBK:open:" + view["nonce"],
                )])
            from ui.construction_company_logo import tr as logo_tr
            rows.append([button(
                logo_tr("button", language), "CLG:open:" + view["nonce"],
            )])
            from ui.construction_company_settings import tr as settings_tr
            rows.append([button(
                settings_tr("button", language), "CST:open:" + view["nonce"],
            )])
            rows.append([button(tr("refresh", language), "CP:refresh:" + view["nonce"])])
            rows.extend(back_rows(language))
            await show(
                callback, state, snapshot, profile_text(result, language),
                rows, view,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_company_profile_failed error_type=%s status=%s code=%s",
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
