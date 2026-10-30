"""Private read-only team screen backed by the common Construction API."""
import logging
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_team import team_text, tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_team")
logger = logging.getLogger(__name__)
VIEW = "construction_team_view"
CONTEXT = "construction_suite_context"


def button(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


async def suspend_forms(state):
    from handlers.construction.construction_lead_create import suspend_lead_form
    from handlers.construction.construction_activity_plan import suspend_activity_form
    from handlers.construction.construction_activity_terminal import suspend_terminal_form
    from handlers.construction.construction_lead_convert import suspend_conversion_form
    from handlers.construction.construction_project_create import suspend_project_form
    from handlers.construction.construction_client_create import suspend_client_form
    for suspend in (
        suspend_lead_form, suspend_activity_form, suspend_terminal_form,
        suspend_conversion_form, suspend_project_form, suspend_client_form,
    ):
        await suspend(state)


async def show(callback, state, text, rows, view=None):
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id} if view else {},
        "last_menu_message_id": sent.message_id,
    })


def back_rows(language):
    return [
        [button(t("construction_bot_home_btn", language), "CS_HOME")],
        [button(t("all_services_suites_btn", language), "M_SUITES")],
    ]


@router.callback_query(F.data.startswith("CT:"))
async def team_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                await callback.message.answer(t("construction_lead_form_stale", language))
                return
            data = await state.get_data()
            context = data.get(CONTEXT, {})
            if (
                not isinstance(context, dict)
                or context.get("telegram_user_id") != callback.from_user.id
                or callback.message.message_id != data.get("last_menu_message_id")
            ):
                await callback.message.answer(t("construction_lead_form_stale", language))
                return
            tenant_id = UUID(context["tenant_id"])
            expected_user_id = None
            if callback.data == "CT:open":
                offset = 0
                await suspend_forms(state)
            else:
                parts = callback.data.split(":")
                view = data.get(VIEW, {})
                if (
                    len(parts) != 4 or parts[1] != "page"
                    or not isinstance(view, dict)
                    or view.get("nonce") != parts[2]
                    or view.get("telegram_user_id") != callback.from_user.id
                    or view.get("tenant_id") != str(tenant_id)
                    or view.get("menu_id") != callback.message.message_id
                ):
                    await callback.message.answer(t("construction_lead_form_stale", language))
                    return
                offset = int(parts[3])
                if (
                    str(offset) != parts[3] or offset < 0
                    or offset not in (
                        0, view["offset"], view["offset"] + 5,
                        max(0, view["offset"] - 5),
                    )
                ):
                    raise ValueError("Invalid team page.")
                expected_user_id = UUID(view["actor_user_id"])

            async with construction_bot_api_entry(
                bot=bot, event_update=event_update,
            ) as service:
                result = await service.team_members(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=tenant_id, expected_user_id=expected_user_id,
                )
            current = (await state.get_data()).get(CONTEXT, {})
            if (
                current.get("telegram_user_id") != callback.from_user.id
                or current.get("tenant_id") != str(tenant_id)
                or (
                    expected_user_id is not None
                    and result["actor_user_id"] != str(expected_user_id)
                )
            ):
                raise ValueError("Team context changed.")
            actor_id = UUID(result["actor_user_id"])
            language = result["language"] or language
            total = len(result["items"])
            offset = min(offset, ((total - 1) // 5) * 5) if total else 0
            view = {
                "nonce": uuid4().hex, "offset": offset,
                "telegram_user_id": callback.from_user.id,
                "tenant_id": str(tenant_id), "actor_user_id": str(actor_id),
            }
            def page(position):
                return "CT:page:" + view["nonce"] + ":" + str(position)
            from ui.construction_team_permissions import member_button
            page_members = result["items"][offset:offset + 5]
            view["user_ids"] = [member["user_id"] for member in page_members]
            rows = [[button(
                member_button(member, offset + index + 1, language),
                "TP:o:" + view["nonce"] + ":" + str(index),
            )] for index, member in enumerate(page_members)]
            if offset:
                rows.append([button(tr("previous", language), page(max(0, offset - 5)))])
            if offset + 5 < total:
                rows.append([button(tr("next", language), page(offset + 5))])
            from ui.construction_invitations import tr as invitation_tr
            rows.append([button(
                invitation_tr("button", language), "IV:org:" + view["nonce"],
            )])
            from handlers.construction.construction_organization_remove import recovery_rows
            rows.extend(recovery_rows(view, data, language))
            from handlers.construction.construction_organization_access import recovery_rows as access_recovery_rows
            rows.extend(access_recovery_rows(view, data, language))
            from handlers.construction.construction_organization_block import recovery_rows as block_recovery_rows
            rows.extend(block_recovery_rows(view, data, language))
            rows.append([button(tr("refresh", language), page(offset))])
            rows.extend(back_rows(language))
            await show(
                callback, state,
                team_text(result, offset=offset, language=language),
                rows, view=view,
            )
        except Exception as error:
            logger.warning(
                "construction_bot_team_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            if isinstance(error, ConstructionApiClientError) and error.status_code == 429:
                key = "construction_bot_rate_limited"
            elif isinstance(error, ConstructionApiClientError) and error.status_code in (401, 403, 404):
                key = "construction_bot_unavailable"
            else:
                key = "construction_bot_error"
            await show(callback, state, t(key, language), back_rows(language))
