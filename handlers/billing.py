import logging
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from database.session import get_session
from handlers.start import normalize_language
from handlers.search import SpecialistSearchFSM, complaint_reason_keyboard
from services.specialist import (
    SpecialistActiveCabinetProfile,
    SpecialistRegistrationError,
)
from services.specialist_cabinets import (
    SpecialistCabinetsProfileNotFoundError,
    SpecialistCabinetsService,
    SpecialistCabinetsUserNotFoundError,
)
from services.specialist_profile import (
    SpecialistProfileAccessError,
    SpecialistProfileNotFoundError,
    SpecialistProfileService,
    SpecialistProfileUserNotFoundError,
    SpecialistProfileSelectionError,
    SpecialistProfileProfessionLimitError,
    SpecialistProfileProfessionNotFoundError,
)
from services.specialist_reviews import SpecialistReviewsService
from services.client_cabinet import (
    ClientCabinetNotFoundError,
    ClientCabinetService,
)
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message, delete_telegram_messages, edit_or_replace_tracked_menu_message
from services.geo_service import GeoServiceError
from services.rate_limit import RateLimitError
from handlers.billing_common import (
    replace_billing_input_screen,
)
from handlers.specialist_cabinet_common import (
    show_specialist_cabinet,
)

from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)

billing_router = Router()
logger = logging.getLogger(__name__)
SPECIALIST_REVIEWS_PAGE_SIZE = 5
SPECIALIST_CABINET_EDITOR_PAGE_SIZE = 5



class SpecialistCabinetFSM(StatesGroup):
    entering_display_name = State()
    entering_description = State()
    entering_contact = State()
    choosing_category = State()
    choosing_profession = State()
    entering_location_query = State()
    entering_country_query = State()
    choosing_geo_place = State()
    choosing_country_place = State()
    waiting_geo = State()

async def get_billing_interface_language(
    telegram_id: int | str,
    fallback_language: str | None,
) -> str:
    language = normalize_language(
        fallback_language
    )

    try:
        async with get_session() as session:
            context = await (
                UserSettingsService(
                    session
                ).get_context(
                    platform_user_id=(
                        telegram_id
                    ),
                )
            )
    except UserSettingsNotFoundError:
        return language

    return normalize_language(
        context.interface_language
    )







def client_cabinet_keyboard(
    language: str,
    *,
    show_role_switch: bool = False,
    show_specialist_registration: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("cabinet_my_profile_btn", language),
                callback_data="CAB_USER_PROFILE",
            )
        ],
    ]

    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("cabinet_crm_btn", language),
                    callback_data="CAB_CRM_STUB",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_finance_btn", language),
                    callback_data="CAB_FINANCE_STUB",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("menu_settings", language),
                    callback_data="M_SETTINGS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def specialist_public_profile_preview_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "specialist_card_show_full",
                        language,
                    ),
                    callback_data="SPEC_CARD_FULL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "specialist_card_edit",
                        language,
                    ),
                    callback_data="CAB_PROFILE_EDIT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="M_SPECIALIST",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )

def specialist_profile_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_profile", language),
                    callback_data="CAB_PROFILE_EDIT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("specialist_profile_services_btn", language),
                    callback_data="SPEC_SERVICES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("specialist_profile_portfolio_btn", language),
                    callback_data="CAB_PORTFOLIO",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("specialist_profile_reviews_btn", language),
                    callback_data="SPEC_REVIEWS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("specialist_profile_languages_btn", language),
                    callback_data="CAB_EDIT_LANGUAGES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("specialist_profile_locations_btn", language),
                    callback_data="CAB_EDIT_LOCATION",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("specialist_profile_settings_btn", language),
                    callback_data="SPEC_SETTINGS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="M_CABINET",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )
def profile_visibility_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("spec_contact_visibility_platform_only", language),
                    callback_data="CAB_PROFILE_VISIBILITY_SET:platform_only",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("spec_contact_visibility_public_limited", language),
                    callback_data="CAB_PROFILE_VISIBILITY_SET:public_limited",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("spec_contact_visibility_private", language),
                    callback_data="CAB_PROFILE_VISIBILITY_SET:private",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="CAB_PROFILE",
                )
            ],
        ]
    )

def profile_status_visibility_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "specialist_search_visibility_visible",
                        language,
                    ),
                    callback_data=(
                        "CAB_PROFILE_VISIBILITY_SET:public_limited"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "specialist_search_visibility_hidden",
                        language,
                    ),
                    callback_data="CAB_PROFILE_VISIBILITY_SET:private",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="CAB_PROFILE_VIEW",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )

def profile_visibility_label(value: str | None, language: str) -> str:
    if value == "platform_only":
        return t("spec_contact_visibility_platform_only", language)
    if value == "public_limited":
        return t("spec_contact_visibility_public_limited", language)
    if value == "private":
        return t("spec_contact_visibility_private", language)
    return t("search_filter_not_set", language)

def specialist_edit_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_name", language),
                    callback_data="CAB_EDIT_NAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_description", language),
                    callback_data="CAB_EDIT_DESCRIPTION",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_contacts", language),
                    callback_data="CAB_EDIT_CONTACT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_profession", language),
                    callback_data="CAB_EDIT_PROFESSION",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_work_format", language),
                    callback_data="CAB_EDIT_WORK_FORMAT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_languages", language),
                    callback_data="CAB_EDIT_LANGUAGES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_location", language),
                    callback_data="CAB_EDIT_LOCATION",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="CAB_PROFILE",
                )
            ],
        ]
    )


def profile_edit_back_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="CAB_PROFILE_EDIT",
                )
            ]
        ]
    )

def location_edit_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
                inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("cabinet_location_manual", language),
                    callback_data="CAB_LOC_MANUAL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_location_whole_country", language),
                    callback_data="CAB_LOC_COUNTRY",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_location_geo", language),
                    callback_data="CAB_LOC_GEO",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="CAB_PROFILE_EDIT",
                )
            ],
        ]
    )

def location_and_format_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "cabinet_edit_work_format",
                        language,
                    ),
                    callback_data="CAB_EDIT_WORK_FORMAT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="M_SPECIALIST",
                )
            ],
        ]
    )

def profile_work_format_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_work_at_client", language),
                    callback_data="CAB_WORK_FORMAT_SET:at_client",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_work_at_specialist", language),
                    callback_data="CAB_WORK_FORMAT_SET:at_specialist",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_work_remote", language),
                    callback_data="CAB_WORK_FORMAT_SET:remote",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_work_mixed", language),
                    callback_data="CAB_WORK_FORMAT_SET:mixed",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="CAB_EDIT_LOCATION",
                )
            ],
        ]
    )

def profile_languages_keyboard(
    selected: list[str],
    language: str,
) -> InlineKeyboardMarkup:
    def marker(code: str) -> str:
        return (
            "✓ "
            if code in selected
            else ""
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        f"{marker('ru')}RU"
                    ),
                    callback_data=(
                        "CAB_LANG_TOGGLE:ru"
                    ),
                ),
                InlineKeyboardButton(
                    text=(
                        f"{marker('en')}EN"
                    ),
                    callback_data=(
                        "CAB_LANG_TOGGLE:en"
                    ),
                ),
                InlineKeyboardButton(
                    text=(
                        f"{marker('pt')}PT"
                    ),
                    callback_data=(
                        "CAB_LANG_TOGGLE:pt"
                    ),
                ),
                InlineKeyboardButton(
                    text=(
                        f"{marker('uk')}UA"
                    ),
                    callback_data=(
                        "CAB_LANG_TOGGLE:uk"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        f"{marker('pl')}PL"
                    ),
                    callback_data=(
                        "CAB_LANG_TOGGLE:pl"
                    ),
                ),
                InlineKeyboardButton(
                    text=(
                        f"{marker('de')}DE"
                    ),
                    callback_data=(
                        "CAB_LANG_TOGGLE:de"
                    ),
                ),
                InlineKeyboardButton(
                    text=(
                        f"{marker('nl')}NL"
                    ),
                    callback_data=(
                        "CAB_LANG_TOGGLE:nl"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "spec_profession_done_btn",
                        language,
                    ),
                    callback_data=(
                        "CAB_LANG_DONE"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "billing_back",
                        language,
                    ),
                    callback_data="M_SPECIALIST",
                )
            ],
        ]
    )

def format_profile_languages_text(
    selected: list[str],
    language: str,
) -> str:
    language_names = {
        "ru": t(
            "search_language_ru",
            language,
        ),
        "en": t(
            "search_language_en",
            language,
        ),
        "pt": t(
            "search_language_pt",
            language,
        ),
        "uk": t(
            "search_language_uk",
            language,
        ),
        "pl": t(
            "search_language_pl",
            language,
        ),
        "de": t(
            "search_language_de",
            language,
        ),
        "nl": t(
            "search_language_nl",
            language,
        ),
    }

    selected_text = ", ".join(
        language_names[code]
        for code in selected
        if code in language_names
    )

    lines = [
        t(
            "specialist_languages_title",
            language,
        ),
        t(
            "specialist_languages_hint",
            language,
        ),
    ]

    if selected_text:
        lines.extend(
            [
                "",
                t(
                    "specialist_languages_selected",
                    language,
                ).format(
                    languages=selected_text,
                ),
            ]
        )

    return "\n".join(lines)

def profile_skills_keyboard(
    *,
    skills,
    selected_ids: list[str],
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, skill in enumerate(skills[:30]):
        skill_id = str(skill.id)
        marker = "* " if skill_id in selected_ids else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{marker}{skill.name}"
                    )[:64],
                    callback_data=f"CAB_SKILL_TOGGLE:{index}",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("spec_profession_done_btn", language),
                    callback_data="CAB_SKILLS_DONE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="M_SPECIALIST",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_profile_skills_text(
    skills,
    selected_ids: list[str],
    language: str,
) -> str:
    lines = [
        t("specialist_skills_title", language),
        t("specialist_skills_hint", language),
    ]

    if not skills:
        lines.extend(
            [
                "",
                t("spec_skills_empty", language),
            ]
        )
        return "\n".join(lines)

    selected_names = [
        skill.name
        for skill in skills
        if str(skill.id) in selected_ids
    ]

    if not selected_names:
        return "\n".join(lines)

    lines.extend(
        [
            "",
            t("spec_selected_skills_title", language),
            "\n".join(
                f"✓ {name}"
                for name in selected_names
            ),
        ]
    )

    return "\n".join(lines)

def specialist_availability_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "spec_availability_now_btn",
                        language,
                    ),
                    callback_data=(
                        "SPEC_AVAILABILITY_SET:available"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "spec_availability_busy_btn",
                        language,
                    ),
                    callback_data=(
                        "SPEC_AVAILABILITY_SET:busy"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "spec_availability_vacation_btn",
                        language,
                    ),
                    callback_data=(
                        "SPEC_AVAILABILITY_SET:vacation"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "spec_availability_unavailable_btn",
                        language,
                    ),
                    callback_data=(
                        "SPEC_AVAILABILITY_SET:"
                        "temporarily_unavailable"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "cabinet_specialist_btn",
                        language,
                    ),
                    callback_data="M_SPECIALIST",
                )
            ],
        ]
    )

def format_specialist_availability_text(
    availability_status: str,
    language: str,
) -> str:
    status_keys = {
        "available": "spec_availability_now",
        "busy": "spec_availability_busy",
        "vacation": "spec_availability_vacation",
        "temporarily_unavailable": (
            "spec_availability_unavailable"
        ),
    }

    status_key = status_keys.get(
        availability_status,
        "spec_availability_unavailable",
    )

    return (
        f"{t('spec_availability_title', language)}\n"
        f"{t('spec_availability_hint', language)}\n\n"
        f"{t(status_key, language)}"
    )
def format_specialist_moderation_text(
    status: str | None,
    language: str,
) -> str:
    status = status or "draft"

    if status == "approved":
        status_text = t(
            "spec_moderation_status_approved",
            language,
        )
        hint_text = t(
            "spec_moderation_status_approved_hint",
            language,
        )
    elif status == "pending_moderation":
        status_text = t(
            "spec_moderation_status_pending",
            language,
        )
        hint_text = t(
            "spec_moderation_status_pending_hint",
            language,
        )
    elif status == "rejected":
        status_text = t(
            "spec_moderation_status_rejected",
            language,
        )
        hint_text = t(
            "spec_moderation_status_rejected_hint",
            language,
        )
    elif status == "hidden":
        status_text = t(
            "spec_moderation_status_hidden",
            language,
        )
        hint_text = t(
            "spec_moderation_status_hidden_hint",
            language,
        )
    elif status == "blocked":
        status_text = t(
            "spec_moderation_status_blocked",
            language,
        )
        hint_text = t(
            "spec_moderation_status_blocked_hint",
            language,
        )
    elif status == "deleted":
        status_text = t(
            "spec_moderation_status_deleted",
            language,
        )
        hint_text = t(
            "spec_moderation_status_deleted_hint",
            language,
        )
    else:
        status_text = t(
            "spec_moderation_status_draft",
            language,
        )
        hint_text = t(
            "spec_moderation_status_draft_hint",
            language,
        )

    lines = [
        t("spec_moderation_title", language),
        "",
        f"{t('admin_status', language)}: {status_text}",
        hint_text,
    ]

    return "\n".join(lines)


def specialist_moderation_keyboard(
    status: str | None,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if status in {
        "draft",
        "rejected",
    }:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "professional_cabinet_submit_moderation_btn",
                        language,
                    ),
                    callback_data=(
                        "SPEC_MODERATION_SUBMIT"
                    ),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "cabinet_specialist_btn",
                        language,
                    ),
                    callback_data="M_SPECIALIST",
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

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


@billing_router.callback_query(F.data == "SPEC_AVAILABILITY")
async def show_specialist_availability(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).get_availability(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )

    except SpecialistCabinetsUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return

    except SpecialistCabinetsProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = action.actor.language
    availability_status = action.result

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=(
                format_specialist_availability_text(
                    availability_status,
                    language,
                )
            ),
            reply_markup=(
                specialist_availability_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("SPEC_AVAILABILITY_SET:"))
async def set_specialist_availability(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    availability_status = (
        callback.data or ""
    ).partition(":")[2]

    try:
        async with get_session() as session:
            action = await (
                SpecialistCabinetsService(
                    session
                ).set_availability(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    availability_status=(
                        availability_status
                    ),
                )
            )

    except SpecialistCabinetsUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return

    except SpecialistCabinetsProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = action.actor.language

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "spec_availability_saved",
                language,
            ),
            reply_markup=(
                specialist_availability_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )
    await callback.answer()

def format_geo_candidates_text(candidates: list[dict], language: str) -> str:
    lines = []

    for index, candidate in enumerate(candidates[:8]):
        name = candidate.get("name") or "-"
        country = candidate.get("country_name") or candidate.get("country_code") or "-"
        place_type = candidate.get("place_type") or candidate.get("osm_type") or "place"
        display_name = candidate.get("display_name") or ""

        line = f"{index + 1}. {name}"
        if place_type:
            line += f" ({place_type})"
        if country:
            line += f", {country}"

        if display_name and display_name != name:
            line += f"\n   {display_name[:120]}"

        lines.append(line)

    return "\n\n".join(lines)

def geo_candidates_keyboard(candidates: list[dict], language: str) -> InlineKeyboardMarkup:
    rows = []

    for index, candidate in enumerate(candidates):
        name = candidate.get("name") or candidate.get("display_name") or "-"
        country = candidate.get("country_name") or candidate.get("country_code") or "-"
        place_type = candidate.get("place_type") or candidate.get("osm_type") or "place"

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {name}"[:64],
                    callback_data=f"CAB_GEO_PLACE:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="CAB_PROFILE_EDIT",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def country_candidates_keyboard(candidates: list[dict], language: str) -> InlineKeyboardMarkup:
    rows = []
    seen = set()

    for index, candidate in enumerate(candidates[:8]):
        country_name = candidate.get("country_name") or candidate.get("display_name") or "-"
        country_code = candidate.get("country_code") or ""
        key = (country_name, country_code)

        if key in seen:
            continue

        seen.add(key)

        title = country_name
        if country_code:
            title = f"{country_name} ({country_code})"

        rows.append(
            [
                InlineKeyboardButton(
                    text=title[:64],
                    callback_data=f"CAB_COUNTRY_PLACE:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="CAB_EDIT_LOCATION",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def indexed_items_keyboard(
    items,
    *,
    prefix: str,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, item in enumerate(items):
        label = (
            getattr(item, f"name_{language}", None)
            or getattr(item, "name", None)
            or str(item.id)
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{prefix}:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="CAB_PROFILE_EDIT",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def cabinet_selected_professions_text(
    selected_professions: list[dict],
    language: str,
) -> str:
    if not selected_professions:
        return t("spec_selected_professions_empty", language)

    rows = []
    for item in selected_professions:
        category_name = item.get("category_name") or "-"
        profession_name = item.get("profession_name") or "-"
        rows.append(f"- {category_name}: {profession_name}")

    return "\n".join(rows)

def cabinet_profession_prompt_text(
    selected_professions: list[dict],
    language: str,
) -> str:
    return (
        f"{t('specialist_professions_title', language)}\n"
        f"{t('specialist_professions_hint', language)}\n\n"
        f"{t('cabinet_choose_profession', language)}\n\n"
        f"{t('spec_selected_professions_title', language)}\n"
        f"{cabinet_selected_professions_text(selected_professions, language)}"
    )


def cabinet_profession_multi_keyboard(
    *,
    items,
    selected_ids: list[str],
    language: str,
    page: int = 0,
) -> InlineKeyboardMarkup:
    page = max(0, page)
    start = page * SPECIALIST_CABINET_EDITOR_PAGE_SIZE
    end = start + SPECIALIST_CABINET_EDITOR_PAGE_SIZE
    page_items = items[start:end]
    selected_set = set(selected_ids)
    rows: list[list[InlineKeyboardButton]] = []

    for index, item in enumerate(page_items, start=start):
        marker = "✓ " if str(item.id) in selected_set else ""

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{localized_name(item, language)}",
                    callback_data=f"CAB_PROF:{index}",
                )
            ]
        )

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"CAB_PROF_PAGE:{page - 1}",
            )
        )

    if end < len(items):
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"CAB_PROF_PAGE:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("spec_profession_done_btn", language),
                callback_data="CAB_PROF_DONE",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="CAB_PROF_BACK_CATEGORIES",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def localized_name(item, language: str) -> str:
    if not item:
        return "-"

    return (
        getattr(item, f"name_{language}", None)
        or getattr(item, "name", None)
        or "-"
    )

def format_specialist_profile_text(
    profile: SpecialistActiveCabinetProfile | None,
    language: str,
) -> str:
    if not profile:
        return t(
            "cabinet_profile_not_found",
            language,
        )

    if profile.reviews_count > 0:
        rating_text = t(
            "specialist_card_rating_value",
            language,
        ).format(
            rating=f"{profile.rating:.1f}",
            count=profile.reviews_count,
        )
    else:
        rating_text = t(
            "specialist_card_rating_new",
            language,
        )

    availability_keys = {
        "available": "spec_availability_now",
        "busy": "spec_availability_busy",
        "vacation": "spec_availability_vacation",
        "temporarily_unavailable": (
            "spec_availability_unavailable"
        ),
    }
    availability_key = availability_keys.get(
        profile.availability_status,
        "spec_availability_unavailable",
    )

    lines = [
        t(
            "specialist_card_title",
            language,
        ),
        "",
        f"👤 {profile.display_name}",
    ]

    if (
        profile.profession_name
        and profile.profession_name != "-"
    ):
        lines.append(
            t(
                "specialist_card_profession",
                language,
            ).format(
                profession=(
                    profile.profession_name
                )
            )
        )

    lines.append(rating_text)

    if profile.location != "-":
        lines.append(
            f"📍 {profile.location}"
        )

    lines.append(
        t(
            availability_key,
            language,
        )
    )

    description = (
        profile.description or ""
    ).strip()
    if description:
        lines.extend(
            [
                "",
                description[:500],
            ]
        )

    return "\n".join(lines)

def specialist_profile_status_block(
    status: str | None,
    language: str,
) -> str:
    normalized = status or "draft"

    status_keys = {
        "approved": (
            "spec_moderation_status_approved",
            "spec_moderation_status_approved_hint",
        ),
        "pending_moderation": (
            "spec_moderation_status_pending",
            "spec_moderation_status_pending_hint",
        ),
        "rejected": (
            "spec_moderation_status_rejected",
            "spec_moderation_status_rejected_hint",
        ),
        "hidden": (
            "spec_moderation_status_hidden",
            "spec_moderation_status_hidden_hint",
        ),
        "blocked": (
            "spec_moderation_status_blocked",
            "spec_moderation_status_blocked_hint",
        ),
        "deleted": (
            "spec_moderation_status_deleted",
            "spec_moderation_status_deleted_hint",
        ),
        "draft": (
            "spec_moderation_status_draft",
            "spec_moderation_status_draft_hint",
        ),
    }

    status_key, hint_key = status_keys.get(
        normalized,
        status_keys["draft"],
    )

    return (
        f"{t('specialist_profile_status_title', language)}\n\n"
        f"{t(status_key, language)}\n"
        f"{t(hint_key, language)}"
    )

def specialist_profile_status_label(
    status: str | None,
    language: str,
) -> str:
    normalized = status or "draft"

    labels = {
        "approved": "spec_moderation_status_approved",
        "pending_moderation": "spec_moderation_status_pending",
        "rejected": "spec_moderation_status_rejected",
        "hidden": "spec_moderation_status_hidden",
        "blocked": "spec_moderation_status_blocked",
        "deleted": "spec_moderation_status_deleted",
        "draft": "spec_moderation_status_draft",
    }

    return t(
        labels.get(
            normalized,
            "spec_moderation_status_draft",
        ),
        language,
    )








def specialist_status_notice(
    status: str | None,
    language: str = "ru",
) -> str:
    return specialist_profile_status_block(
        status,
        language,
    )

def specialist_visibility_notice(visibility: str | None, language: str = "ru") -> str:
    if visibility == "private":
        value = t("specialist_search_visibility_hidden", language)
    else:
        value = t("specialist_search_visibility_visible", language)

    return (
        f"{t('specialist_search_visibility_title', language)}\n\n"
        f"{value}"
    )

def specialist_profile_publication_notice(
    *,
    status: str | None,
    visibility: str | None,
    language: str = "ru",
) -> str:
    return (
        f"{t('specialist_profile_publication_title', language)}\n\n"
        f"{specialist_status_notice(status, language)}\n\n"
        f"{specialist_visibility_notice(visibility, language)}"
    )





@billing_router.callback_query(
    F.data.startswith("SPEC_REQUEST")
)
async def block_legacy_specialist_request_callbacks(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer(
        t("legacy_requests_unavailable", language),
        show_alert=True,
    )

def format_specialist_reviews_cabinet(review_page, language: str) -> str:
    if review_page.reputation and review_page.reputation.review_count:
        rating = f"{float(review_page.reputation.score or 0):.1f}"
        count = review_page.reputation.review_count
    else:
        rating = t("search_no_reviews", language)
        count = 0

    lines = [
        t("public_reviews_title", language),
        t("public_reviews_summary", language).format(
            rating=rating,
            count=count,
        ),
        "",
    ]

    if not review_page.reviews:
        lines.append(t("public_reviews_empty", language))
        return "\n".join(lines)

    start_number = review_page.page * review_page.page_size + 1

    for number, review in enumerate(review_page.reviews, start=start_number):
        text = (review.text or "").strip() or t("public_review_without_text", language)
        lines.append(
            t("public_review_item", language).format(
                number=number,
                rating=review.rating,
                text=text,
            )
        )

        if review.specialist_reply:
            lines.append(
                t("public_review_specialist_reply", language).format(
                    reply=review.specialist_reply,
                )
            )

        lines.append("")

    return "\n".join(lines).strip()


def specialist_reviews_keyboard(
    *,
    language: str,
    page: int,
    has_previous: bool,
    has_next: bool,
    reviews_count: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    nav_row: list[InlineKeyboardButton] = []
    if has_previous:
        nav_row.append(
            InlineKeyboardButton(
                text=t("prev_btn", language),
                callback_data=f"SPEC_REVIEWS_PAGE:{page - 1}",
            )
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(
                text=t("next_btn", language),
                callback_data=f"SPEC_REVIEWS_PAGE:{page + 1}",
            )
        )
    if nav_row:
        rows.append(nav_row)

    for index in range(reviews_count):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("public_review_report_btn", language).format(
                        number=index + 1,
                    ),
                    callback_data=f"SPEC_REVIEW_REPORT:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="M_CABINET",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_specialist_reviews_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    page: int = 0,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            action = await (
                SpecialistReviewsService(
                    session
                ).list_reviews(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    page=page,
                    page_size=(
                        SPECIALIST_REVIEWS_PAGE_SIZE
                    ),
                )
            )

    except SpecialistCabinetsUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return

    except SpecialistCabinetsProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = action.actor.language
    review_page = action.result

    await state.update_data(
        specialist_review_ids=[
            str(review.id)
            for review in review_page.reviews
        ],
        specialist_reviews_page=(
            review_page.page
        ),
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=(
                format_specialist_reviews_cabinet(
                    review_page,
                    language,
                )
            ),
            reply_markup=(
                specialist_reviews_keyboard(
                    language=language,
                    page=review_page.page,
                    has_previous=(
                        review_page.has_previous
                    ),
                    has_next=(
                        review_page.has_next
                    ),
                    reviews_count=len(
                        review_page.reviews
                    ),
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@billing_router.callback_query(F.data == "SPEC_REVIEWS")
async def specialist_reviews_entry(callback: CallbackQuery, state: FSMContext):
    await render_specialist_reviews_cabinet(callback, state, page=0)


@billing_router.callback_query(F.data.startswith("SPEC_REVIEWS_PAGE:"))
async def paginate_specialist_reviews(callback: CallbackQuery, state: FSMContext):
    try:
        page = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer()
        return

    await render_specialist_reviews_cabinet(callback, state, page=page)


@billing_router.callback_query(F.data.startswith("SPEC_REVIEW_REPORT:"))
async def report_specialist_review(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer()
        return

    data = await state.get_data()
    review_ids = data.get("specialist_review_ids") or []

    if index < 0 or index >= len(review_ids):
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    await state.update_data(
        pending_report_target_type="review",
        pending_report_target_id=review_ids[index],
        selected_specialist_id=review_ids[index],
        user_language=language,
    )
    await state.set_state(SpecialistSearchFSM.viewing_results)

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "complaint_reason_prompt",
            language,
        ),
        reply_markup=complaint_reason_keyboard(
            language
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

def specialist_settings_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("settings_language_btn", language),
                    callback_data="SPEC_SETTINGS_LANGUAGE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings_notifications_btn", language),
                    callback_data="SPEC_SETTINGS_NOTIFICATIONS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_profile_status_visibility", language),
                    callback_data="CAB_PROFILE_VISIBILITY",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings_consents_btn", language),
                    callback_data="SPEC_SETTINGS_CONSENTS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="M_CABINET",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )


@billing_router.callback_query(
    F.data == "SPEC_SETTINGS"
)
async def specialist_settings_entry(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "specialist_settings_title",
            language,
        ),
        reply_markup=(
            specialist_settings_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

def specialist_language_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "settings_interface_language_label",
                        language,
                    ),
                    callback_data=(
                        "SPEC_INTERFACE_LANGUAGE"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "settings_translation_mode_label",
                        language,
                    ),
                    callback_data=(
                        "SPEC_TRANSLATION_SETTINGS"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "billing_back",
                        language,
                    ),
                    callback_data="SPEC_SETTINGS",
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





async def render_specialist_language_menu(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = (
        await get_billing_interface_language(
            callback.from_user.id,
            callback.from_user.language_code,
        )
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "settings_language_menu_title",
                language,
            ),
            reply_markup=(
                specialist_language_menu_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(
    F.data == "SPEC_SETTINGS_LANGUAGE"
)
async def specialist_settings_language(
    callback: CallbackQuery,
    state: FSMContext,
):
    await render_specialist_language_menu(
        callback,
        state,
    )

@billing_router.callback_query(
    F.data == "SPEC_SETTINGS_NOTIFICATIONS"
)
async def specialist_settings_notifications(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "specialist_notifications_settings",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "billing_back",
                            language,
                        ),
                        callback_data="SPEC_SETTINGS",
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
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )




def cabinet_role_label(
    role: str | None,
    language: str,
) -> str:
    if not role:
        return t("role_text_other", language)

    key = f"role_text_{role}"
    label = t(key, language)

    if label == key:
        return t("role_text_other", language)

    return label

def format_client_user_profile(profile, language: str) -> str:
    lines = [t("cabinet_user_profile_title", language), ""]

    if profile.name:
        lines.append(f"{t('cabinet_user_profile_name', language)}: {profile.name}")

    if profile.username:
        lines.append(f"Telegram: @{profile.username}")

    lines.append(f"{t('cabinet_user_profile_number', language)}: {profile.user_number}")
    lines.append(f"{t('cabinet_user_profile_language', language)}: {profile.language_code}")

    if profile.city_name:
        lines.append(f"{t('cabinet_user_profile_city', language)}: {profile.city_name}")

    if profile.active_role:
        lines.append(
            f"{t('cabinet_user_profile_active_role', language)}: "
            f"{cabinet_role_label(profile.active_role, language)}"
        )

    if profile.available_roles:
        cabinet_labels = [
            cabinet_role_label(role, language)
            for role in profile.available_roles
        ]
        lines.append(
            f"{t('cabinet_user_profile_roles', language)}: "
            f"{', '.join(cabinet_labels)}"
        )

    return "\n".join(lines)

@billing_router.callback_query(
    F.data == "CAB_USER_PROFILE"
)
async def show_client_user_profile(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            action = await (
                ClientCabinetService(
                    session
                ).get_profile(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )

    except ClientCabinetNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return

    language = action.language
    profile = action.result

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=format_client_user_profile(
                profile,
                language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "back",
                                language,
                            ),
                            callback_data=(
                                "M_CABINET"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "search_menu",
                                language,
                            ),
                            callback_data=(
                                "BILL_MENU"
                            ),
                        )
                    ],
                ]
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@billing_router.callback_query(
    (F.data == "CAB_ORDERS")
    | F.data.startswith("CLIENT_ORDER")
)
async def block_legacy_client_order_callbacks(
    callback: CallbackQuery,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer(
        t(
            "order_actions_unavailable",
            language,
        ),
        show_alert=True,
    )

async def show_client_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    callback_answered: bool = False,
):
    if not callback_answered:
        await callback.answer()

    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            action = await (
                ClientCabinetService(
                    session
                ).open_cabinet(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )

    except ClientCabinetNotFoundError:
        text = t(
            "billing_start_required",
            language,
        )
        keyboard = None

    else:
        language = action.language
        cabinet_context = action.result

        text = (
            t(
                "client_cabinet_title",
                language,
            )
            + "\n\n"
            + t(
                "client_cabinet_summary",
                language,
            )
        )
        keyboard = client_cabinet_keyboard(
            language,
            show_role_switch=(
                cabinet_context.show_role_switch
            ),
            show_specialist_registration=(
                cabinet_context
                .show_specialist_registration
            ),
        )

    await state.clear()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=text,
            reply_markup=keyboard,
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@billing_router.callback_query(
    F.data.in_(
        {
            "CAB_CRM_STUB",
            "CAB_FINANCE_STUB",
        }
    )
)
async def show_cabinet_stub(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    text_key = (
        "cabinet_crm_stub"
        if callback.data == "CAB_CRM_STUB"
        else "cabinet_finance_stub"
    )

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(text_key, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "menu_my_cabinet",
                            language,
                        ),
                        callback_data="M_CABINET",
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
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )


@billing_router.callback_query(
    F.data == "SPEC_MODERATION"
)
async def show_specialist_moderation(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    callback_answered: bool = False,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).get_moderation(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileAccessError:
        await callback.answer(
            t(
                "specialist_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    cabinet = profile_action.result

    if not cabinet:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    if not callback_answered:
        await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=format_specialist_moderation_text(
            cabinet.moderation_status,
            language,
        ),
        reply_markup=specialist_moderation_keyboard(
            cabinet.moderation_status,
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )

@billing_router.callback_query(
    F.data == "SPEC_MODERATION_SUBMIT"
)
async def submit_specialist_cabinet_for_moderation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).submit_moderation(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileAccessError:
        await callback.answer(
            t(
                "specialist_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistRegistrationError:
        await callback.answer(
            t(
                "professional_cabinet_submit_failed",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    changed = profile_action.result

    result_key = (
        "professional_cabinet_submitted"
        if changed
        else "professional_cabinet_already_pending"
    )

    await callback.answer(
        t(
            result_key,
            language,
        )
    )

    await show_specialist_moderation(
        callback,
        state,
        callback_answered=True,
    )




@billing_router.callback_query(
    F.data.in_(
        {
            "CAB_PROFILE",
            "SPEC_PUBLIC_PROFILE",
        }
    )
)
async def show_specialist_profile_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).get_active_profile(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    profile = profile_action.result

    if not profile:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=format_specialist_profile_text(
            profile,
            language,
        ),
        reply_markup=(
            specialist_public_profile_preview_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(
    F.data == "SPEC_CARD_FULL"
)
async def show_specialist_card_full_description(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).get_active_profile(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    profile = profile_action.result

    if not profile:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    description = (
        profile.description or ""
    ).strip()

    if description:
        text = (
            f"{t('specialist_card_full_title', language)}"
            f"\n\n{description[:3800]}"
        )
    else:
        text = t(
            "specialist_card_full_empty",
            language,
        )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=text,
        reply_markup=(
            specialist_public_profile_preview_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(
    F.data == "CAB_PROFILE_VIEW"
)
async def view_specialist_profile(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).get_active_profile(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    profile = profile_action.result

    if not profile:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=format_specialist_profile_text(
            profile,
            language,
        ),
        reply_markup=(
            specialist_profile_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(F.data == "CAB_PROFILE_PAUSE")
async def block_legacy_specialist_profile_pause(
    callback: CallbackQuery,
) -> None:
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer(
        t(
            "specialist_profile_status_managed_by_moderation",
            language,
        ),
        show_alert=True,
    )


@billing_router.callback_query(
    F.data == "CAB_PROFILE_VISIBILITY"
)
async def show_specialist_profile_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).get_visibility(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    current_visibility = (
        profile_action.result.visibility
    )
    moderation_status = (
        profile_action.result
        .moderation_status
    )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=specialist_profile_publication_notice(
            status=moderation_status,
            visibility=current_visibility,
            language=language,
        ),
        reply_markup=(
            profile_status_visibility_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@billing_router.callback_query(F.data.startswith("CAB_PROFILE_VISIBILITY_SET:"))
async def set_specialist_profile_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        visibility = (
            callback.data or ""
        ).split(
            ":",
            1,
        )[1]
    except IndexError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).set_visibility(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    visibility=visibility,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        SpecialistProfileAccessError,
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    visibility = (
        profile_action.result.visibility
    )
    moderation_status = (
        profile_action.result
        .moderation_status
    )

    await callback.answer(
        t(
            "cabinet_visibility_updated",
            language,
        ).format(
            visibility=profile_visibility_label(
                visibility,
                language,
            ),
        )
    )

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=specialist_profile_publication_notice(
            status=moderation_status,
            visibility=visibility,
            language=language,
        ),
        reply_markup=(
            profile_status_visibility_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(
    F.data.startswith("CAB_PROFILE_STATUS_SET:")
)
async def block_legacy_specialist_profile_status_change(
    callback: CallbackQuery,
) -> None:
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer(
        t(
            "specialist_profile_status_managed_by_moderation",
            language,
        ),
        show_alert=True,
    )
@billing_router.callback_query(
    F.data == "CAB_PROFILE_DELETE"
)
async def confirm_specialist_profile_delete(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_actor = await (
                SpecialistProfileService(
                    session
                ).require_actor(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_actor.language
    )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "privacy_confirm_delete_profile",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "privacy_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "CAB_PROFILE_DELETE_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "privacy_cancel_btn",
                            language,
                        ),
                        callback_data="SPEC_SETTINGS",
                    )
                ],
            ]
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )



@billing_router.callback_query(
    F.data == "CAB_PROFILE_EDIT"
)
async def edit_specialist_profile_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_actor = await (
                SpecialistProfileService(
                    session
                ).require_actor(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_actor.language
    )


    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "cabinet_edit_profile",
            language,
        ),
        reply_markup=(
            specialist_edit_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )


@billing_router.callback_query(F.data == "CAB_EDIT_NAME")
async def ask_edit_specialist_name(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "cabinet_enter_name",
            language,
        ),
        reply_markup=(
            profile_edit_back_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )
    await state.set_state(
        SpecialistCabinetFSM.entering_display_name
    )

@billing_router.callback_query(F.data == "CAB_EDIT_DESCRIPTION")
async def ask_edit_specialist_description(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "cabinet_enter_description",
            language,
        ),
        reply_markup=(
            profile_edit_back_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )
    await state.set_state(
        SpecialistCabinetFSM.entering_description
    )


@billing_router.callback_query(F.data == "CAB_EDIT_CONTACT")
async def ask_edit_specialist_contact(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "cabinet_enter_contact",
            language,
        ),
        reply_markup=(
            profile_edit_back_keyboard(
                language
            )
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )
    await state.set_state(
        SpecialistCabinetFSM.entering_contact
    )
@billing_router.callback_query(
    F.data == "CAB_EDIT_WORK_FORMAT"
)
async def ask_edit_specialist_work_format(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).open_work_format(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "spec_work_format_prompt",
                language,
            ),
            reply_markup=(
                profile_work_format_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )



@billing_router.callback_query(F.data.startswith("CAB_WORK_FORMAT_SET:"))
async def set_edit_specialist_work_format(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    work_format = (
        (callback.data or "")
        .partition(":")[2]
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).save_work_format(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    work_format=work_format,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    _, _, _, changed = (
        profile_action.result
    )
    text_key = (
        "cabinet_profile_updated"
        if changed
        else "cabinet_profile_no_changes"
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                text_key,
                language,
            ),
            reply_markup=(
                location_and_format_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(F.data == "CAB_EDIT_LANGUAGES")
async def ask_edit_specialist_languages(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).get_languages(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistRegistrationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    actor = profile_action.actor
    selected = profile_action.result
    language = normalize_language(
        actor.language
    )

    await state.update_data(
        cabinet_selected_languages=selected,
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=format_profile_languages_text(
                selected,
                language,
            ),
            reply_markup=(
                profile_languages_keyboard(
                    selected,
                    language,
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@billing_router.callback_query(F.data.startswith("CAB_LANG_TOGGLE:"))
async def toggle_specialist_language(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    code = (
        (callback.data or "")
        .partition(":")[2]
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).toggle_language(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    selected_codes=list(
                        data.get(
                            "cabinet_selected_languages"
                        )
                        or ["ru"]
                    ),
                    language_code=code,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except ValueError:
        await callback.answer()
        return
    except SpecialistRegistrationError:
        await callback.answer(
            t(
                "spec_profession_select_one",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    selected = profile_action.result

    await state.update_data(
        cabinet_selected_languages=selected
    )

    await callback.message.edit_text(
        format_profile_languages_text(
            selected,
            language,
        ),
        reply_markup=(
            profile_languages_keyboard(
                selected,
                language,
            )
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_LANG_DONE")
async def save_specialist_languages(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    selected = list(
        data.get(
            "cabinet_selected_languages"
        )
        or []
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).save_languages(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    language_codes=selected,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistRegistrationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    _, _, changed = profile_action.result
    text_key = (
        "cabinet_profile_updated"
        if changed
        else "cabinet_profile_no_changes"
    )

    await state.set_state(None)
    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                text_key,
                language,
            ),
            reply_markup=(
                specialist_edit_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(F.data == "SPEC_SKILLS")
async def show_specialist_skills(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).get_skills(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    limit=30,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistRegistrationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    actor = profile_action.actor
    edit_data = profile_action.result
    language = normalize_language(
        actor.language
    )
    skills = list(edit_data.skills)
    selected_ids = [
        str(item)
        for item in edit_data.selected_ids
    ]

    await state.update_data(
        cabinet_skill_ids=[
            str(skill.id)
            for skill in skills
        ],
        cabinet_selected_skill_ids=(
            selected_ids
        ),
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=format_profile_skills_text(
                skills,
                selected_ids,
                language,
            ),
            reply_markup=(
                profile_skills_keyboard(
                    skills=skills,
                    selected_ids=(
                        selected_ids
                    ),
                    language=language,
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@billing_router.callback_query(F.data.startswith("CAB_SKILL_TOGGLE:"))
async def toggle_specialist_skill(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).get_skills(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    limit=30,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistRegistrationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    edit_data = profile_action.result
    skills = list(edit_data.skills)
    skill_ids = [
        str(skill.id)
        for skill in skills
    ]

    if index < 0 or index >= len(skill_ids):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    data = await state.get_data()
    valid_skill_ids = set(skill_ids)
    selected_ids = [
        item
        for item in (
            data.get(
                "cabinet_selected_skill_ids"
            )
            or []
        )
        if item in valid_skill_ids
    ]
    skill_id = skill_ids[index]

    if skill_id in selected_ids:
        selected_ids = [
            item
            for item in selected_ids
            if item != skill_id
        ]
    else:
        selected_ids.append(skill_id)

    await state.update_data(
        cabinet_skill_ids=skill_ids,
        cabinet_selected_skill_ids=(
            selected_ids
        ),
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=format_profile_skills_text(
                skills,
                selected_ids,
                language,
            ),
            reply_markup=(
                profile_skills_keyboard(
                    skills=skills,
                    selected_ids=(
                        selected_ids
                    ),
                    language=language,
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@billing_router.callback_query(F.data == "CAB_SKILLS_DONE")
async def save_specialist_skills(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    selected_ids = list(
        data.get(
            "cabinet_selected_skill_ids"
        )
        or []
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).save_skills(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    skill_ids=selected_ids,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        SpecialistProfileSelectionError,
        SpecialistRegistrationError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )

    await state.set_state(None)
    await callback.answer(
        t(
            "spec_skills_saved",
            language,
        )
    )

    await show_specialist_cabinet(
        callback,
        state,
        callback_answered=True,
    )

async def block_critical_profile_edit(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    field: str,
    language: str,
) -> None:
    try:
        async with get_session() as session:
            await (
                SpecialistProfileService(
                    session
                ).record_blocked_change(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    field=field,
                )
            )
    except SpecialistProfileAccessError:
        pass

    await state.clear()
    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "cabinet_critical_edit_blocked",
                language,
            ),
            reply_markup=(
                specialist_edit_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


async def block_critical_profile_edit_message(
    message: Message,
    state: FSMContext,
    *,
    field: str,
    language: str,
) -> None:
    try:
        async with get_session() as session:
            await (
                SpecialistProfileService(
                    session
                ).record_blocked_change(
                    platform_user_id=(
                        message.from_user.id
                    ),
                    field=field,
                    source=(
                        "stale_fsm_state"
                    ),
                )
            )
    except SpecialistProfileAccessError:
        pass

    data = await state.get_data()

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            message.message_id
        ],
    )

    menu_message_id = (
        await (
            edit_or_replace_tracked_menu_message(
                message=message,
                menu_message_id=data.get(
                    "last_menu_message_id"
                ),
                text=t(
                    "cabinet_critical_edit_blocked",
                    language,
                ),
                reply_markup=(
                    specialist_edit_keyboard(
                        language
                    )
                ),
            )
        )
    )

    await state.clear()
    await state.update_data(
        last_menu_message_id=(
            menu_message_id
        ),
    )

@billing_router.callback_query(
    F.data == "CAB_EDIT_LOCATION"
)
async def show_location_and_format(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=(
            f"{t('specialist_location_work_title', language)}\n"
            f"{t('specialist_location_work_hint', language)}"
        ),
        reply_markup=location_and_format_keyboard(
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id
    )


@billing_router.callback_query(
    F.data == "CAB_LOCATION_EDIT"
)
async def explain_specialist_location_unavailable(
    callback: CallbackQuery,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer(
        t(
            "specialist_location_work_hint",
            language,
        ),
        show_alert=True,
    )

@billing_router.callback_query(
    F.data.in_(
        {
            "CAB_LOC_MANUAL",
            "CAB_LOC_COUNTRY",
            "CAB_LOC_GEO",
            "CAB_PROF_DONE",
        }
    )
    | F.data.startswith("CAB_GEO_PLACE:")
    | F.data.startswith("CAB_GEO_COUNTRY:")
    | F.data.startswith("CAB_COUNTRY_PLACE:")
    | F.data.startswith("CAB_CAT:")
    | F.data.startswith("CAB_PROF:"),
    StateFilter(None),
)
async def block_stale_critical_profile_edit_callbacks(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    await block_critical_profile_edit(
        callback,
        state,
        field="critical_profile_field",
        language=language,
    )

@billing_router.callback_query(
    F.data == "CAB_LOC_MANUAL"
)
async def ask_edit_specialist_location_manual(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "cabinet_location_query_prompt",
            language,
        ),
        reply_markup=profile_edit_back_keyboard(
            language
        ),
    )

    await state.set_state(
        SpecialistCabinetFSM.entering_location_query
    )
    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

@billing_router.callback_query(
    F.data == "CAB_LOC_COUNTRY"
)
async def ask_edit_specialist_location_country(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "spec_country_search_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "billing_back",
                            language,
                        ),
                        callback_data=(
                            "CAB_EDIT_LOCATION"
                        ),
                    )
                ]
            ]
        ),
    )

    await state.set_state(
        SpecialistCabinetFSM.entering_country_query
    )
    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@billing_router.callback_query(
    F.data == "CAB_LOC_GEO"
)
async def ask_edit_specialist_location_geo(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            callback.message.message_id
        ],
    )

    menu_message = await callback.message.answer(
        t(
            "cabinet_geo_required",
            language,
        ),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=t(
                            "cabinet_send_geo_btn",
                            language,
                        ),
                        request_location=True,
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )

    await state.set_state(
        SpecialistCabinetFSM.waiting_geo
    )
    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

@billing_router.message(SpecialistCabinetFSM.entering_location_query)
async def receive_specialist_location_query(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    await block_critical_profile_edit_message(
        message,
        state,
        field="location",
        language=language,
    )

@billing_router.message(SpecialistCabinetFSM.entering_country_query)
async def receive_specialist_country_query(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    await block_critical_profile_edit_message(
        message,
        state,
        field="location",
        language=language,
    )

@billing_router.message(SpecialistCabinetFSM.waiting_geo)
async def receive_specialist_location_geo(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    await block_critical_profile_edit_message(
        message,
        state,
        field="location",
        language=language,
    )
@billing_router.callback_query(F.data.startswith("CAB_GEO_PLACE:"))
async def choose_specialist_location_update(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    candidates = (
        data.get("cabinet_geo_candidates")
        or []
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
        if index < 0:
            raise IndexError
        candidate = candidates[index]
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).save_location_candidate(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    candidate=candidate,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except RateLimitError:
        await callback.answer(
            t(
                "error_rate_limited",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        GeoServiceError,
        SpecialistRegistrationError,
    ) as exc:
        await callback.answer(
            t(
                "cabinet_profile_update_failed",
                language,
            ).format(
                error=str(exc)
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )

    await state.set_state(None)
    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "cabinet_location_updated",
                language,
            ),
            reply_markup=(
                specialist_edit_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

@billing_router.callback_query(
    F.data.startswith("CAB_GEO_COUNTRY:")
)
@billing_router.callback_query(
    F.data.startswith("CAB_COUNTRY_PLACE:")
)
async def choose_specialist_country_update(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    if (callback.data or "").startswith(
        "CAB_COUNTRY_PLACE:"
    ):
        candidates = (
            data.get(
                "cabinet_country_candidates"
            )
            or []
        )
    else:
        candidates = (
            data.get(
                "cabinet_geo_candidates"
            )
            or []
        )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
        if index < 0:
            raise IndexError
        candidate = candidates[index]
    except (
        IndexError,
        TypeError,
        ValueError,
        KeyError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).save_country_candidate(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    candidate=candidate,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except RateLimitError:
        await callback.answer(
            t(
                "error_rate_limited",
                language,
            ),
            show_alert=True,
        )
        return
    except (
        GeoServiceError,
        SpecialistRegistrationError,
    ) as exc:
        await callback.answer(
            t(
                "cabinet_profile_update_failed",
                language,
            ).format(
                error=str(exc)
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )

    await state.set_state(None)
    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "cabinet_location_updated",
                language,
            ),
            reply_markup=(
                specialist_edit_keyboard(
                    language
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

@billing_router.callback_query(F.data == "CAB_EDIT_CATEGORY")
async def ask_edit_specialist_category(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).open_profession_editor(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    limit=50,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    categories = list(
        profile_action.result.categories
    )
    selected_professions = list(
        profile_action.result.selections
    )
    selected_profession_ids = [
        item["profession_id"]
        for item in selected_professions
    ]

    await state.update_data(
        cabinet_category_ids=[
            str(item.id)
            for item in categories
        ],
        cabinet_selected_profession_ids=(
            selected_profession_ids
        ),
        cabinet_selected_professions=(
            selected_professions
        ),
        cabinet_categories_page=0,
    )
    await state.set_state(
        SpecialistCabinetFSM
        .choosing_category,
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=(
                cabinet_category_prompt_text(
                    selected_professions,
                    language,
                )
            ),
            reply_markup=(
                cabinet_category_keyboard(
                    items=categories,
                    selected_professions=(
                        selected_professions
                    ),
                    language=language,
                    page=0,
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(
    StateFilter(SpecialistCabinetFSM.choosing_category),
    F.data.startswith("CAB_CAT_PAGE:"),
)
async def change_specialist_category_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        page = max(
            0,
            int(
                (callback.data or "")
                .split(":", 1)[1]
            ),
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).list_profession_categories(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    limit=50,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    categories = list(
        profile_action.result
    )
    data = await state.get_data()
    selected_professions = (
        data.get(
            "cabinet_selected_professions"
        )
        or []
    )

    await state.update_data(
        cabinet_categories_page=page,
        cabinet_category_ids=[
            str(item.id)
            for item in categories
        ],
    )

    await callback.message.edit_reply_markup(
        reply_markup=(
            cabinet_category_keyboard(
                items=categories,
                selected_professions=(
                    selected_professions
                ),
                language=language,
                page=page,
            )
        )
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("CAB_CAT:"))
async def choose_specialist_category_update(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    category_ids = (
        data.get("cabinet_category_ids")
        or []
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
        if index < 0:
            raise IndexError
        category_id = category_ids[index]
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).open_profession_category(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    category_id=category_id,
                    limit=50,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileProfessionNotFoundError:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    category = (
        profile_action.result.category
    )
    professions = list(
        profile_action.result.professions
    )
    category_id = str(category.id)
    selected_profession_ids = list(
        data.get(
            "cabinet_selected_profession_ids"
        )
        or []
    )
    selected_professions = list(
        data.get(
            "cabinet_selected_professions"
        )
        or []
    )

    await state.update_data(
        cabinet_pending_category_id=(
            category_id
        ),
        cabinet_pending_category_name=(
            localized_name(
                category,
                language,
            )
        ),
        cabinet_profession_ids=[
            str(item.id)
            for item in professions
        ],
        cabinet_professions_page=0,
    )
    await state.set_state(
        SpecialistCabinetFSM
        .choosing_profession
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=(
                cabinet_profession_prompt_text(
                    selected_professions,
                    language,
                )
            ),
            reply_markup=(
                cabinet_profession_multi_keyboard(
                    items=professions,
                    selected_ids=(
                        selected_profession_ids
                    ),
                    language=language,
                    page=0,
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )

@billing_router.callback_query(F.data == "CAB_EDIT_PROFESSION")
async def ask_edit_specialist_profession(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    await block_critical_profile_edit(
        callback,
        state,
        field="professions",
        language=language,
    )

@billing_router.callback_query(
    StateFilter(SpecialistCabinetFSM.choosing_profession),
    F.data.startswith("CAB_PROF_PAGE:"),
)
async def change_specialist_profession_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    try:
        page = max(
            0,
            int(
                (callback.data or "")
                .split(":", 1)[1]
            ),
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    category_id = data.get(
        "cabinet_pending_category_id"
    )
    if not category_id:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).list_professions_for_category(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    category_id=category_id,
                    limit=50,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileSelectionError:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    professions = list(
        profile_action.result
    )

    await state.update_data(
        cabinet_profession_ids=[
            str(item.id)
            for item in professions
        ],
        cabinet_professions_page=page,
    )

    await callback.message.edit_reply_markup(
        reply_markup=(
            cabinet_profession_multi_keyboard(
                items=professions,
                selected_ids=(
                    data.get(
                        "cabinet_selected_profession_ids"
                    )
                    or []
                ),
                language=language,
                page=page,
            )
        )
    )
    await callback.answer()

@billing_router.callback_query(
    StateFilter(SpecialistCabinetFSM.choosing_profession),
    F.data == "CAB_PROF_BACK_CATEGORIES",
)
async def return_to_specialist_categories(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).list_profession_categories(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    limit=50,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    categories = list(
        profile_action.result
    )
    selected_professions = (
        data.get(
            "cabinet_selected_professions"
        )
        or []
    )
    page = data.get(
        "cabinet_categories_page",
        0,
    )

    await state.update_data(
        cabinet_category_ids=[
            str(item.id)
            for item in categories
        ],
    )
    await state.set_state(
        SpecialistCabinetFSM
        .choosing_category,
    )

    await callback.message.edit_text(
        cabinet_category_prompt_text(
            selected_professions,
            language,
        ),
        reply_markup=(
            cabinet_category_keyboard(
                items=categories,
                selected_professions=(
                    selected_professions
                ),
                language=language,
                page=page,
            )
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("CAB_PROF:"))
async def choose_specialist_profession_update(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    category_id = data.get(
        "cabinet_pending_category_id"
    )
    selected_profession_ids = list(
        data.get(
            "cabinet_selected_profession_ids"
        )
        or []
    )
    selected_professions = list(
        data.get(
            "cabinet_selected_professions"
        )
        or []
    )
    page = data.get(
        "cabinet_professions_page",
        0,
    )

    if not category_id:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
        if index < 0:
            raise ValueError
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).toggle_profession(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    category_id=category_id,
                    profession_index=index,
                    selected_professions=(
                        selected_professions
                    ),
                    limit=50,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileProfessionNotFoundError:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileProfessionLimitError as exc:
        error_key = {
            "categories": (
                "spec_profession_limit_categories"
            ),
            "per_category": (
                "spec_profession_limit_per_category"
            ),
        }.get(
            exc.reason,
            "spec_profession_select_one",
        )
        await callback.answer(
            t(
                error_key,
                language,
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    result = profile_action.result
    category = result.category
    profession = result.profession
    professions = list(
        result.professions
    )
    profession_id = str(
        profession.id
    )

    if result.operation == "remove":
        selected_profession_ids = [
            item
            for item
            in selected_profession_ids
            if item != profession_id
        ]
        selected_professions = [
            item
            for item in selected_professions
            if item["profession_id"]
            != profession_id
        ]
    else:
        selected_profession_ids.append(
            profession_id
        )
        selected_professions.append(
            {
                "category_id": str(
                    profession.category_id
                ),
                "category_name": (
                    localized_name(
                        category,
                        language,
                    )
                ),
                "profession_id": (
                    profession_id
                ),
                "profession_name": (
                    localized_name(
                        profession,
                        language,
                    )
                ),
            }
        )

    await state.update_data(
        cabinet_pending_category_id=str(
            category.id
        ),
        cabinet_pending_category_name=(
            localized_name(
                category,
                language,
            )
        ),
        cabinet_profession_ids=[
            str(item.id)
            for item in professions
        ],
        cabinet_selected_profession_ids=(
            selected_profession_ids
        ),
        cabinet_selected_professions=(
            selected_professions
        ),
    )

    await callback.message.edit_text(
        cabinet_profession_prompt_text(
            selected_professions,
            language,
        ),
        reply_markup=(
            cabinet_profession_multi_keyboard(
                items=professions,
                selected_ids=(
                    selected_profession_ids
                ),
                language=language,
                page=page,
            )
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_PROF_DONE")
async def save_specialist_professions_update(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    selected_professions = (
        data.get(
            "cabinet_selected_professions"
        )
        or []
    )

    if not selected_professions:
        await callback.answer(
            t(
                "spec_profession_select_one",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).save_professions(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    profession_selections=(
                        selected_professions
                    ),
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "billing_start_required",
                language,
            ),
            show_alert=True,
        )
        return
    except SpecialistProfileNotFoundError:
        await state.clear()
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        return
    except ValueError as exc:
        await callback.answer(
            t(
                "cabinet_profile_update_failed",
                language,
            ).format(
                error=str(exc)
            ),
            show_alert=True,
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    specialist = profile_action.result

    logger.info(
        (
            "cabinet_professions_updated "
            "telegram_id=%s "
            "specialist_id=%s"
        ),
        callback.from_user.id,
        specialist.id,
    )

    await callback.answer(
        t(
            "cabinet_profile_updated",
            language,
        )
    )

    await show_specialist_cabinet(
        callback,
        state,
        callback_answered=True,
    )

def cabinet_category_prompt_text(
    selected_professions: list[dict],
    language: str,
) -> str:
    lines = [
        t("specialist_professions_title", language),
        t("specialist_professions_hint", language),
        "",
        t("cabinet_choose_direction", language),
    ]

    if selected_professions:
        lines.extend(
            [
                "",
                t("spec_selected_professions_title", language),
                cabinet_selected_professions_text(
                    selected_professions,
                    language,
                ),
            ]
        )

    return "\n".join(lines)

def cabinet_category_keyboard(
    *,
    items,
    selected_professions: list[dict],
    language: str,
    page: int = 0,
) -> InlineKeyboardMarkup:
    page = max(0, page)
    start = page * SPECIALIST_CABINET_EDITOR_PAGE_SIZE
    end = start + SPECIALIST_CABINET_EDITOR_PAGE_SIZE
    page_items = items[start:end]

    selected_category_ids = {
        item["category_id"]
        for item in selected_professions
    }
    rows: list[list[InlineKeyboardButton]] = []

    for index, item in enumerate(page_items, start=start):
        marker = "✓ " if str(item.id) in selected_category_ids else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{localized_name(item, language)}",
                    callback_data=f"CAB_CAT:{index}",
                )
            ]
        )

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"CAB_CAT_PAGE:{page - 1}",
            )
        )

    if end < len(items):
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"CAB_CAT_PAGE:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    if selected_professions:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("spec_profession_done_btn", language),
                    callback_data="CAB_PROF_DONE",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="CAB_PROFILE_EDIT",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def save_specialist_profile_update(
    *,
    message: Message,
    state: FSMContext,
    display_name: str | None = None,
    short_description: str | None = None,
    contact_text: str | None = None,
):
    language = (
        await get_billing_interface_language(
            message.from_user.id,
            message.from_user.language_code,
        )
    )

    try:
        async with get_session() as session:
            profile_action = await (
                SpecialistProfileService(
                    session
                ).save_basic_profile(
                    platform_user_id=(
                        message.from_user.id
                    ),
                    display_name=display_name,
                    short_description=(
                        short_description
                    ),
                    contact_text=contact_text,
                )
            )
    except SpecialistProfileUserNotFoundError:
        await state.set_state(None)
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "billing_start_required",
                language,
            ),
        )
        return
    except SpecialistProfileNotFoundError:
        await state.set_state(None)
        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "cabinet_profile_not_found",
                language,
            ),
        )
        return
    except SpecialistRegistrationError as exc:
        logger.warning(
            (
                "cabinet_profile_update_failed "
                "telegram_id=%s error=%s"
            ),
            message.from_user.id,
            exc,
        )

        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "cabinet_profile_update_failed",
                language,
            ).format(
                error=str(exc)
            ),
            reply_markup=(
                specialist_edit_keyboard(
                    language
                )
            ),
        )
        return

    language = normalize_language(
        profile_action.actor.language
    )
    result = profile_action.result

    if not result.changed:
        await state.set_state(None)

        await replace_billing_input_screen(
            message=message,
            state=state,
            text=t(
                "cabinet_profile_no_changes",
                language,
            ),
            reply_markup=(
                specialist_edit_keyboard(
                    language
                )
            ),
        )
        return

    logger.info(
        (
            "cabinet_profile_updated "
            "telegram_id=%s "
            "specialist_id=%s"
        ),
        message.from_user.id,
        result.specialist_id,
    )

    await state.set_state(None)

    await replace_billing_input_screen(
        message=message,
        state=state,
        text=t(
            "cabinet_profile_updated",
            language,
        ),
        reply_markup=(
            specialist_edit_keyboard(
                language
            )
        ),
    )


@billing_router.message(SpecialistCabinetFSM.entering_display_name)
async def receive_specialist_name_update(message: Message, state: FSMContext):
    await save_specialist_profile_update(
        message=message,
        state=state,
        display_name=(message.text or "").strip(),
    )


@billing_router.message(SpecialistCabinetFSM.entering_description)
async def receive_specialist_description_update(message: Message, state: FSMContext):
    await save_specialist_profile_update(
        message=message,
        state=state,
        short_description=(message.text or "").strip(),
    )


@billing_router.message(SpecialistCabinetFSM.entering_contact)
async def receive_specialist_contact_update(message: Message, state: FSMContext):
    await save_specialist_profile_update(
        message=message,
        state=state,
        contact_text=(message.text or "").strip(),
    )

@billing_router.callback_query(
    F.data.startswith("CLIENT_REQUEST")
)
async def block_legacy_client_request_callbacks(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.answer(
        t("legacy_requests_unavailable", language),
        show_alert=True,
    )
