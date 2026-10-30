"""Telegram lead status changes through the approved PATCH API."""
import logging
from uuid import UUID

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import suspend_lead_form
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from ui.construction_lead_status import (
    STATUS_ORDER, available_statuses, status_label, transition_callback,
)
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_lead_status")
logger = logging.getLogger(__name__)
CONTEXT = "construction_suite_context"


def tr(key, language):
    return t("construction_lead_status_" + key, language)


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def back_rows(language, lead_id=None):
    rows = []
    if lead_id is not None:
        rows.append([button(
            t("construction_lead_form_open", language),
            "CS_LEAD:" + lead_id.hex,
        )])
    rows.extend([
        [button(t("construction_bot_leads_btn", language), "CS_LEADS")],
        [button(t("construction_bot_home_btn", language), "CS_HOME")],
    ])
    return rows


async def render(callback, state, text, rows):
    sent = await edit_or_replace_menu_message(
        callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.update_data(last_menu_message_id=sent.message_id)


async def menu(callback, state, result, language, notice=None):
    item = result["item"]
    lead_id = UUID(item["id"])
    version = item["row_version"]
    if type(version) is not int or not 0 < version < 2**63:
        raise ValueError("Invalid lead version.")
    language = result.get("language") or language
    choices = available_statuses(item["status"])
    lines = [
        tr("title", language),
        str(item["name"])[:350],
        tr("current", language).format(
            status=status_label(item["status"], language),
        ),
    ]
    if notice:
        lines.append(notice)
    lines.append(tr("choose" if choices else "terminal", language))
    rows = [
        [button(
            status_label(status, language),
            transition_callback(lead_id, version, status),
        )]
        for status in choices
    ]
    rows.extend(back_rows(language, lead_id))
    await render(callback, state, "\n\n".join(lines), rows)


def log_failure(error):
    logger.warning(
        "construction_bot_lead_status_failed error_type=%s status=%s code=%s",
        type(error).__name__,
        getattr(error, "status_code", None),
        getattr(error, "code", None),
    )


@router.callback_query(F.data.startswith("LS:"))
async def lead_status_callback(callback, state, bot=None, event_update=None):
    language = (callback.from_user.language_code or "ru").lower()
    message = callback.message
    if (
        message is None
        or message.chat.type != "private"
        or message.chat.id != callback.from_user.id
        or callback.from_user.is_bot
    ):
        await callback.answer(
            t("construction_bot_private", language), show_alert=True,
        )
        return
    await callback.answer()

    lead_id = None
    attempted = False
    try:
        data = await state.get_data()
        context = data.get(CONTEXT, {})
        if (
            not isinstance(context, dict)
            or context.get("telegram_user_id") != callback.from_user.id
            or await state.get_state() is not None
        ):
            raise ValueError("Selected organization unavailable.")
        tenant_id = UUID(context["tenant_id"])
        parts = callback.data.split(":")
        if len(parts) == 3 and parts[1] == "open":
            action = "open"
        elif len(parts) == 5 and parts[1] == "set":
            action = "set"
        else:
            raise ValueError("Invalid status callback.")
        lead_id = UUID(parts[2])
        if parts[2] != lead_id.hex:
            raise ValueError("Invalid lead identifier.")

        if action == "set":
            version = int(parts[3], 16)
            index = int(parts[4])
            if (
                not 0 < version < 2**63
                or not 0 <= index < len(STATUS_ORDER)
                or parts[3] != format(version, "x")
                or parts[4] != str(index)
            ):
                raise ValueError("Invalid status command.")
            next_status = STATUS_ORDER[index]

        await suspend_lead_form(state)
        async with construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            if action == "open":
                result = await service.lead(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=tenant_id, lead_id=lead_id,
                )
                await menu(callback, state, result, language)
                return

            attempted = True
            try:
                changed = await service.change_lead_status(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=tenant_id, lead_id=lead_id,
                    next_status=next_status,
                    expected_row_version=version,
                )
            except ConstructionApiClientError as error:
                if error.status_code != 409:
                    raise
                # Refresh only for a new user choice, never resend the PATCH.
                current = await service.lead(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=tenant_id, lead_id=lead_id,
                )
                await menu(
                    callback, state, current, language,
                    notice=tr("conflict", current.get("language") or language),
                )
                return

            language = changed.get("language") or language
            saved = tr("saved", language).format(
                status=status_label(changed["item"]["status"], language),
            )
            try:
                current = await service.lead(
                    telegram_user_id=callback.from_user.id,
                    tenant_id=tenant_id, lead_id=lead_id,
                )
            except Exception as error:
                # The PATCH has a successful receipt even if refresh fails.
                log_failure(error)
                await render(
                    callback, state, saved,
                    back_rows(language, lead_id),
                )
                return
            await menu(callback, state, current, language, notice=saved)

    except ConstructionApiClientError as error:
        log_failure(error)
        if error.status_code == 429:
            text = t("construction_bot_rate_limited", language)
        elif error.status_code in (401, 403, 404, 422):
            text = t("construction_bot_unavailable", language)
        else:
            text = (
                tr("uncertain", language) if attempted
                else t("construction_bot_error", language)
            )
        await render(callback, state, text, back_rows(language, lead_id))
    except Exception as error:
        log_failure(error)
        text = (
            tr("uncertain", language) if attempted
            else t("construction_bot_unavailable", language)
        )
        await render(callback, state, text, back_rows(language, lead_id))
