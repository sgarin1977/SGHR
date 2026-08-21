from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.session import get_session
from handlers.start import normalize_language
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message
from services.specialist_cabinets import SpecialistCabinetsAccessError, SpecialistCabinetsService


def cabinet_menu_keyboard(
    language: str,
    *,
    show_role_switch: bool = False,
    show_moderation: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("spec_public_profile_btn", language),
                callback_data="SPEC_PUBLIC_PROFILE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "professional_cabinets_btn",
                    language,
                ),
                callback_data=(
                    "SPEC_PRO_CABINETS"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                text=t("specialist_dialogs_btn", language),
                callback_data="SPEC_DIALOGS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "spec_categories_directions_btn",
                    language,
                ),
                callback_data="CAB_EDIT_CATEGORY",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("spec_skills_btn", language),
                callback_data="SPEC_SKILLS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("spec_geo_work_btn", language),
                callback_data="CAB_EDIT_LOCATION",
            )
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "specialist_profile_languages_btn",
                    language,
                ),
                callback_data="CAB_EDIT_LANGUAGES",
            )
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "specialist_profile_portfolio_btn",
                    language,
                ),
                callback_data="CAB_PORTFOLIO",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("spec_availability_btn", language),
                callback_data="SPEC_AVAILABILITY",
            )
        ],
    ]

    if show_moderation:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("spec_moderation_btn", language),
                    callback_data="SPEC_MODERATION",
                )
            ]
        )

    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def specialist_cabinet_publication_text(
    status: str | None,
    language: str,
) -> str:
    normalized = status or "draft"

    if normalized == "approved":
        key = "specialist_cabinet_published"
    elif normalized == "pending_moderation":
        key = "specialist_cabinet_pending"
    elif normalized == "rejected":
        key = "specialist_cabinet_rejected"
    elif normalized == "hidden":
        key = "specialist_cabinet_hidden"
    elif normalized == "blocked":
        key = "specialist_cabinet_blocked"
    elif normalized == "deleted":
        key = "specialist_cabinet_deleted"
    else:
        key = "specialist_cabinet_draft"

    return t(key, language)


def format_specialist_cabinet_text(
    *,
    display_name: str,
    status: str | None,
    unread_count: int,
    language: str,
) -> str:
    lines = [
        t("specialist_cabinet_title", language),
        "",
        display_name,
        "",
        specialist_cabinet_publication_text(
            status,
            language,
        ),
    ]

    if unread_count > 0:
        lines.extend(
            [
                "",
                t(
                    "specialist_cabinet_unread",
                    language,
                ).format(count=unread_count),
            ]
        )

    return "\n".join(lines)


async def build_specialist_cabinet_payload(
    telegram_id: int | str,
    fallback_language: str | None,
) -> tuple[
    str,
    str,
    InlineKeyboardMarkup | None,
]:
    language = normalize_language(
        fallback_language
    )

    try:
        async with get_session() as session:
            result = await (
                SpecialistCabinetsService(
                    session
                ).open_cabinet(
                    platform_user_id=telegram_id,
                )
            )
    except SpecialistCabinetsAccessError:
        return (
            language,
            t(
                "billing_start_required",
                language,
            ),
            None,
        )

    language = result.language
    context = result.context

    if not context.user_found:
        return (
            language,
            t(
                "billing_start_required",
                language,
            ),
            None,
        )

    if not context.specialist_found:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "menu_offer_services",
                            language,
                        ),
                        callback_data="SS_START",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_menu",
                            language,
                        ),
                        callback_data="BILL_MENU",
                    )
                ],
            ]
        )

        return (
            language,
            t(
                "specialist_no_profile_start",
                language,
            ),
            keyboard,
        )

    profession_name = (
        ", ".join(context.profession_names)
        or "-"
    )

    text = format_specialist_cabinet_text(
        display_name=profession_name,
        status=context.status,
        unread_count=context.unread_count,
        language=language,
    )

    keyboard = cabinet_menu_keyboard(
        language,
        show_role_switch=(
            context.show_role_switch
        ),
        show_moderation=(
            context.show_moderation
        ),
    )

    return language, text, keyboard


async def show_specialist_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    callback_answered: bool = False,
):
    if not callback_answered:
        await callback.answer()

    language, text, keyboard = (
        await build_specialist_cabinet_payload(
            callback.from_user.id,
            callback.from_user.language_code,
        )
    )

    await state.clear()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=text,
        reply_markup=keyboard,
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )


async def send_specialist_cabinet_message(message: Message, state: FSMContext):
    if not message.from_user:
        return

    _, text, keyboard = await build_specialist_cabinet_payload(
        message.from_user.id,
        message.from_user.language_code,
    )

    await state.clear()
    await message.answer(
        text,
        reply_markup=keyboard,
    )
