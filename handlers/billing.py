import logging
import logging
from uuid import UUID
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
)
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from database.repositories.reviews import ReviewRepository
from database.repositories.translation import TranslationRepository
from database.repositories.user import UserRepository
from database.models import (
    Invoice,
    PaidFeature,
    Specialist,
    SpecialistService as SpecialistServiceModel,
)
from database.repositories.billing import BillingRepository
from database.repositories.legal import LegalRepository
from services.legal import LegalService
from database.repositories.specialist import SpecialistRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard_for_user, normalize_language, open_current_role_cabinet, send_global_main_menu
from handlers.search import (
    SpecialistSearchFSM,
    complaint_reason_keyboard,
    contact_thread_keyboard,
    format_chat_message_body,
)
from services.translation import TranslationService
from services.billing import BillingError, BillingService
from services.specialist import (
    SpecialistProfileUpdateData,
    SpecialistRegistrationError,
    SpecialistService,
    SpecialistServiceItemData,
    MAX_PROFESSIONS_PER_CATEGORY,
    MAX_SPECIALIST_CATEGORIES,
)
from services.user import UserService
from ui.texts import t
from utils.telegram_cleanup import (
    send_telegram_attachment,
    split_telegram_text,
)
from services.geo_service import GeoServiceError
from services.rate_limit import RateLimitError
from database.repositories.portfolio import PortfolioRepository
from database.repositories.favorites import FavoriteRepository
from database.repositories.search import SpecialistSearchRepository
from services.geo_search import GeoSearchService, SpecialistPublicCard
from services.portfolio import PortfolioService, PortfolioServiceError
from database.repositories.privacy import PrivacyRepository
from services.privacy import PrivacyService
from services.reviews import ReviewService
from io import BytesIO
from database.repositories.contact import ContactChatRepository
from services.contact_chat import ContactChatError, ContactChatService
from services.favorites import FavoriteService

billing_router = Router()
logger = logging.getLogger(__name__)
SPECIALIST_SERVICES_PAGE_SIZE = 5
OWNER_PORTFOLIO_PAGE_SIZE = 5
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
    waiting_portfolio_file = State()
    entering_portfolio_caption = State()
    confirming_portfolio_upload = State()
    entering_service_title = State()
    entering_service_description = State()
    entering_service_price = State()
    confirming_service = State()
    entering_availability_date = State()
    entering_messages_search = State()

async def get_billing_user_context(telegram_id: int | str):
    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return None, None
        return user.id, user.tenant_id

async def get_current_specialist_for_telegram(
    telegram_id: int | str,
):
    async with get_session() as session:
        context = await UserService(
            session
        ).get_specialist_context_by_telegram_id(
            telegram_id
        )

        if not context:
            return None, None, None

        return (
            context.user,
            context.specialist,
            context.tenant_id,
        )

async def get_billing_interface_language(
    telegram_id: int | str,
    fallback_language: str | None,
) -> str:
    language = normalize_language(fallback_language)

    async with get_session() as session:
        user = await UserService(
            session
        ).get_user_by_telegram_id(
            telegram_id
        )

        if not user:
            return language

        resolved_language = await TranslationService(
            TranslationRepository(session)
        ).resolve_interface_language(
            user_id=user.id,
            fallback_language=user.language_code,
        )

    return normalize_language(resolved_language)

def billing_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("feature_disabled_beta", language),
                    callback_data="BETA_DISABLED:promotion",
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

CLIENT_DIALOGS_PAGE_SIZE = 5
FAVORITES_PAGE_SIZE = 5


def client_dialogs_keyboard(
    *,
    items_count: int,
    page: int,
    view: str,
    language: str,
    show_role_switch: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("messages_tab_new", language),
                callback_data="CLIENT_DIALOGS:new:0",
            ),
            InlineKeyboardButton(
                text=t("messages_tab_correspondence", language),
                callback_data="CLIENT_DIALOGS:active:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("messages_tab_completed", language),
                callback_data="CLIENT_DIALOGS:completed:0",
            ),
            InlineKeyboardButton(
                text=t("messages_tab_archive", language),
                callback_data="CLIENT_DIALOGS:archive:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("messages_search_btn", language),
                callback_data="CLIENT_DIALOG_SEARCH",
            )
        ],
    ]

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"CLIENT_DIALOGS:{view}:{page - 1}",
            )
        )
    if items_count >= CLIENT_DIALOGS_PAGE_SIZE:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"CLIENT_DIALOGS:{view}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

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

def client_dialog_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("messages_open_chat", language),
                    callback_data=f"CLIENT_DIALOG_OPEN:{index}",
                )
            ]
        ]
    )

def client_dialog_status_label(status: str | None, language: str) -> str:
    key = {
        "waiting_specialist": "client_dialog_status_waiting_specialist",
        "waiting_client": "client_dialog_status_waiting_client",
        "open": "client_dialog_status_open",
        "in_discussion": "client_dialog_status_in_discussion",
        "completed": "client_dialog_status_completed",
        "closed": "client_dialog_status_closed",
    }.get(status or "", "client_dialog_status_other")

    return t(key, language)

def compact_dialog_text(value: str | None, limit: int = 56) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."

def format_dialog_card(
    *,
    item,
    display_number: int,
    language: str,
) -> str:
    name = (
        item.specialist_name
        or item.client_name
        or t("client_dialog_unknown_user", language)
    )
    unread = int(item.unread_count or 0)

    if item.last_message_text == "[deleted by user request]":
        last_text = t("dialog_message_deleted", language)
    else:
        last_text = compact_dialog_text(
            item.last_message_text,
            limit=96,
        )

    if unread > 0:
        status = t("messages_card_status_new", language)
    elif item.status == "waiting_client":
        status = t("messages_card_status_waiting_you", language)
    elif item.status == "waiting_specialist":
        status = t("messages_card_status_waiting_other", language)
    elif item.status in {"completed", "closed"}:
        status = t("messages_card_status_completed", language)
    else:
        status = t("messages_card_status_in_progress", language)

    lines = [
        f"👤 {name}",
    ]

    if item.profession_name:
        lines.append(f"💼 {item.profession_name}")

    lines.append(status)

    if last_text:
        lines.append(f"💬 {last_text}")

    if item.last_message_at:
        lines.append(
            f"🕘 {item.last_message_at:%d.%m %H:%M}",
        )

    if unread > 0:
        lines.append(
            t(
                "messages_card_unread",
                language,
            ).format(count=unread)
        )

    return "\n".join(lines)

def format_messages_list_text(
    items,
    *,
    unread_messages: int,
    language: str,
) -> str:
    title = (
        t(
            "messages_title_with_unread",
            language,
        ).format(count=unread_messages)
        if unread_messages > 0
        else t("messages_title", language)
    )

    lines = [
        title,
        t("messages_hint", language),
    ]

    if not items:
        lines.extend(
            [
                "",
                t("messages_empty", language),
            ]
        )

    return "\n".join(lines)


def format_client_dialogs_text(
    items,
    language: str,
    *,
    unread_messages: int,
) -> str:
    return format_messages_list_text(
        items,
        unread_messages=unread_messages,
        language=language,
    )

def format_thread_history(
    messages,
    *,
    counterpart_name: str,
    language: str,
) -> str:
    if not messages:
        return t("client_thread_no_messages", language)

    lines = []

    for message in messages:
        if message.is_system:
            lines.append(
                format_chat_message_body(message, language)
            )
            continue

        sender_name = (
            t("contact_chat_you_label", language)
            if message.is_sent_by_viewer
            else counterpart_name
        )
        sent_at = message.created_at.strftime("%d.%m %H:%M")

        lines.append(
            f"{sender_name} · {sent_at}\n"
            f"{format_chat_message_body(message, language)}"
        )

    return "\n\n".join(lines)

def message_thread_status_label(
    status: str | None,
    *,
    viewer_role: str,
    language: str,
) -> str:
    if status in {"completed", "closed"}:
        return t("messages_card_status_completed", language)

    waiting_for_viewer = (
        "waiting_client"
        if viewer_role == "client"
        else "waiting_specialist"
    )

    if status == waiting_for_viewer:
        return t("messages_card_status_waiting_you", language)

    if status in {"waiting_client", "waiting_specialist"}:
        return t("messages_card_status_waiting_other", language)

    return t("messages_card_status_in_progress", language)


def format_open_thread_chat_text(
    detail,
    *,
    counterpart_name: str,
    viewer_role: str,
    language: str,
) -> str:
    history = format_thread_history(
        detail.messages or [],
        counterpart_name=counterpart_name,
        language=language,
    )

    lines = [
        f"💬 {counterpart_name}",
    ]

    if detail.profession_name:
        lines.append(f"💼 {detail.profession_name}")

    lines.extend(
        [
            message_thread_status_label(
                detail.thread_status,
                viewer_role=viewer_role,
                language=language,
            ),
            "",
            history,
        ]
    )

    return "\n".join(lines)


def format_client_thread_detail_text(
    detail,
    language: str,
) -> str:
    return format_open_thread_chat_text(
        detail,
        counterpart_name=detail.specialist_name,
        viewer_role="client",
        language=language,
    )


def format_specialist_thread_detail_text(
    detail,
    language: str,
) -> str:
    return format_open_thread_chat_text(
        detail,
        counterpart_name=detail.client_name,
        viewer_role="specialist",
        language=language,
    )

def paid_features_keyboard(
    features: list[PaidFeature],
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, feature in enumerate(features):
        rows.append(
            [
                InlineKeyboardButton(
                    text=format_feature_button(feature),
                    callback_data=f"BILL_BUY:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="BILL_PANEL",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invoice_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("billing_i_paid", language),
                    callback_data="BILL_CLAIM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="BILL_FEATURES",
                )
            ],
        ]
    )

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

def favorites_list_keyboard(
    language: str,
    *,
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows = []
    navigation_row = []

    if page > 0:
        navigation_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_prev", language),
                callback_data=f"CAB_FAVORITES:{page - 1}",
            )
        )

    if has_next:
        navigation_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_next", language),
                callback_data=f"CAB_FAVORITES:{page + 1}",
            )
        )

    if navigation_row:
        rows.append(navigation_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="search_start",
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


def favorite_list_card_keyboard(
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("search_result_details_btn", language),
                    callback_data=f"CAB_FAV_VIEW:{index}",
                ),
                InlineKeyboardButton(
                    text=t("search_result_message_btn", language),
                    callback_data=f"search_result_contact:{index}",
                ),
            ]
        ]
    )

def favorite_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("contact", language),
                    callback_data="search_contact_pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("favorite_remove_btn", language),
                    callback_data="CAB_FAV_REMOVE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="CAB_FAVORITES",
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


def favorite_work_format_label(value: str | None, language: str) -> str:
    labels = {
        None: t("search_filter_any", language),
        "at_client": t("search_work_at_client", language),
        "at_specialist": t("search_work_at_specialist", language),
        "remote": t("search_work_remote", language),
        "mixed": t("search_work_mixed", language),
    }
    return labels.get(value, value or "-")


def format_favorite_card(card: SpecialistPublicCard, language: str) -> str:
    lines = [card.display_name, ""]

    category_parts = [
        part
        for part in [card.category_name, card.profession_name]
        if part
    ]
    if category_parts:
        lines.append(" • ".join(category_parts))

    if card.city_name:
        lines.append(f"{t('search_filter_location_label', language)}: {card.city_name}")
    elif card.work_format == "remote":
        lines.append(
            f"{t('search_filter_location_label', language)}: "
            f"{favorite_work_format_label('remote', language)}"
        )

    work_format = favorite_work_format_label(card.work_format, language)
    if card.work_format:
        lines.append(f"{t('search_filter_work_label', language)}: {work_format}")

    if card.service_titles:
        lines.append(
            f"{t('search_services_label', language)}: "
            f"{', '.join(card.service_titles)}"
        )

    if card.languages:
        lines.append(
            f"{t('search_filter_language_label', language)}: "
            f"{', '.join(card.languages)}"
        )

    if card.reviews_count > 0 and card.rating is not None:
        rating = f"{float(card.rating):.1f} ({card.reviews_count})"
    else:
        rating = t("search_no_reviews", language)

    lines.append(f"{t('search_rating', language)}: {rating}")

    description = " ".join((card.short_description or "").split())
    if description:
        lines.extend(["", description[:300]])

    return "\n".join(lines)

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

def portfolio_menu_keyboard(
    language: str,
    *,
    page: int = 0,
    total: int = 0,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("portfolio_upload_button", language),
                callback_data="CAB_PORTFOLIO_UPLOAD",
            )
        ]
    ]

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"CAB_PORTFOLIO_PAGE:{page - 1}",
            )
        )

    if (page + 1) * OWNER_PORTFOLIO_PAGE_SIZE < total:
        nav_row.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"CAB_PORTFOLIO_PAGE:{page + 1}",
            )
        )

    if nav_row:
        rows.append(nav_row)

    rows.extend(
        [
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

    return InlineKeyboardMarkup(inline_keyboard=rows)

def portfolio_item_keyboard(
    *,
    item_id: UUID,
    signed_url: str,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("portfolio_open_button", language),
                    url=signed_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("portfolio_delete_button", language),
                    callback_data=f"CAB_PORT_DEL:{item_id}",
                )
            ],
        ]
    )


def portfolio_item_text(view, language: str) -> str:
    status_key = (
        f"portfolio_status_{view.item.status}"
    )

    file_label_key = (
        "portfolio_photo_label"
        if view.storage_object.file_type == "photo"
        else "portfolio_pdf_label"
    )

    file_label = t(file_label_key, language)
    title = view.item.title or file_label
    status = t(status_key, language)

    return f"{file_label}: {title}\n{status}"

async def send_owner_portfolio(
    message: Message,
    *,
    tenant_id: UUID,
    owner_user_id: UUID,
    language: str,
    page: int = 0,
):
    async with get_session() as session:
        portfolio_page = await PortfolioService(
            PortfolioRepository(session)
        ).list_owner_items_page(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            page=page,
            page_size=OWNER_PORTFOLIO_PAGE_SIZE,
        )

    page = portfolio_page.page
    total = portfolio_page.total
    page_items = portfolio_page.items

    if total == 0:
        await message.answer(
            (
                f"{t('specialist_portfolio_title', language)}\n"
                f"{t('specialist_portfolio_hint', language)}\n\n"
                f"{t('portfolio_empty', language)}"
            ),
            reply_markup=portfolio_menu_keyboard(
                language,
                page=page,
                total=total,
            ),
        )
        return

    await message.answer(
        (
            f"{t('specialist_portfolio_title', language)}\n"
            f"{t('specialist_portfolio_hint', language)}\n"
            f"{page + 1}/"
            f"{portfolio_page.total_pages}"
        ),
        reply_markup=portfolio_menu_keyboard(
            language,
            page=page,
            total=total,
        ),
    )

    for view in page_items:
        text = portfolio_item_text(view, language)
        keyboard = portfolio_item_keyboard(
            item_id=view.item.id,
            signed_url=view.signed_url,
            language=language,
        )
        await message.answer(text, reply_markup=keyboard)
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
        return "✓ " if code in selected else ""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{marker('ru')}RU",
                    callback_data="CAB_LANG_TOGGLE:ru",
                ),
                InlineKeyboardButton(
                    text=f"{marker('en')}EN",
                    callback_data="CAB_LANG_TOGGLE:en",
                ),
                InlineKeyboardButton(
                    text=f"{marker('pt')}PT",
                    callback_data="CAB_LANG_TOGGLE:pt",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("spec_profession_done_btn", language),
                    callback_data="CAB_LANG_DONE",
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

def format_profile_languages_text(
    selected: list[str],
    language: str,
) -> str:
    language_names = {
        "ru": t("search_language_ru", language),
        "en": t("search_language_en", language),
        "pt": t("search_language_pt", language),
    }
    selected_text = ", ".join(
        language_names[code]
        for code in selected
        if code in language_names
    )

    lines = [
        t("specialist_languages_title", language),
        t("specialist_languages_hint", language),
    ]

    if selected_text:
        lines.extend(
            [
                "",
                t(
                    "specialist_languages_selected",
                    language,
                ).format(languages=selected_text),
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

def specialist_availability_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("spec_availability_now_btn", language),
                    callback_data="SPEC_AVAILABILITY_SET:available_now",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("spec_availability_busy_btn", language),
                    callback_data="SPEC_AVAILABILITY_SET:partly_busy",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("spec_availability_from_date_btn", language),
                    callback_data="SPEC_AVAILABILITY_SET:available_from",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_specialist_btn", language),
                    callback_data="M_SPECIALIST",
                )
            ],
        ]
    )


def format_specialist_availability_text(specialist, language: str) -> str:
    metadata = dict(specialist.extra_metadata or {})
    availability_status = metadata.get("availability_status")

    if availability_status == "partly_busy":
        status_text = t("spec_availability_busy", language)
    elif availability_status == "available_from":
        date_text = metadata.get("available_from_text") or ""
        status_text = t("spec_availability_from_date", language).format(
            date=date_text,
        )
    else:
        status_text = t("spec_availability_now", language)

    return (
        f"{t('spec_availability_title', language)}\n"
        f"{t('spec_availability_hint', language)}\n\n"
        f"{status_text}"
    )

def format_specialist_moderation_text(
    specialist,
    language: str,
) -> str:
    status = specialist.status or "draft"

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

    if specialist.moderation_comment:
        lines.extend(
            [
                "",
                f"{t('spec_moderation_comment_label', language)}:",
                specialist.moderation_comment,
            ]
        )

    return "\n".join(lines)

@billing_router.callback_query(F.data == "SPEC_AVAILABILITY")
async def show_specialist_availability(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    await state.update_data(
        cabinet_specialist_id=str(specialist.id),
        cabinet_user_id=str(user.id),
        cabinet_tenant_id=str(tenant_id),
    )

    await callback.message.answer(
        format_specialist_availability_text(specialist, language),
        reply_markup=specialist_availability_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("SPEC_AVAILABILITY_SET:"))
async def set_specialist_availability(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    availability_status = (callback.data or "").split(":", 1)[1]

    if availability_status == "available_from":
        await state.update_data(pending_availability_status=availability_status)
        await state.set_state(SpecialistCabinetFSM.entering_availability_date)
        await callback.message.answer(
            t("spec_availability_date_prompt", language),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("billing_back", language),
                            callback_data="SPEC_AVAILABILITY",
                        )
                    ]
                ]
            ),
        )
        await callback.answer()
        return

    specialist_id = data.get("cabinet_specialist_id")
    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")

    if not specialist_id or not user_id or not tenant_id:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        await state.clear()
        return

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).update_availability(
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
                specialist_id=UUID(
                    specialist_id
                ),
                availability_status=(
                    availability_status
                ),
            )

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("spec_availability_saved", language),
        reply_markup=specialist_availability_keyboard(language),
    )
    await callback.answer()


@billing_router.message(SpecialistCabinetFSM.entering_availability_date)
async def receive_specialist_availability_date(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    data = await state.get_data()

    specialist_id = data.get("cabinet_specialist_id")
    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    date_text = (message.text or "").strip()

    if not specialist_id or not user_id or not tenant_id:
        await message.answer(t("cabinet_profile_not_found", language))
        await state.clear()
        return

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).update_availability(
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
                specialist_id=UUID(
                    specialist_id
                ),
                availability_status=(
                    "available_from"
                ),
                available_from_text=date_text,
            )

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await message.answer(
            str(exc)
        )
        return

    await state.set_state(None)
    await message.answer(
        t("spec_availability_saved", language),
        reply_markup=specialist_availability_keyboard(language),
    )

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

def cabinet_profession_limit_error_key(
    selected_professions: list[dict],
    category_id: str,
) -> str | None:
    category_ids = {
        str(item.get("category_id"))
        for item in selected_professions
        if item.get("category_id")
    }

    if category_id not in category_ids and len(category_ids) >= MAX_SPECIALIST_CATEGORIES:
        return "spec_profession_limit_categories"

    professions_in_category = [
        item
        for item in selected_professions
        if str(item.get("category_id")) == category_id
    ]

    if len(professions_in_category) >= MAX_PROFESSIONS_PER_CATEGORY:
        return "spec_profession_limit_per_category"

    return None

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


async def get_specialist_location_text(
    specialist: Specialist | None,
    language: str,
) -> str:
    if not specialist:
        return "-"

    async with get_session() as session:
        city, country = await SpecialistRepository(
            session
        ).get_specialist_location_parts(
            specialist=specialist,
        )

    city_name = localized_name(city, language)
    country_name = localized_name(country, language)

    if city and country:
        return f"{city_name}, {country_name}"

    if city:
        return city_name

    if country:
        return country_name

    return "-"


def format_specialist_profile_text(
    specialist: Specialist | None,
    language: str,
    location_text: str = "-",
    profession_text: str = "-",
) -> str:
    if not specialist:
        return t("cabinet_profile_not_found", language)

    reviews_count = specialist.reviews_count or 0

    if reviews_count > 0 and specialist.rating is not None:
        rating_text = t(
            "specialist_card_rating_value",
            language,
        ).format(
            rating=f"{float(specialist.rating):.1f}",
            count=reviews_count,
        )
    else:
        rating_text = t(
            "specialist_card_rating_new",
            language,
        )

    status_key = (
        "specialist_card_status_available"
        if specialist.is_available
        else "specialist_card_status_busy"
    )

    lines = [
        t("specialist_card_title", language),
        "",
        f"👤 {specialist.display_name or '-'}",
    ]

    if profession_text and profession_text != "-":
        lines.append(
            t(
                "specialist_card_profession",
                language,
            ).format(profession=profession_text)
        )

    lines.append(rating_text)

    if location_text and location_text != "-":
        lines.append(f"📍 {location_text}")

    lines.extend(
        [
            t(status_key, language),
        ]
    )

    description = (specialist.short_description or "").strip()
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

def format_feature_button(feature: PaidFeature) -> str:
    return f"{feature.name} - {feature.price} {feature.currency}"


def format_features_text(features: list[PaidFeature], language: str) -> str:
    if not features:
        return t("billing_no_features", language)

    lines = [t("billing_features_title", language), ""]
    for index, feature in enumerate(features, start=1):
        duration_days = (feature.extra_metadata or {}).get("duration_days")
        period = (
            t("billing_period_days", language).format(days=duration_days)
            if duration_days
            else t("billing_period_not_set", language)
        )
        lines.append(
            f"{index}. {feature.name}\n"
            f"{feature.description or ''}\n"
            f"{t('billing_price', language)}: {feature.price} {feature.currency}\n"
            f"{t('billing_period', language)}: {period}"
        )
        lines.append("")

    return "\n".join(lines).strip()

def billing_status_label(status: str | None, language: str) -> str:
    labels = {
        "pending": {
            "ru": "Ожидает оплаты",
            "en": "Waiting for payment",
            "pt": "Aguardando pagamento",
        },
        "claimed": {
            "ru": "Оплата отправлена на проверку",
            "en": "Payment sent for review",
            "pt": "Pagamento enviado para revisão",
        },
        "paid": {
            "ru": "Оплачено",
            "en": "Paid",
            "pt": "Pago",
        },
        "cancelled": {
            "ru": "Отменено",
            "en": "Cancelled",
            "pt": "Cancelado",
        },
        "failed": {
            "ru": "Не удалось оплатить",
            "en": "Payment failed",
            "pt": "Falha no pagamento",
        },
    }

    normalized_language = language if language in {"ru", "en", "pt"} else "ru"
    return labels.get(status or "", {}).get(normalized_language, "")

def format_invoice_text(
    invoice: Invoice,
    manual_instructions: str,
    language: str,
) -> str:
    return (
        f"{t('billing_invoice_created', language)}\n\n"
        f"{t('billing_invoice_id', language)}: {invoice.id}\n"
        f"{t('billing_amount', language)}: {invoice.amount} {invoice.currency}\n"
        f"{t('admin_status', language)}: {billing_status_label(invoice.status, language)}\n\n"
        f"{t('billing_manual_instructions_title', language)}\n"
        f"{manual_instructions}"
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

async def build_specialist_cabinet_payload(
    telegram_id: int | str,
    fallback_language: str | None,
) -> tuple[
    str,
    str,
    InlineKeyboardMarkup | None,
]:
    language = (
        await get_billing_interface_language(
            telegram_id,
            fallback_language,
        )
    )

    async with get_session() as session:
        context = await SpecialistService(
            SpecialistRepository(session)
        ).open_specialist_cabinet(
            telegram_id=telegram_id,
            language=language,
        )

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

    if keyboard is None:
        await callback.message.answer(text)
        return

    await state.clear()
    await callback.message.answer(
        text,
        reply_markup=keyboard,
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

def message_thread_keyboard(
    language: str,
    *,
    role: str,
    allow_finish: bool = True,
) -> InlineKeyboardMarkup:
    back_callback = (
        "CLIENT_DIALOGS"
        if role == "client"
        else "SPEC_DIALOGS"
    )

    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "contact_chat_attach_btn",
                    language,
                ),
                callback_data="CONTACT_ATTACH_FILE",
            )
        ]
    ]

    if allow_finish:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_finish_btn",
                        language,
                    ),
                    callback_data="SPEC_THREAD_COMPLETE",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_report_btn",
                        language,
                    ),
                    callback_data="SPEC_THREAD_REPORT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "contact_chat_back_btn",
                        language,
                    ),
                    callback_data=back_callback,
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )

def completion_confirmation_keyboard(
    *,
    thread_id: UUID,
    role: str,
    language: str,
) -> InlineKeyboardMarkup:
    role_code = "s" if role == "specialist" else "c"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "messages_completion_confirm_btn",
                        language,
                    ),
                    callback_data=(
                        f"TCF:{thread_id}:{role_code}"
                    ),
                )
            ]
        ]
    )

def completed_conversation_keyboard(
    *,
    contact_request_id: str | None,
    role: str,
    language: str,
) -> InlineKeyboardMarkup:
    back_callback = (
        "CLIENT_DIALOGS"
        if role == "client"
        else "SPEC_DIALOGS"
    )

    rows: list[list[InlineKeyboardButton]] = []

    if contact_request_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("review_leave_btn", language),
                    callback_data=(
                        f"review_start:{contact_request_id}"
                    ),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("contact_chat_back_btn", language),
                callback_data=back_callback,
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )

def specialist_dialogs_keyboard(
    *,
    items_count: int,
    page: int,
    view: str,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("messages_tab_new", language),
                callback_data="SPEC_DIALOGS_VIEW:new:0",
            ),
            InlineKeyboardButton(
                text=t("messages_tab_correspondence", language),
                callback_data="SPEC_DIALOGS_VIEW:active:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("messages_tab_completed", language),
                callback_data="SPEC_DIALOGS_VIEW:completed:0",
            ),
            InlineKeyboardButton(
                text=t("messages_tab_archive", language),
                callback_data="SPEC_DIALOGS_VIEW:archive:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("messages_search_btn", language),
                callback_data="SPEC_DIALOG_SEARCH",
            )
        ],
    ]

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=(
                    f"SPEC_DIALOGS_VIEW:{view}:{page - 1}"
                ),
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=(
                    f"SPEC_DIALOGS_VIEW:{view}:{page + 1}"
                ),
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def specialist_dialog_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("messages_open_chat", language),
                    callback_data=f"SPEC_DIALOG_OPEN:{index}",
                )
            ]
        ]
    )

def format_specialist_dialogs_text(
    *,
    dialogs,
    view: str,
    page: int,
    unread_messages: int,
    language: str,
) -> str:
    return format_messages_list_text(
        dialogs,
        unread_messages=unread_messages,
        language=language,
    )

async def show_specialist_dialogs(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str = "active",
    page: int = 0,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(
            t("billing_start_required", language),
            show_alert=True,
        )
        return

    state_data = await state.get_data()
    search_query = state_data.get(
        "specialist_messages_search_query",
    )

    async with get_session() as session:
        contact_service = ContactChatService(
            ContactChatRepository(session)
        )

        dialogs = await contact_service.list_specialist_threads(
            user_id=user_id,
            view=view,
            limit=6,
            offset=page * 5,
            language=language,
            search_query=search_query,
        )

        unread_messages = (
            await contact_service.count_unread_messages(
                user_id=user_id,
                participant_role="specialist",
            )
        )

        await contact_service.record_messages_opened(
            tenant_id=tenant_id,
            user_id=user_id,
            participant_role="specialist",
            view=view,
            page=page,
        )

    visible_dialogs = dialogs[:5]
    has_next = len(dialogs) > 5

    await state.update_data(
        specialist_dialog_ids=[str(item.thread_id) for item in visible_dialogs],
        specialist_dialogs_view=view,
        specialist_dialogs_page=page,
    )
    await callback.message.answer(
        format_specialist_dialogs_text(
            dialogs=visible_dialogs,
            view=view,
            page=page,
            unread_messages=unread_messages,
            language=language,
        ),
    )

    for index, item in enumerate(visible_dialogs):
        display_number = page * CLIENT_DIALOGS_PAGE_SIZE + index + 1
        await callback.message.answer(
            format_dialog_card(
                item=item,
                display_number=display_number,
                language=language,
            ),
            reply_markup=specialist_dialog_card_keyboard(
                index=index,
                language=language,
            ),
        )

    await callback.message.answer(
        t("messages_hint", language),
        reply_markup=specialist_dialogs_keyboard(
            items_count=len(visible_dialogs),
            page=page,
            view=view,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

async def send_specialist_thread_detail(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    thread_id: str,
    language: str,
) -> None:
    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            detail = await ContactChatService(
                ContactChatRepository(session)
            ).get_thread_detail_for_viewer(
                tenant_id=tenant_id,
                thread_id=UUID(thread_id),
                user_id=user_id,
                participant_role="specialist",
                language=language,
            )
    except Exception:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await state.update_data(
        active_contact_request_id=(
            str(detail.contact_request_id)
            if detail.contact_request_id
            else None
        ),
        active_thread_id=thread_id,
        active_thread_role="specialist",
    )
    await state.set_state(
        SpecialistSearchFSM.entering_thread_message,
    )

    attachment_items = [
        item
        for item in detail.messages
        if item.attachment
    ]
    chat_chunks = split_telegram_text(
        format_specialist_thread_detail_text(
            detail,
            language,
        )
    )

    for index, chunk in enumerate(chat_chunks):
        is_last_chunk = index == len(chat_chunks) - 1

        await callback.message.answer(
            chunk,
            reply_markup=(
                message_thread_keyboard(
                    language,
                    role="specialist",
                )
                if is_last_chunk and not attachment_items
                else None
            ),
        )

    for index, item in enumerate(attachment_items):
        is_last_attachment = (
            index == len(attachment_items) - 1
        )
        sender_name = (
            t("contact_chat_you_label", language)
            if item.is_sent_by_viewer
            else detail.client_name
        )
        sent_at = item.created_at.strftime(
            "%d.%m %H:%M"
        )

        await send_telegram_attachment(
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            attachment=item.attachment,
            caption=(
                f"{sender_name} · {sent_at}\n"
                f"{format_chat_message_body(item, language)}"
            ),
            reply_markup=(
                message_thread_keyboard(
                    language,
                    role="specialist",
                )
                if is_last_attachment
                else None
            ),
        )

    await callback.answer()

@billing_router.callback_query(F.data.startswith("SPEC_DIALOG_OPEN:"))
async def open_specialist_dialog(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    data = await state.get_data()
    thread_ids = data.get("specialist_dialog_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(thread_ids):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await send_specialist_thread_detail(
        callback=callback,
        state=state,
        thread_id=thread_ids[index],
        language=language,
    )

@billing_router.callback_query(F.data == "SPEC_DIALOGS")
async def specialist_dialogs_entry(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.update_data(
        specialist_messages_search_query=None,
    )
    await show_specialist_dialogs(
        callback,
        state,
        view="active",
        page=0,
    )

@billing_router.callback_query(F.data.startswith("SPEC_DIALOGS_VIEW:"))
async def specialist_dialogs_view(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    view = parts[1] if len(parts) > 1 else "active"
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0

    if view not in{"new", "active", "completed", "archive"}:
        view = "active"
    if page < 0:
        page = 0

    await show_specialist_dialogs(callback, state, view=view, page=page)

@billing_router.callback_query(F.data == "SPEC_THREAD_COMPLETE")
async def finish_thread_from_chat(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    thread_id = data.get("active_thread_id")
    role = data.get("active_thread_role") or "client"
    contact_request_id = data.get("active_contact_request_id")

    if not thread_id:
        await callback.answer(
            t("contact_thread_not_found", language),
            show_alert=True,
        )
        return

    user_id, tenant_id = await get_billing_user_context(
        callback.from_user.id,
    )
    if not user_id or not tenant_id:
        await callback.answer(
            t("billing_start_required", language),
            show_alert=True,
        )
        return

    receiver_chat_id: str | None = None
    receiver_language = "ru"

    try:
        async with get_session() as session:
            result = await ContactChatService(
                ContactChatRepository(session)
            ).finish_thread(
                tenant_id=tenant_id,
                thread_id=UUID(thread_id),
                actor_user_id=user_id,
            )

            if (
                result.action == "requested"
                and result.requested_for_user_id
            ):
                user_repository = UserRepository(session)
                receiver_account = (
                    await user_repository
                    .get_telegram_account_by_user_id(
                        result.requested_for_user_id
                    )
                )
                receiver_language = normalize_language(
                    await user_repository.get_language_code(
                        result.requested_for_user_id
                    )
                    or "ru"
                )

                if receiver_account:
                    receiver_chat_id = (
                        receiver_account.platform_user_id
                    )
    except ContactChatError as exc:
        await callback.answer(
            t("contact_request_error", language).format(
                error=str(exc),
            ),
            show_alert=True,
        )
        return

    if result.action == "requested":
        pending_keyboard = message_thread_keyboard(
            language,
            role=role,
            allow_finish=False,
        )

        try:
            await callback.message.edit_reply_markup(
                reply_markup=pending_keyboard,
            )
        except TelegramBadRequest:
            pass

        await callback.message.answer(
            t("messages_completion_requested", language),
            reply_markup=pending_keyboard,
        )

        if (
            receiver_chat_id
            and result.requested_for_role
        ):
            try:
                await callback.message.bot.send_message(
                    chat_id=receiver_chat_id,
                    text=t(
                        "messages_completion_request_received",
                        receiver_language,
                    ),
                    reply_markup=completion_confirmation_keyboard(
                        thread_id=result.thread_id,
                        role=result.requested_for_role,
                        language=receiver_language,
                    ),
                )
            except (
                TelegramBadRequest,
                TelegramForbiddenError,
            ) as exc:
                logger.warning(
                    "completion_request_delivery_failed "
                    "thread_id=%s receiver_user_id=%s "
                    "error=%s",
                    result.thread_id,
                    result.requested_for_user_id,
                    exc,
                )

    elif result.action == "pending":
        try:
            await callback.message.edit_reply_markup(
                reply_markup=message_thread_keyboard(
                    language,
                    role=role,
                    allow_finish=False,
                ),
            )
        except TelegramBadRequest:
            pass

        await callback.answer(
            t(
                "messages_completion_already_requested",
                language,
            ),
            show_alert=True,
        )
        return

    else:
        await state.update_data(
            active_thread_id=None,
            review_thread_id=thread_id,
            review_thread_role=role,
        )

        await callback.message.answer(
            t("messages_completion_confirmed", language),
            reply_markup=completed_conversation_keyboard(
                contact_request_id=contact_request_id,
                role=role,
                language=language,
            ),
        )
        
@billing_router.callback_query(
    F.data.startswith("TCF:")
)
async def confirm_thread_completion_from_notification(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        _, thread_id_raw, role_code = (
            callback.data or ""
        ).split(":", 2)
        thread_id = UUID(thread_id_raw)
    except (TypeError, ValueError):
        await callback.answer(
            t("contact_thread_not_found", language),
            show_alert=True,
        )
        return

    if role_code not in {"c", "s"}:
        await callback.answer(
            t("contact_thread_not_found", language),
            show_alert=True,
        )
        return

    role = (
        "specialist"
        if role_code == "s"
        else "client"
    )

    user_id, tenant_id = await get_billing_user_context(
        callback.from_user.id,
    )
    if not user_id or not tenant_id:
        await callback.answer(
            t("billing_start_required", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ContactChatService(
                ContactChatRepository(session)
            ).finish_thread(
                tenant_id=tenant_id,
                thread_id=thread_id,
                actor_user_id=user_id,
            )
    except ContactChatError as exc:
        await callback.answer(
            t("contact_request_error", language).format(
                error=str(exc),
            ),
            show_alert=True,
        )
        return

    if result.action != "completed":
        await callback.answer(
            t("messages_completion_requested", language),
            show_alert=True,
        )
        return

    contact_request_id = (
        str(result.contact_request_id)
        if result.contact_request_id
        else None
    )

    await state.update_data(
        active_thread_id=None,
        active_contact_request_id=contact_request_id,
        review_thread_id=str(result.thread_id),
        review_thread_role=role,
    )

    await callback.message.answer(
        t("messages_completion_confirmed", language),
        reply_markup=completed_conversation_keyboard(
            contact_request_id=contact_request_id,
            role=role,
            language=language,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "SPEC_THREAD_REPORT")
async def report_specialist_thread(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    thread_id = data.get("active_thread_id")

    if not thread_id:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await state.update_data(
        pending_report_target_type="thread",
        pending_report_target_id=thread_id,
        selected_specialist_id=thread_id,
        user_language=language,
    )
    await state.set_state(SpecialistSearchFSM.viewing_results)

    await callback.message.answer(
        t("complaint_reason_prompt", language),
        reply_markup=complaint_reason_keyboard(language),
    )
    await callback.answer()


def specialist_service_status_text(status: str | None, language: str) -> str:
    normalized = status or "active"
    key = f"specialist_service_status_{normalized}"
    translated = t(key, language)
    if translated == key:
        return normalized
    return translated


def specialist_service_price_text(service: SpecialistServiceModel, language: str) -> str:
    if service.price_from is None and service.price_to is None:
        return t("specialist_service_price_not_set", language)

    currency = service.currency or "EUR"

    if service.price_from is not None and service.price_to is not None:
        return f"{float(service.price_from):.2f}-{float(service.price_to):.2f} {currency}"

    if service.price_from is not None:
        return f"{float(service.price_from):.2f} {currency}"

    return f"{float(service.price_to):.2f} {currency}"


def format_specialist_services_list(
    services: list[SpecialistServiceModel],
    *,
    page: int,
    total: int,
    language: str,
) -> str:
    lines = [
        t("specialist_services_title", language),
        t("specialist_services_hint", language),
        "",
        (
            f"{page + 1}/"
            f"{max(1, (total + SPECIALIST_SERVICES_PAGE_SIZE - 1) // SPECIALIST_SERVICES_PAGE_SIZE)}"
        ),
        "",
    ]

    if not services:
        lines.append(t("specialist_services_empty", language))
        return "\n".join(lines)

    for index, service in enumerate(services, start=1):
        lines.extend(
            [
                f"{index}. {service.title}",
                f"{t('cabinet_profile_price', language)}: {specialist_service_price_text(service, language)}",
                f"{t('cabinet_profile_status', language)}: {specialist_service_status_text(service.status, language)}",
                "",
            ]
        )

    return "\n".join(lines).strip()


def specialist_services_keyboard(
    *,
    services: list[SpecialistServiceModel],
    page: int,
    total: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t("specialist_service_add_btn", language),
                callback_data="SPEC_SERVICE_ADD",
            )
        ]
    ]

    for index, service in enumerate(services):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('specialist_service_edit_btn', language)}",
                    callback_data=f"SPEC_SERVICE_EDIT:{index}",
                ),
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('specialist_service_pause_btn', language)}",
                    callback_data=f"SPEC_SERVICE_PAUSE:{index}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('specialist_service_delete_btn', language)}",
                    callback_data=f"SPEC_SERVICE_DELETE:{index}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="<",
                callback_data=f"SPEC_SERVICES_PAGE:{page - 1}",
            )
        )

    if (page + 1) * SPECIALIST_SERVICES_PAGE_SIZE < total:
        nav_row.append(
            InlineKeyboardButton(
                text=">",
                callback_data=f"SPEC_SERVICES_PAGE:{page + 1}",
            )
        )

    if nav_row:
        rows.append(nav_row)

    rows.extend(
        [
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

    return InlineKeyboardMarkup(inline_keyboard=rows)

def service_form_back_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="SPEC_SERVICES",
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


def service_price_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("specialist_service_skip_price_btn", language),
                    callback_data="SPEC_SERVICE_PRICE_SKIP",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="SPEC_SERVICES",
                )
            ],
        ]
    )


def service_confirm_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("confirm", language),
                    callback_data="SPEC_SERVICE_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="SPEC_SERVICES",
                )
            ],
        ]
    )

def service_delete_confirm_keyboard(
    service_id: str,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("privacy_confirm_btn", language),
                    callback_data=f"SPEC_SERVICE_DELETE_CONFIRM:{service_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="SPEC_SERVICES",
                )
            ],
        ]
    )

def parse_service_price(value: str) -> tuple[float | None, float | None]:
    cleaned = (value or "").strip().replace(",", ".")
    if not cleaned:
        raise ValueError("empty")

    if "-" in cleaned:
        left, right = [part.strip() for part in cleaned.split("-", 1)]
        price_from = float(left)
        price_to = float(right)
    else:
        price_from = float(cleaned)
        price_to = None

    if price_from < 0 or (price_to is not None and price_to < 0):
        raise ValueError("negative")

    if price_to is not None and price_to < price_from:
        raise ValueError("range")

    return price_from, price_to


def service_preview_text(data: dict, language: str) -> str:
    price_from = data.get("service_price_from")
    price_to = data.get("service_price_to")
    currency = data.get("service_currency") or "EUR"

    if price_from is None and price_to is None:
        price = t("specialist_service_price_not_set", language)
    elif price_from is not None and price_to is not None:
        price = f"{float(price_from):.2f}-{float(price_to):.2f} {currency}"
    elif price_from is not None:
        price = f"{float(price_from):.2f} {currency}"
    else:
        price = f"{float(price_to):.2f} {currency}"

    return t("specialist_service_preview", language).format(
        title=data.get("service_title") or "-",
        description=data.get("service_description") or "-",
        price=price,
        currency=currency,
    )

@billing_router.callback_query(F.data == "SPEC_SERVICES")
@billing_router.callback_query(F.data.startswith("SPEC_SERVICES_PAGE:"))
async def specialist_services_entry(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    page = 0
    if callback.data and callback.data.startswith("SPEC_SERVICES_PAGE:"):
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        total, services = await SpecialistService(
            SpecialistRepository(session)
        ).list_service_items_page_for_viewer(
            tenant_id=tenant_id,
            user_id=user.id,
            specialist_id=specialist.id,
            page=page,
            page_size=SPECIALIST_SERVICES_PAGE_SIZE,
        )

    await state.update_data(
        specialist_service_ids=[str(item.id) for item in services],
        specialist_services_page=page,
    )

    await callback.message.answer(
        format_specialist_services_list(
            services,
            page=page,
            total=total,
            language=language,
        ),
        reply_markup=specialist_services_keyboard(
            services=services,
            page=page,
            total=total,
            language=language,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "SPEC_SERVICE_ADD")
async def add_specialist_service(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    await state.update_data(
        service_mode="create",
        service_specialist_id=str(specialist.id),
        service_tenant_id=str(tenant_id),
        service_user_id=str(user.id),
        service_category_id=str(specialist.category_id) if specialist.category_id else None,
        service_profession_id=str(specialist.profession_id) if specialist.profession_id else None,
        service_currency="EUR",
        service_price_from=None,
        service_price_to=None,
    )
    await state.set_state(SpecialistCabinetFSM.entering_service_title)

    await callback.message.answer(
        t("specialist_service_title_prompt", language),
        reply_markup=service_form_back_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("SPEC_SERVICE_EDIT:"))
async def edit_specialist_service(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    service_ids = data.get("specialist_service_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
        service_id = UUID(service_ids[index])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("specialist_service_not_found", language), show_alert=True)
        return

    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            service_data = await (
                SpecialistService(
                    SpecialistRepository(session)
                ).get_service_item_for_editing(
                    user_id=user.id,
                    specialist_id=specialist.id,
                    service_id=service_id,
                )
            )

    except SpecialistRegistrationError:
        await callback.answer(
            t(
                "specialist_service_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        service_mode="edit",
        service_id=str(
            service_data.service_id
        ),
        service_specialist_id=str(
            specialist.id
        ),
        service_tenant_id=str(
            tenant_id
        ),
        service_user_id=str(
            user.id
        ),
        service_category_id=(
            str(service_data.category_id)
            if service_data.category_id
            else None
        ),
        service_profession_id=(
            str(service_data.profession_id)
            if service_data.profession_id
            else None
        ),
        service_title=service_data.title,
        service_description=(
            service_data.description
        ),
        service_price_from=(
            service_data.price_from
        ),
        service_price_to=(
            service_data.price_to
        ),
        service_currency=(
            service_data.currency
        ),
    )

    await state.set_state(SpecialistCabinetFSM.entering_service_title)
    await callback.message.answer(
        t("specialist_service_title_prompt", language),
        reply_markup=service_form_back_keyboard(language),
    )
    await callback.answer()

@billing_router.message(SpecialistCabinetFSM.entering_service_title)
async def receive_service_title(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    title = (message.text or "").strip()

    if not title:
        await message.answer(t("specialist_service_title_required", language))
        return

    await state.update_data(service_title=title)
    await state.set_state(SpecialistCabinetFSM.entering_service_description)
    await message.answer(
        t("specialist_service_description_prompt", language),
        reply_markup=service_form_back_keyboard(language),
    )


@billing_router.message(SpecialistCabinetFSM.entering_service_description)
async def receive_service_description(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    description = (message.text or "").strip()

    if not description:
        await message.answer(t("specialist_service_description_required", language))
        return

    await state.update_data(service_description=description)
    await state.set_state(SpecialistCabinetFSM.entering_service_price)
    await message.answer(
        t("specialist_service_price_prompt", language),
        reply_markup=service_price_keyboard(language),
    )


@billing_router.callback_query(F.data == "SPEC_SERVICE_PRICE_SKIP")
async def skip_service_price(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await state.update_data(service_price_from=None, service_price_to=None)
    await state.set_state(SpecialistCabinetFSM.confirming_service)

    data = await state.get_data()
    await callback.message.answer(
        service_preview_text(data, language),
        reply_markup=service_confirm_keyboard(language),
    )
    await callback.answer()


@billing_router.message(SpecialistCabinetFSM.entering_service_price)
async def receive_service_price(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )

    try:
        price_from, price_to = parse_service_price(message.text or "")
    except (TypeError, ValueError):
        await message.answer(t("specialist_service_price_invalid", language))
        return

    await state.update_data(
        service_price_from=price_from,
        service_price_to=price_to,
    )
    await state.set_state(SpecialistCabinetFSM.confirming_service)

    data = await state.get_data()
    await message.answer(
        service_preview_text(data, language),
        reply_markup=service_confirm_keyboard(language),
    )


@billing_router.callback_query(F.data == "SPEC_SERVICE_CONFIRM")
async def confirm_specialist_service(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    tenant_id = data.get("service_tenant_id")
    user_id = data.get("service_user_id")
    specialist_id = data.get("service_specialist_id")
    title = (data.get("service_title") or "").strip()
    description = (data.get("service_description") or "").strip()

    if not tenant_id or not user_id or not specialist_id:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        await state.clear()
        return

    if not title:
        await callback.answer(t("specialist_service_title_required", language), show_alert=True)
        return

    if not description:
        await callback.answer(t("specialist_service_description_required", language), show_alert=True)
        return

    mode = data.get("service_mode") or "create"
    service_id = data.get("service_id") if mode == "edit" else None

    if mode == "edit" and not service_id:
        await callback.answer(t("specialist_service_not_found", language), show_alert=True)
        await state.clear()
        return

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).save_service_item(
                SpecialistServiceItemData(
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(user_id),
                    specialist_id=UUID(
                        specialist_id
                    ),
                    service_id=(
                        UUID(service_id)
                        if service_id
                        else None
                    ),
                    category_id=(
                        UUID(
                            data[
                                "service_category_id"
                            ]
                        )
                        if data.get(
                            "service_category_id"
                        )
                        else None
                    ),
                    profession_id=(
                        UUID(
                            data[
                                "service_profession_id"
                            ]
                        )
                        if data.get(
                            "service_profession_id"
                        )
                        else None
                    ),
                    title=title,
                    description=description,
                    price_from=data.get(
                        "service_price_from"
                    ),
                    price_to=data.get(
                        "service_price_to"
                    ),
                    currency=(
                        data.get("service_currency")
                        or "EUR"
                    ),
                )
            )
    except (SpecialistRegistrationError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await callback.message.answer(
        t("specialist_service_saved", language),
        reply_markup=specialist_services_keyboard(
            services=[],
            page=0,
            total=0,
            language=language,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("SPEC_SERVICE_PAUSE:"))
async def pause_specialist_service(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    service_ids = data.get("specialist_service_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
        service_id = UUID(service_ids[index])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("specialist_service_not_found", language), show_alert=True)
        return

    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).toggle_service_item_status(
                tenant_id=tenant_id,
                user_id=user.id,
                specialist_id=specialist.id,
                service_id=service_id,
            )
    except (SpecialistRegistrationError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(t("specialist_service_status_changed", language))
    await specialist_services_entry(callback, state)

@billing_router.callback_query(F.data.startswith("SPEC_SERVICE_DELETE:"))
async def ask_delete_specialist_service(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    service_ids = data.get("specialist_service_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
        service_id = service_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("specialist_service_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("specialist_service_delete_confirm", language),
        reply_markup=service_delete_confirm_keyboard(service_id, language),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("SPEC_SERVICE_DELETE_CONFIRM:"))
async def delete_specialist_service(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        service_id = UUID((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("specialist_service_not_found", language), show_alert=True)
        return

    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).delete_service_item(
                tenant_id=tenant_id,
                user_id=user.id,
                specialist_id=specialist.id,
                service_id=service_id,
            )
    except (SpecialistRegistrationError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(t("specialist_service_deleted", language))
    await specialist_services_entry(callback, state)

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
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        review_page = await ReviewService(
            ReviewRepository(session)
        ).list_public_reviews_for_viewer(
            tenant_id=tenant_id,
            specialist_id=specialist.id,
            viewer_user_id=user.id,
            page=page,
            page_size=(
                SPECIALIST_REVIEWS_PAGE_SIZE
            ),
            source="specialist_cabinet",
        )

    await state.update_data(
        specialist_review_ids=[str(review.id) for review in review_page.reviews],
        specialist_reviews_page=review_page.page,
    )

    await callback.message.answer(
        format_specialist_reviews_cabinet(review_page, language),
        reply_markup=specialist_reviews_keyboard(
            language=language,
            page=review_page.page,
            has_previous=review_page.has_previous,
            has_next=review_page.has_next,
            reviews_count=len(review_page.reviews),
        ),
    )
    await callback.answer()


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

    await callback.message.answer(
        t("complaint_reason_prompt", language),
        reply_markup=complaint_reason_keyboard(language),
    )
    await callback.answer()

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


@billing_router.callback_query(F.data == "SPEC_SETTINGS")
async def specialist_settings_entry(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.message.answer(
        t("specialist_settings_title", language),
        reply_markup=specialist_settings_keyboard(language),
    )
    await callback.answer()

def specialist_language_settings_keyboard(
    *,
    language: str,
    message_language: str,
    show_original_button: bool,
) -> InlineKeyboardMarkup:
    original_text = (
        t("settings_show_original_on", language)
        if show_original_button
        else t("settings_show_original_off", language)
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("settings_interface_language_label", language),
                    callback_data="SET_NOOP",
                )
            ],
            [
                InlineKeyboardButton(text="RU", callback_data="SPEC_SET_UI_LANG:ru"),
                InlineKeyboardButton(text="EN", callback_data="SPEC_SET_UI_LANG:en"),
                InlineKeyboardButton(text="PT", callback_data="SPEC_SET_UI_LANG:pt"),
            ],
            [
                InlineKeyboardButton(
                    text=t("settings_message_language_label", language),
                    callback_data="SET_NOOP",
                )
            ],
            [
                InlineKeyboardButton(text="RU", callback_data="SPEC_SET_MSG_LANG:ru"),
                InlineKeyboardButton(text="EN", callback_data="SPEC_SET_MSG_LANG:en"),
                InlineKeyboardButton(text="PT", callback_data="SPEC_SET_MSG_LANG:pt"),
            ],
            [
                InlineKeyboardButton(
                    text=original_text,
                    callback_data="SPEC_SET_SHOW_ORIGINAL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="SPEC_SETTINGS",
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


async def render_specialist_language_settings(callback: CallbackQuery) -> None:
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        settings = await TranslationService(
            TranslationRepository(session)
        ).get_language_settings_view(
            user_id=user.id,
        )
        language = normalize_language(
            settings.interface_language
            or user.language_code
        )

    await callback.message.answer(
        t("specialist_language_settings_title", language).format(
            interface_language=settings.interface_language,
            message_language=settings.message_language,
            notifications=t("settings_enabled", language),
            auto_translate=t("feature_disabled_beta", language),
            show_original=t(
                "settings_enabled" if settings.show_original_button else "settings_disabled",
                language,
            ),
        ),
        reply_markup=specialist_language_settings_keyboard(
            language=language,
            message_language=settings.message_language,
            show_original_button=settings.show_original_button,
        ),
    )
    await callback.answer()

@billing_router.callback_query(
    F.data.startswith("SPEC_SET_UI_LANG:")
)
async def set_specialist_interface_language(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    interface_language = normalize_language(
        (callback.data or "").split(":", 1)[1]
    )

    async with get_session() as session:
        user = await UserService(
            session
        ).get_user_by_telegram_id(
            callback.from_user.id
        )

        if not user:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    fallback_language,
                ),
                show_alert=True,
            )
            return

        await TranslationService(
            TranslationRepository(session)
        ).update_interface_language(
            tenant_id=user.tenant_id,
            user_id=user.id,
            language_code=interface_language,
            source="specialist_settings",
        )

    await render_specialist_language_settings(
        callback
    )

@billing_router.callback_query(
    F.data.startswith("SPEC_SET_MSG_LANG:")
)
async def set_specialist_message_language(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    message_language = (
        (callback.data or "").split(":", 1)[1]
    )

    async with get_session() as session:
        user = await UserService(
            session
        ).get_user_by_telegram_id(
            callback.from_user.id
        )

        if not user:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    language,
                ),
                show_alert=True,
            )
            return

        await TranslationService(
            TranslationRepository(session)
        ).update_message_language(
            tenant_id=user.tenant_id,
            user_id=user.id,
            language_code=message_language,
            source="specialist_settings",
        )

    await render_specialist_language_settings(
        callback
    )

@billing_router.callback_query(
    F.data == "SPEC_SET_SHOW_ORIGINAL"
)
async def toggle_specialist_show_original(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    async with get_session() as session:
        user = await UserService(
            session
        ).get_user_by_telegram_id(
            callback.from_user.id
        )

        if not user:
            await callback.answer(
                t(
                    "search_contact_user_not_found",
                    language,
                ),
                show_alert=True,
            )
            return

        await TranslationService(
            TranslationRepository(session)
        ).toggle_show_original(
            tenant_id=user.tenant_id,
            user_id=user.id,
            source="specialist_settings",
        )

    await render_specialist_language_settings(
        callback
    )

@billing_router.callback_query(F.data == "SPEC_SETTINGS_LANGUAGE")
async def specialist_settings_language(callback: CallbackQuery, state: FSMContext):
    await render_specialist_language_settings(callback)
@billing_router.callback_query(F.data == "SPEC_SETTINGS_NOTIFICATIONS")
async def specialist_settings_notifications(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.message.answer(
        t("specialist_notifications_settings", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("billing_back", language),
                        callback_data="SPEC_SETTINGS",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("search_menu", language),
                        callback_data="BILL_MENU",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "SPEC_SETTINGS_CONSENTS")
async def specialist_settings_consents(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    async with get_session() as session:
        consents = await LegalService(
            LegalRepository(session)
        ).list_user_consent_views(
            tenant_id=tenant_id,
            user_id=user_id,
        )

    if consents:
        lines = [t("settings_consents_title", language), ""]
        for index, consent in enumerate(consents, start=1):
            status = (
                t(
                    "settings_consent_revoked",
                    language,
                )
                if consent.is_revoked
                else t(
                    "settings_consent_active",
                    language,
                )
            )
            lines.append(
                t("settings_consent_item", language).format(
                    number=index,
                    consent_type=consent.consent_type,
                    version=consent.version,
                    status=status,
                )
            )
    else:
        lines = [t("settings_consents_empty", language)]

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("billing_back", language),
                        callback_data="SPEC_SETTINGS",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("search_menu", language),
                        callback_data="BILL_MENU",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "M_CABINET")
async def billing_open_current_role_cabinet(callback: CallbackQuery, state: FSMContext):
    await open_current_role_cabinet(callback, state)

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

@billing_router.callback_query(F.data == "CAB_USER_PROFILE")
async def show_client_user_profile(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    async with get_session() as session:
        profile = await UserService(session).get_client_profile(
            telegram_id=callback.from_user.id,
            language=language,
        )

    if not profile:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    await callback.message.answer(
        format_client_user_profile(profile, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("back", language),
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
        ),
    )
    await callback.answer()

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

    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    async with get_session() as session:
        cabinet_context = await UserService(
            session
        ).open_client_cabinet(
            telegram_id=callback.from_user.id,
            language=language,
        )

    if not cabinet_context:
        await callback.message.answer(
            t(
                "billing_start_required",
                language,
            )
        )
        return

    show_role_switch = (
        cabinet_context.show_role_switch
    )
    show_specialist_registration = (
        cabinet_context
        .show_specialist_registration
    )

    await state.clear()
    await callback.message.answer(
        t("client_cabinet_title", language)
        + "\n\n"
        + t("client_cabinet_summary", language),
        reply_markup=client_cabinet_keyboard(
            language,
            show_role_switch=show_role_switch,
            show_specialist_registration=show_specialist_registration,
        ),
    )

@billing_router.callback_query(F.data.in_({"CAB_CRM_STUB", "CAB_FINANCE_STUB"}))
async def show_cabinet_stub(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    text_key = (
        "cabinet_crm_stub"
        if callback.data == "CAB_CRM_STUB"
        else "cabinet_finance_stub"
    )

    await callback.message.answer(
        t(text_key, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                    text=t("menu_my_cabinet", language),
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
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "SPEC_MODERATION")
async def show_specialist_moderation(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not specialist:
        await callback.answer(t("specialist_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        format_specialist_moderation_text(specialist, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("cabinet_specialist_btn", language),
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
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_FAVORITES")
@billing_router.callback_query(F.data.startswith("CAB_FAVORITES:"))
async def show_favorites(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    callback_answered: bool = False,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    page = 0
    if callback.data and callback.data.startswith("CAB_FAVORITES:"):
        parts = callback.data.split(":")
        if len(parts) >= 2 and parts[1].isdigit():
            page = int(parts[1])

    user_id, tenant_id = await get_billing_user_context(
        callback.from_user.id
    )

    if not user_id or not tenant_id:
        await callback.answer(
            t("billing_start_required", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        favorites_page = await FavoriteService(
            FavoriteRepository(session)
        ).list_public_cards_page(
            tenant_id=tenant_id,
            user_id=user_id,
            page=page,
            page_size=FAVORITES_PAGE_SIZE,
            language=language,
        )

    cards = favorites_page.cards
    has_next = favorites_page.has_next
    page = favorites_page.page

    specialist_ids = [
        str(card.specialist_id)
        for card in cards
    ]

    await state.update_data(
        user_language=language,
        cabinet_favorite_ids=specialist_ids,
        cabinet_favorites_page=page,
        result_specialist_ids=specialist_ids,
        result_distances=[None] * len(specialist_ids),
        results_page=0,
        profession_id=None,
    )

    if not cards:
        await callback.message.answer(
            t("favorites_empty", language),
            reply_markup=favorites_list_keyboard(
                language,
                page=page,
                has_next=False,
            ),
        )
        if not callback_answered:
            await callback.answer()

        return

    await callback.message.answer(
        (
            f"{t('favorites_title', language)}\n"
            f"{t('favorites_hint', language)}"
        )
    )

    for index, card in enumerate(cards):
        await callback.message.answer(
            format_favorite_card(card, language),
            reply_markup=favorite_list_card_keyboard(
                index,
                language,
            ),
        )

    await callback.message.answer(
        t("favorites_navigation", language),
        reply_markup=favorites_list_keyboard(
            language,
            page=page,
            has_next=has_next,
        ),
    )
    if not callback_answered:
        await callback.answer()

@billing_router.callback_query(F.data.startswith("CAB_FAV_VIEW:"))
async def show_favorite_card(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    ids = data.get("cabinet_favorite_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer()
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    specialist_id = ids[index]

    async with get_session() as session:
        card = await FavoriteService(
            FavoriteRepository(session)
        ).get_saved_public_card(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=UUID(
                specialist_id
            ),
            language=language,
        )

    if not card:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await state.update_data(
        selected_specialist_id=specialist_id,
        selected_specialist_distance=None,
        results_page=0,
        user_language=language,
    )

    await callback.message.answer(
        format_favorite_card(card, language),
        reply_markup=favorite_card_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_FAV_REMOVE")
async def remove_favorite_from_cabinet(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    page = int(data.get("cabinet_favorites_page") or 0)
    specialist_id = data.get("selected_specialist_id")

    if not specialist_id:
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    async with get_session() as session:
        removed = await FavoriteService(
            FavoriteRepository(session)
        ).remove_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=UUID(
                specialist_id
            ),
            source="favorites",
        )

    text_key = "favorite_removed" if removed else "favorites_not_found"
    await callback.answer(t(text_key, language), show_alert=True)

    await state.update_data(
        selected_specialist_id=None,
    )

    callback.data = (
        f"CAB_FAVORITES:{page}"
    )

    await show_favorites(
        callback,
        state,
        callback_answered=True,
    )


@billing_router.callback_query(F.data.in_({"CAB_PROFILE", "SPEC_PUBLIC_PROFILE"}))
async def show_specialist_profile_menu(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    user, specialist, tenant_id = await get_current_specialist_for_telegram(callback.from_user.id)

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    location_text = await get_specialist_location_text(specialist, language)
    profession_text = "-"

    if specialist:
        async with get_session() as session:
            profession_names = await SpecialistService(
                SpecialistRepository(session)
            ).list_profile_profession_names(
                specialist_id=specialist.id,
                language=language,
            )

        if profession_names:
            profession_text = ", ".join(profession_names)

    await callback.message.answer(
        format_specialist_profile_text(
            specialist,
            language,
            location_text,
            profession_text,
        ),
        reply_markup=specialist_public_profile_preview_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "SPEC_CARD_FULL")
async def show_specialist_card_full_description(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, _ = (
        await get_current_specialist_for_telegram(
            callback.from_user.id,
        )
    )

    if not user or not specialist:
        await callback.answer(
            t("cabinet_profile_not_found", language),
            show_alert=True,
        )
        return

    description = (
        specialist.full_description
        or specialist.short_description
        or ""
    ).strip()

    if not description:
        await callback.message.answer(
            t("specialist_card_full_empty", language),
            reply_markup=specialist_public_profile_preview_keyboard(
                language,
            ),
        )
        await callback.answer()
        return

    await callback.message.answer(
        (
            f"{t('specialist_card_full_title', language)}\n\n"
            f"{description[:3800]}"
        ),
        reply_markup=specialist_public_profile_preview_keyboard(
            language,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_PORTFOLIO")
async def show_owner_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    user_id, tenant_id = await get_billing_user_context(
        callback.from_user.id
    )

    if not user_id or not tenant_id:
        await callback.answer(
            t("billing_start_required", language),
            show_alert=True,
        )
        return

    try:
        await send_owner_portfolio(
            callback.message,
            tenant_id=tenant_id,
            owner_user_id=user_id,
            language=language,
            page=0,
        )
    except PortfolioServiceError as exc:
        await callback.answer(
            t("portfolio_error", language).format(error=str(exc)),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await callback.answer()

@billing_router.callback_query(F.data.startswith("CAB_PORTFOLIO_PAGE:"))
async def show_owner_portfolio_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        page = max(0, int((callback.data or "").split(":", 1)[1]))
    except (IndexError, TypeError, ValueError):
        page = 0

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        await send_owner_portfolio(
            callback.message,
            tenant_id=tenant_id,
            owner_user_id=user_id,
            language=language,
            page=page,
        )
    except PortfolioServiceError as exc:
        await callback.answer(
            t("portfolio_error", language).format(error=str(exc)),
            show_alert=True,
        )
        return

    await callback.answer()

def portfolio_caption_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("portfolio_caption_skip_btn", language),
                    callback_data="CAB_PORTFOLIO_CAPTION_SKIP",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="CAB_PORTFOLIO",
                )
            ],
        ]
    )


def portfolio_upload_confirm_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("portfolio_upload_confirm_btn", language),
                    callback_data="CAB_PORTFOLIO_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("privacy_cancel_btn", language),
                    callback_data="CAB_PORTFOLIO",
                )
            ],
        ]
    )


def portfolio_upload_preview_text(data: dict, language: str) -> str:
    caption = (data.get("portfolio_caption") or "").strip() or "-"
    size_bytes = int(data.get("portfolio_size_bytes") or 0)
    size_kb = max(1, round(size_bytes / 1024))

    return t("portfolio_upload_preview", language).format(
        filename=data.get("portfolio_filename") or "-",
        file_type=data.get("portfolio_mime_type") or "-",
        size_kb=size_kb,
        caption=caption,
    )

@billing_router.callback_query(F.data == "CAB_PORTFOLIO_UPLOAD")
async def ask_portfolio_upload(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.message.answer(
        t("portfolio_upload_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("billing_back", language),
                        callback_data="CAB_PORTFOLIO",
                    )
                ]
            ]
        ),
    )

    await state.set_state(
        SpecialistCabinetFSM.waiting_portfolio_file
    )
    await callback.answer()


@billing_router.message(
    SpecialistCabinetFSM.waiting_portfolio_file,
    F.photo | F.document,
)
async def receive_portfolio_file(
    message: Message,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )

    user_id, tenant_id = await get_billing_user_context(
        message.from_user.id
    )

    if not user_id or not tenant_id:
        await message.answer(
            t("billing_start_required", language)
        )
        return

    buffer = BytesIO()

    if message.document:
        telegram_file = message.document
        filename = (
            telegram_file.file_name
            or f"{telegram_file.file_unique_id}.bin"
        )
        mime_type = telegram_file.mime_type
    else:
        telegram_file = message.photo[-1]
        filename = f"{telegram_file.file_unique_id}.jpg"
        mime_type = "image/jpeg"

    try:
        await message.bot.download(
            telegram_file,
            destination=buffer,
        )
    except Exception as exc:
        await message.answer(
            t("portfolio_upload_error", language).format(error=str(exc))
        )
        return

    content = buffer.getvalue()

    await state.update_data(
        portfolio_tenant_id=str(tenant_id),
        portfolio_owner_user_id=str(user_id),
        portfolio_filename=filename,
        portfolio_mime_type=mime_type,
        portfolio_content=content,
        portfolio_size_bytes=len(content),
    )
    await state.set_state(SpecialistCabinetFSM.entering_portfolio_caption)

    await message.answer(
        t("portfolio_caption_prompt", language),
        reply_markup=portfolio_caption_keyboard(language),
    )

@billing_router.message(SpecialistCabinetFSM.entering_portfolio_caption)
async def receive_portfolio_caption(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    caption = (message.text or "").strip()

    await state.update_data(portfolio_caption=caption)
    await state.set_state(SpecialistCabinetFSM.confirming_portfolio_upload)

    data = await state.get_data()
    await message.answer(
        portfolio_upload_preview_text(data, language),
        reply_markup=portfolio_upload_confirm_keyboard(language),
    )


@billing_router.callback_query(F.data == "CAB_PORTFOLIO_CAPTION_SKIP")
async def skip_portfolio_caption(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await state.update_data(portfolio_caption="")
    await state.set_state(SpecialistCabinetFSM.confirming_portfolio_upload)

    data = await state.get_data()
    await callback.message.answer(
        portfolio_upload_preview_text(data, language),
        reply_markup=portfolio_upload_confirm_keyboard(language),
    )
    await callback.answer()

@billing_router.message(
    SpecialistCabinetFSM.waiting_portfolio_file,
)
async def reject_invalid_portfolio_message(
    message: Message,
):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )

    await message.answer(
        t("portfolio_invalid_file", language)
    )


@billing_router.callback_query(
    F.data.startswith("CAB_PORT_DEL:")
)
async def delete_owner_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    user_id, tenant_id = await get_billing_user_context(
        callback.from_user.id
    )

    if not user_id or not tenant_id:
        await callback.answer(
            t("billing_start_required", language),
            show_alert=True,
        )
        return

    try:
        item_id = UUID(callback.data.split(":", 1)[1])

        async with get_session() as session:
            service = PortfolioService(
                PortfolioRepository(session)
            )
            await service.delete_owner_item(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                item_id=item_id,
            )

    except (ValueError, PortfolioServiceError) as exc:
        await callback.answer(
            t("portfolio_error", language).format(error=str(exc)),
            show_alert=True,
        )
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )
    await callback.answer(
        t("portfolio_deleted", language),
        show_alert=True,
    )
    await state.set_state(None)

@billing_router.callback_query(F.data == "CAB_PORTFOLIO_CONFIRM")
async def confirm_portfolio_upload(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    tenant_id = data.get("portfolio_tenant_id")
    owner_user_id = data.get("portfolio_owner_user_id")
    filename = data.get("portfolio_filename")
    mime_type = data.get("portfolio_mime_type")
    content = data.get("portfolio_content")
    caption = (data.get("portfolio_caption") or "").strip()

    if not tenant_id or not owner_user_id or not filename or not content:
        await callback.answer(t("portfolio_invalid_file", language), show_alert=True)
        await state.clear()
        return

    try:
        async with get_session() as session:
            service = PortfolioService(
                PortfolioRepository(session)
            )
            await service.upload_item(
                tenant_id=UUID(tenant_id),
                owner_user_id=UUID(owner_user_id),
                filename=filename,
                mime_type=mime_type,
                content=content,
                title=caption or filename,
                description=caption or None,
            )

        await state.clear()

        await callback.message.answer(
            t("portfolio_upload_success", language)
        )

        await send_owner_portfolio(
            callback.message,
            tenant_id=UUID(tenant_id),
            owner_user_id=UUID(owner_user_id),
            language=language,
            page=0,
        )

    except PortfolioServiceError as exc:
        await callback.message.answer(
            t("portfolio_upload_error", language).format(
                error=str(exc)
            )
        )

    await callback.answer()

@billing_router.callback_query(F.data == "CAB_PROFILE_VIEW")
async def view_specialist_profile(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    user, specialist, tenant_id = await get_current_specialist_for_telegram(callback.from_user.id)

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    location_text = await get_specialist_location_text(specialist, language)
    profession_text = "-"

    if specialist:
        async with get_session() as session:
            card = await GeoSearchService(
                SpecialistSearchRepository(session)
            ).get_public_card(
                specialist_id=specialist.id,
                language=language,
            )
            if card and card.profession_name:
                profession_text = card.profession_name

    await callback.message.answer(
        format_specialist_profile_text(
            specialist,
            language,
            location_text,
            profession_text,
        ),
        reply_markup=specialist_profile_keyboard(language),
    )
    await callback.answer()

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

@billing_router.callback_query(F.data == "CAB_PROFILE_VISIBILITY")
async def show_specialist_profile_visibility(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        current_visibility = await SpecialistService(
            SpecialistRepository(session)
        ).get_profile_visibility(
            user_id=user.id,
        )

    await callback.message.answer(
        specialist_profile_publication_notice(
            status=specialist.status,
            visibility=current_visibility,
            language=language,
        ),
        reply_markup=profile_status_visibility_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("CAB_PROFILE_VISIBILITY_SET:"))
async def set_specialist_profile_visibility(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    visibility = (callback.data or "").split(":", 1)[1]

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).update_profile_visibility(
                tenant_id=tenant_id,
                user_id=user.id,
                specialist_id=specialist.id,
                visibility=visibility,
            )

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("cabinet_visibility_updated", language).format(
            visibility=profile_visibility_label(visibility, language),
        ),
    )
    await callback.message.answer(
        specialist_profile_publication_notice(
            status=specialist.status,
            visibility=visibility,
            language=language,
        ),
        reply_markup=profile_status_visibility_keyboard(language),
    )
    await callback.answer()

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
@billing_router.callback_query(F.data == "CAB_PROFILE_DELETE")
async def confirm_specialist_profile_delete(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("privacy_confirm_delete_profile", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("privacy_confirm_btn", language),
                        callback_data="CAB_PROFILE_DELETE_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("privacy_cancel_btn", language),
                        callback_data="SPEC_SETTINGS"
                    )
                ],
            ]
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_PROFILE_DELETE_CONFIRM")
async def schedule_specialist_profile_delete(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        await PrivacyService(
            PrivacyRepository(session)
        ).schedule_profile_deletion(
            tenant_id=tenant_id,
            user_id=user.id,
            specialist_id=specialist.id,
            source="specialist_cabinet",
        )

    await callback.message.answer(t("privacy_deletion_scheduled", language))
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_PROFILE_EDIT")
async def edit_specialist_profile_menu(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    user, specialist, tenant_id = await get_current_specialist_for_telegram(callback.from_user.id)

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    await state.update_data(
        cabinet_specialist_id=str(specialist.id),
        cabinet_tenant_id=str(tenant_id),
        cabinet_user_id=str(user.id),
    )
    await callback.message.answer(
        t("cabinet_edit_profile", language),
        reply_markup=specialist_edit_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_EDIT_NAME")
async def ask_edit_specialist_name(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_enter_name", language),
        reply_markup=profile_edit_back_keyboard(language),
    )
    await state.set_state(SpecialistCabinetFSM.entering_display_name)
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_EDIT_DESCRIPTION")
async def ask_edit_specialist_description(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_enter_description", language),
        reply_markup=profile_edit_back_keyboard(language),
    )
    await state.set_state(SpecialistCabinetFSM.entering_description)
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_EDIT_CONTACT")
async def ask_edit_specialist_contact(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_enter_contact", language),
        reply_markup=profile_edit_back_keyboard(language),
    )
    await state.set_state(SpecialistCabinetFSM.entering_contact)
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_EDIT_WORK_FORMAT")
async def ask_edit_specialist_work_format(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        t("spec_work_format_prompt", language),
        reply_markup=profile_work_format_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("CAB_WORK_FORMAT_SET:"))
async def set_edit_specialist_work_format(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    work_format = (callback.data or "").split(":", 1)[1]

    try:
        async with get_session() as session:
            _, _, _, changed = (
                await SpecialistService(
                    SpecialistRepository(session)
                ).update_work_format(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    specialist_id=specialist.id,
                    work_format=work_format,
                )
            )

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if not changed:
        await callback.message.answer(
            t(
                "cabinet_profile_no_changes",
                language,
            ),
            reply_markup=(
                location_and_format_keyboard(
                    language
                )
            ),
        )
        await callback.answer()
        return

    await callback.message.answer(
        t("cabinet_profile_updated", language),
        reply_markup=location_and_format_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_EDIT_LANGUAGES")
async def ask_edit_specialist_languages(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            selected = await (
                SpecialistService(
                    SpecialistRepository(session)
                ).get_languages_for_editing(
                    user_id=user.id,
                    specialist_id=specialist.id,
                )
            )

    except SpecialistRegistrationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        cabinet_specialist_id=str(specialist.id),
        cabinet_user_id=str(user.id),
        cabinet_tenant_id=str(tenant_id),
        cabinet_selected_languages=selected,
    )

    await callback.message.answer(
        format_profile_languages_text(
            selected,
            language,
        ),
        reply_markup=profile_languages_keyboard(
            selected,
            language,
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("CAB_LANG_TOGGLE:"))
async def toggle_specialist_language(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    code = (
        (callback.data or "").split(
            ":",
            1,
        )[1]
    )

    try:
        selected = (
            SpecialistService
            .toggle_language_selection(
                selected_codes=list(
                    data.get(
                        "cabinet_selected_languages"
                    )
                    or ["ru"]
                ),
                language_code=code,
            )
        )

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

    await state.update_data(cabinet_selected_languages=selected)

    await callback.message.edit_text(
        format_profile_languages_text(
            selected,
            language,
        ),
        reply_markup=profile_languages_keyboard(
            selected,
            language,
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_LANG_DONE")
async def save_specialist_languages(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    specialist_id = data.get("cabinet_specialist_id")
    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    selected = list(data.get("cabinet_selected_languages") or [])

    if not specialist_id or not user_id or not tenant_id:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        await state.clear()
        return

    try:
        async with get_session() as session:
            _, _, changed = await SpecialistService(
                SpecialistRepository(session)
            ).update_languages(
                tenant_id=UUID(
                    tenant_id
                ),
                user_id=UUID(
                    user_id
                ),
                specialist_id=UUID(
                    specialist_id
                ),
                language_codes=selected,
            )

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if not changed:
        await state.set_state(None)
        await callback.message.answer(
            t(
                "cabinet_profile_no_changes",
                language,
            ),
            reply_markup=(
                specialist_edit_keyboard(
                    language
                )
            ),
        )
        await callback.answer()
        return

    await state.set_state(None)
    await callback.message.answer(
        t("cabinet_profile_updated", language),
        reply_markup=specialist_edit_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "SPEC_SKILLS")
async def show_specialist_skills(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            edit_data = await (
                SpecialistService(
                    SpecialistRepository(session)
                ).get_skills_for_editing(
                    user_id=user.id,
                    specialist_id=specialist.id,
                    language=language,
                    limit=30,
                )
            )

    except SpecialistRegistrationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    skills = list(edit_data.skills)
    selected_ids = [
        str(item)
        for item in edit_data.selected_ids
    ]

    await state.update_data(
        cabinet_specialist_id=str(specialist.id),
        cabinet_user_id=str(user.id),
        cabinet_tenant_id=str(tenant_id),
        cabinet_skill_ids=[str(skill.id) for skill in skills],
        cabinet_selected_skill_ids=selected_ids,
    )

    await callback.message.answer(
        format_profile_skills_text(skills, selected_ids, language),
        reply_markup=profile_skills_keyboard(
            skills=skills,
            selected_ids=selected_ids,
            language=language,
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("CAB_SKILL_TOGGLE:"))
async def toggle_specialist_skill(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    skill_ids = list(data.get("cabinet_skill_ids") or [])
    selected_ids = list(data.get("cabinet_selected_skill_ids") or [])

    if index < 0 or index >= len(skill_ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    skill_id = skill_ids[index]

    if skill_id in selected_ids:
        selected_ids = [item for item in selected_ids if item != skill_id]
    else:
        selected_ids.append(skill_id)

    await state.update_data(cabinet_selected_skill_ids=selected_ids)

    specialist_id = data.get(
        "cabinet_specialist_id"
    )
    user_id = data.get(
        "cabinet_user_id"
    )

    if not specialist_id or not user_id:
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
            edit_data = await (
                SpecialistService(
                    SpecialistRepository(session)
                ).get_skills_for_editing(
                    user_id=UUID(user_id),
                    specialist_id=UUID(
                        specialist_id
                    ),
                    language=language,
                    limit=30,
                )
            )

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    skills = list(edit_data.skills)

    await callback.message.answer(
        format_profile_skills_text(skills, selected_ids, language),
        reply_markup=profile_skills_keyboard(
            skills=skills,
            selected_ids=selected_ids,
            language=language,
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_SKILLS_DONE")
async def save_specialist_skills(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    specialist_id = data.get("cabinet_specialist_id")
    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    selected_ids = list(data.get("cabinet_selected_skill_ids") or [])

    if not specialist_id or not user_id or not tenant_id:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        await state.clear()
        return

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).update_skills(
                tenant_id=UUID(
                    tenant_id
                ),
                user_id=UUID(
                    user_id
                ),
                specialist_id=UUID(
                    specialist_id
                ),
                skill_ids=[
                    UUID(item)
                    for item in selected_ids
                ],
            )

    except (
        SpecialistRegistrationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await callback.message.answer(
        t("spec_skills_saved", language),
        reply_markup=specialist_edit_keyboard(language),
    )
    await callback.answer()

async def block_critical_profile_edit(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    field: str,
    language: str,
) -> None:
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        callback.from_user.id
    )

    if user and specialist and tenant_id:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).record_blocked_profile_change(
                tenant_id=tenant_id,
                user_id=user.id,
                specialist_id=specialist.id,
                field=field,
            )

    await state.clear()
    await callback.message.answer(
        t("cabinet_critical_edit_blocked", language),
        reply_markup=specialist_edit_keyboard(language),
    )
    await callback.answer()


async def block_critical_profile_edit_message(
    message: Message,
    state: FSMContext,
    *,
    field: str,
    language: str,
) -> None:
    user, specialist, tenant_id = await get_current_specialist_for_telegram(
        message.from_user.id
    )

    if user and specialist and tenant_id:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).record_blocked_profile_change(
                tenant_id=tenant_id,
                user_id=user.id,
                specialist_id=specialist.id,
                field=field,
                source="stale_fsm_state",
            )

    await state.clear()
    await message.answer(
        t("cabinet_critical_edit_blocked", language),
        reply_markup=specialist_edit_keyboard(language),
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

    await callback.message.answer(
        (
            f"{t('specialist_location_work_title', language)}\n"
            f"{t('specialist_location_work_hint', language)}"
        ),
        reply_markup=location_and_format_keyboard(
            language,
        ),
    )
    await callback.answer()


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

@billing_router.callback_query(F.data == "CAB_LOC_MANUAL")
async def ask_edit_specialist_location_manual(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_location_query_prompt", language),
        reply_markup=profile_edit_back_keyboard(language),
    )
    await state.set_state(SpecialistCabinetFSM.entering_location_query)
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_LOC_COUNTRY")
async def ask_edit_specialist_location_country(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await callback.message.answer(
        t("spec_country_search_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("billing_back", language),
                        callback_data="CAB_EDIT_LOCATION",
                    )
                ]
            ]
        ),
    )
    await state.set_state(SpecialistCabinetFSM.entering_country_query)
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_LOC_GEO")
async def ask_edit_specialist_location_geo(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_geo_required", language),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=t("cabinet_send_geo_btn", language),
                        request_location=True,
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await state.set_state(SpecialistCabinetFSM.waiting_geo)
    await callback.answer()

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
async def choose_specialist_location_update(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()
    candidates = data.get("cabinet_geo_candidates") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
        candidate = candidates[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    specialist_id = data.get("cabinet_specialist_id")

    if not user_id or not tenant_id or not specialist_id:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        await state.clear()
        return

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).update_location_from_candidate(
                tenant_id=UUID(
                    tenant_id
                ),
                user_id=UUID(
                    user_id
                ),
                specialist_id=UUID(
                    specialist_id
                ),
                candidate=candidate,
                service_radius_km=25,
            )

    except RateLimitError as exc:
        await callback.answer(t("error_rate_limited", language), show_alert=True)
        return
    except (GeoServiceError, SpecialistRegistrationError) as exc:
        await callback.answer(
            t("cabinet_profile_update_failed", language).format(error=str(exc)),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await callback.message.answer(
        t("cabinet_location_updated", language),
        reply_markup=specialist_edit_keyboard(language),
    )
    await callback.answer()

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
    language = (
        await get_billing_interface_language(
            callback.from_user.id,
            callback.from_user.language_code,
        )
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

    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    specialist_id = data.get(
        "cabinet_specialist_id"
    )

    if (
        not user_id
        or not tenant_id
        or not specialist_id
    ):
        await callback.answer(
            t(
                "cabinet_profile_not_found",
                language,
            ),
            show_alert=True,
        )
        await state.clear()
        return

    try:
        async with get_session() as session:
            await SpecialistService(
                SpecialistRepository(session)
            ).update_country_from_candidate(
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
                specialist_id=UUID(
                    specialist_id
                ),
                candidate=candidate,
            )

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

    await state.set_state(None)
    await callback.message.answer(
        t(
            "cabinet_location_updated",
            language,
        ),
        reply_markup=specialist_edit_keyboard(
            language
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_EDIT_CATEGORY")
async def ask_edit_specialist_category(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    user, specialist, tenant_id = (
        await get_current_specialist_for_telegram(
            callback.from_user.id,
        )
    )

    if not user:
        await callback.answer(
            t("billing_start_required", language),
            show_alert=True,
        )
        return

    if not specialist:
        await callback.answer(
            t("cabinet_profile_not_found", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        service = SpecialistService(
            SpecialistRepository(session),
        )
        categories = (
            await service.list_active_categories_for_profile_editor(
                limit=50,
            )
        )
        selected_professions = (
            await service.get_profile_profession_selections(
                specialist_id=specialist.id,
                language=language,
            )
        )

    selected_profession_ids = [
        item["profession_id"]
        for item in selected_professions
    ]

    await state.update_data(
        cabinet_specialist_id=str(specialist.id),
        cabinet_tenant_id=str(tenant_id),
        cabinet_user_id=str(user.id),
        cabinet_category_ids=[
            str(item.id)
            for item in categories
        ],
        cabinet_selected_profession_ids=selected_profession_ids,
        cabinet_selected_professions=selected_professions,
        cabinet_categories_page=0,
    )
    await state.set_state(
        SpecialistCabinetFSM.choosing_category,
    )

    await callback.message.answer(
        cabinet_category_prompt_text(
            selected_professions,
            language,
        ),
        reply_markup=cabinet_category_keyboard(
            items=categories,
            selected_professions=selected_professions,
            language=language,
            page=0,
        ),
    )
    await callback.answer()

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
            int((callback.data or "").split(":", 1)[1]),
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        service = SpecialistService(
            SpecialistRepository(session),
        )
        categories = await service.list_active_categories_for_profile_editor(
            limit=50,
        )

    data = await state.get_data()
    selected_professions = (
        data.get("cabinet_selected_professions") or []
    )

    await state.update_data(
        cabinet_categories_page=page,
        cabinet_category_ids=[
            str(item.id)
            for item in categories
        ],
    )

    await callback.message.edit_reply_markup(
        reply_markup=cabinet_category_keyboard(
            items=categories,
            selected_professions=selected_professions,
            language=language,
            page=page,
        )
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("CAB_CAT:"))
async def choose_specialist_category_update(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()
    category_ids = data.get("cabinet_category_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
        category_id = category_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        repository = SpecialistRepository(session)
        category = await repository.get_active_category(UUID(category_id))
        professions = await repository.list_active_professions_by_category(
            UUID(category_id),
            limit=50,
        )

    selected_profession_ids = data.get("cabinet_selected_profession_ids") or []
    selected_professions = data.get("cabinet_selected_professions") or []

    await state.update_data(
        cabinet_pending_category_id=category_id,
        cabinet_pending_category_name=localized_name(category, language) if category else None,
        cabinet_profession_ids=[
            str(item.id)
            for item in professions
        ],
        cabinet_professions_page=0,
    )
    await state.set_state(SpecialistCabinetFSM.choosing_profession)

    await callback.message.answer(
        cabinet_profession_prompt_text(selected_professions, language),
        reply_markup=cabinet_profession_multi_keyboard(
            items=professions,
            selected_ids=selected_profession_ids,
            language=language,
            page=0,
        ),
    )
    await callback.answer()

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
    profession_ids = data.get("cabinet_profession_ids") or []

    try:
        page = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    category_id = data.get("cabinet_pending_category_id")
    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        professions = await SpecialistRepository(
            session,
        ).list_active_professions_by_category(
            UUID(category_id),
            limit=50,
        )

    await state.update_data(
        cabinet_profession_ids=[
            str(item.id)
            for item in professions
        ],
        cabinet_professions_page=page,
    )

    await callback.message.edit_reply_markup(
        reply_markup=cabinet_profession_multi_keyboard(
            items=professions,
            selected_ids=(
                data.get("cabinet_selected_profession_ids") or []
            ),
            language=language,
            page=page,
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

    async with get_session() as session:
        service = SpecialistService(
            SpecialistRepository(session),
        )
        categories = await service.list_active_categories_for_profile_editor(
            limit=50,
        )

    selected_professions = (
        data.get("cabinet_selected_professions") or []
    )
    page = data.get("cabinet_categories_page", 0)

    await state.update_data(
        cabinet_category_ids=[
            str(item.id)
            for item in categories
        ],
    )
    await state.set_state(
        SpecialistCabinetFSM.choosing_category,
    )

    await callback.message.edit_text(
        cabinet_category_prompt_text(
            selected_professions,
            language,
        ),
        reply_markup=cabinet_category_keyboard(
            items=categories,
            selected_professions=selected_professions,
            language=language,
            page=page,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("CAB_PROF:"))
async def choose_specialist_profession_update(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()
    profession_ids = data.get("cabinet_profession_ids") or []
    category_id = data.get("cabinet_pending_category_id")
    category_name = data.get("cabinet_pending_category_name")
    selected_profession_ids = data.get("cabinet_selected_profession_ids") or []
    selected_professions = data.get("cabinet_selected_professions") or []
    page = data.get("cabinet_professions_page", 0)

    try:
        index = int((callback.data or "").split(":", 1)[1])
        profession_id = profession_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        repository = SpecialistRepository(session)
        profession = await repository.get_active_profession(UUID(profession_id))
        professions = await repository.list_active_professions_by_category(
            UUID(category_id),
            limit=50,
        )

    if not profession:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    profession_id_text = str(profession.id)

    if profession_id_text in selected_profession_ids:
        selected_profession_ids = [
            item for item in selected_profession_ids if item != profession_id_text
        ]
        selected_professions = [
            item for item in selected_professions if item["profession_id"] != profession_id_text
        ]
    else:
        limit_error_key = cabinet_profession_limit_error_key(
            selected_professions,
            str(profession.category_id),
        )
        if limit_error_key:
            await callback.answer(t(limit_error_key, language), show_alert=True)
            return

        selected_profession_ids.append(profession_id_text)
        selected_professions.append(
            {
                "category_id": str(profession.category_id),
                "category_name": category_name,
                "profession_id": profession_id_text,
                "profession_name": localized_name(profession, language),
            }
        )

    await state.update_data(
        cabinet_selected_profession_ids=selected_profession_ids,
        cabinet_selected_professions=selected_professions,
    )

    await callback.message.edit_text(
        cabinet_profession_prompt_text(
            selected_professions,
            language,
        ),
        reply_markup=cabinet_profession_multi_keyboard(
            items=professions,
            selected_ids=selected_profession_ids,
            language=language,
            page=page,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_PROF_DONE")
async def save_specialist_professions_update(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()

    user_id = data.get("cabinet_user_id")
    specialist_id = data.get("cabinet_specialist_id")
    selected_professions = data.get("cabinet_selected_professions") or []

    if not user_id or not specialist_id:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        await state.clear()
        return

    if not selected_professions:
        await callback.answer(t("spec_profession_select_one", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            service = SpecialistService(
                SpecialistRepository(session),
            )
            specialist = await service.replace_profile_professions(
                specialist_id=specialist_id,
                user_id=user_id,
                profession_selections=selected_professions,
            )
    except ValueError as exc:
        await callback.message.answer(
            t("cabinet_profile_update_failed", language).format(error=str(exc)),
            reply_markup=specialist_edit_keyboard(language),
        )
        return

    logger.info(
        "cabinet_professions_updated telegram_id=%s specialist_id=%s",
        callback.from_user.id,
        specialist.id,
    )

    await callback.message.answer(
        t("cabinet_profile_updated", language),
    )
    await show_specialist_cabinet(
        callback,
        state,
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
    data = await state.get_data()
    language = (
        await get_billing_interface_language(
            message.from_user.id,
            message.from_user.language_code,
        )
    )

    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    specialist_id = data.get(
        "cabinet_specialist_id"
    )

    if (
        not user_id
        or not tenant_id
        or not specialist_id
    ):
        await message.answer(
            t(
                "cabinet_profile_not_found",
                language,
            )
        )
        await state.clear()
        return

    try:
        async with get_session() as session:
            result = await SpecialistService(
                SpecialistRepository(session)
            ).update_profile_with_audit(
                SpecialistProfileUpdateData(
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(user_id),
                    specialist_id=UUID(
                        specialist_id
                    ),
                    display_name=display_name,
                    short_description=(
                        short_description
                    ),
                    contact_text=contact_text,
                )
            )

    except SpecialistRegistrationError as exc:
        logger.warning(
            "cabinet_profile_update_failed "
            "telegram_id=%s "
            "specialist_id=%s error=%s",
            message.from_user.id,
            specialist_id,
            exc,
        )
        await message.answer(
            t(
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

    if not result.changed:
        await state.set_state(None)
        await message.answer(
            t(
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
        "cabinet_profile_updated "
        "telegram_id=%s specialist_id=%s",
        message.from_user.id,
        result.specialist_id,
    )

    await state.set_state(None)
    await message.answer(
        t(
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

@billing_router.callback_query(F.data == "BILL_PANEL")
async def show_billing_panel(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)

    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    await state.clear()
    await callback.message.answer(
        t("billing_panel_title", language),
        reply_markup=billing_menu_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "BILL_MENU")
async def billing_to_menu(callback: CallbackQuery, state: FSMContext):
    await send_global_main_menu(callback, state)


@billing_router.callback_query(F.data == "BILL_FEATURES")
async def list_billing_features(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)

    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            service = BillingService(BillingRepository(session))
            features = await service.list_paid_features(tenant_id=tenant_id)
    except BillingError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        billing_feature_codes=[feature.code for feature in features],
    )
    await callback.message.answer(
        format_features_text(features, language),
        reply_markup=paid_features_keyboard(features, language),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("BILL_BUY:"))
async def create_billing_invoice(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()
    feature_codes = data.get("billing_feature_codes") or []
    index = int(callback.data.split(":", 1)[1])

    if index < 0 or index >= len(feature_codes):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        feature_code = feature_codes[index]
        async with get_session() as session:
            service = BillingService(BillingRepository(session))
            result = await service.create_manual_invoice(
                tenant_id=tenant_id,
                payer_user_id=user_id,
                feature_code=feature_code,
                language=language,
            )

        logger.info(
            "billing_invoice_created telegram_id=%s user_id=%s invoice_id=%s feature_code=%s amount=%s currency=%s",
            callback.from_user.id,
            user_id,
            result.invoice.id,
            feature_code,
            result.invoice.amount,
            result.invoice.currency,
        )
    except BillingError as exc:
        logger.warning(
            "billing_invoice_create_failed telegram_id=%s user_id=%s feature_code=%s error=%s",
            callback.from_user.id,
            user_id,
            feature_codes[index],
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(billing_invoice_id=str(result.invoice.id))
    await callback.message.answer(
        format_invoice_text(result.invoice, result.manual_instructions, language),
        reply_markup=invoice_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "BILL_CLAIM")
async def claim_billing_payment(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()
    invoice_id = data.get("billing_invoice_id")

    if not invoice_id:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        invoice_uuid = UUID(invoice_id)
        async with get_session() as session:
            result = await BillingService(
                BillingRepository(session)
            ).claim_manual_payment(
                tenant_id=tenant_id,
                payer_user_id=user_id,
                invoice_id=invoice_uuid,
            )

        logger.info(
            "billing_payment_claimed telegram_id=%s user_id=%s invoice_id=%s payment_id=%s status=%s",
            callback.from_user.id,
            user_id,
            invoice_uuid,
            result.payment.id,
            result.status,
        )
    except BillingError as exc:
        logger.warning(
            "billing_payment_claim_failed telegram_id=%s user_id=%s invoice_id=%s error=%s",
            callback.from_user.id,
            user_id,
            invoice_id,
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return
    

    await callback.message.answer(
        t("billing_payment_claimed", language).format(
            status=billing_status_label(result.status, language),
        ),
        reply_markup=billing_menu_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(
    F.data.startswith("BETA_DISABLED:")
)
async def beta_disabled(
    callback: CallbackQuery,
):
    language = (
        await get_billing_interface_language(
            callback.from_user.id,
            callback.from_user.language_code,
        )
    )

    feature = (
        (callback.data or "").split(
            ":",
            1,
        )[1]
        if ":" in (callback.data or "")
        else "unknown"
    )

    user_id, tenant_id = (
        await get_billing_user_context(
            callback.from_user.id
        )
    )

    if user_id and tenant_id:
        async with get_session() as session:
            await BillingService(
                BillingRepository(session)
            ).record_unavailable_feature_opened(
                tenant_id=tenant_id,
                user_id=user_id,
                feature=feature,
                source="specialist_cabinet",
            )

    await callback.message.answer(
        t(
            "feature_disabled_beta_message",
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
    await callback.answer()

async def open_messages_search_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    role: str,
) -> None:
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    await state.update_data(
        messages_search_role=role,
    )
    await state.set_state(
        SpecialistCabinetFSM.entering_messages_search,
    )

    await callback.message.answer(
        t("messages_search_prompt", language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CLIENT_DIALOG_SEARCH")
async def start_client_messages_search(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_messages_search_prompt(
        callback,
        state,
        role="client",
    )


@billing_router.callback_query(F.data == "SPEC_DIALOG_SEARCH")
async def start_specialist_messages_search(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_messages_search_prompt(
        callback,
        state,
        role="specialist",
    )

@billing_router.message(
    SpecialistCabinetFSM.entering_messages_search
)
async def receive_messages_search(
    message: Message,
    state: FSMContext,
):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    search_query = (message.text or "").strip()

    if not search_query:
        await message.answer(
            t("messages_search_empty_query", language),
        )
        return

    data = await state.get_data()
    role = data.get("messages_search_role")

    user_id, _ = await get_billing_user_context(
        message.from_user.id,
    )
    if not user_id:
        await message.answer(
            t("billing_start_required", language),
        )
        return

    async with get_session() as session:
        service = ContactChatService(
            ContactChatRepository(session),
        )

        if role == "client":
            view = data.get("client_dialog_view") or "active"

            items = await service.list_client_threads(
                user_id=user_id,
                view=view,
                limit=CLIENT_DIALOGS_PAGE_SIZE,
                offset=0,
                language=language,
                search_query=search_query,
            )
            unread_messages = await service.count_unread_messages(
                user_id=user_id,
                participant_role="client",
            )
        else:
            view = (
                data.get("specialist_dialogs_view")
                or "active"
            )

            items = await service.list_specialist_threads(
                user_id=user_id,
                view=view,
                limit=6,
                offset=0,
                language=language,
                search_query=search_query,
            )
            unread_messages = await service.count_unread_messages(
                user_id=user_id,
                participant_role="specialist",
            )

    if role == "client":
        await state.update_data(
            client_messages_search_query=search_query,
            client_dialog_thread_ids=[
                str(item.thread_id)
                for item in items
            ],
            client_dialog_view=view,
            client_dialog_page=0,
        )

        await message.answer(
            format_client_dialogs_text(
                items,
                language,
                unread_messages=unread_messages,
            )
        )

        for index, item in enumerate(items):
            await message.answer(
                format_dialog_card(
                    item=item,
                    display_number=index + 1,
                    language=language,
                ),
                reply_markup=client_dialog_card_keyboard(
                    index=index,
                    language=language,
                ),
            )

        await message.answer(
            t("messages_hint", language),
            reply_markup=client_dialogs_keyboard(
                items_count=len(items),
                page=0,
                view=view,
                language=language,
                show_role_switch=False,
            ),
        )
    else:
        visible_items = items[:5]
        has_next = len(items) > 5

        await state.update_data(
            specialist_messages_search_query=search_query,
            specialist_dialog_ids=[
                str(item.thread_id)
                for item in visible_items
            ],
            specialist_dialogs_view=view,
            specialist_dialogs_page=0,
        )

        await message.answer(
            format_specialist_dialogs_text(
                dialogs=visible_items,
                view=view,
                page=0,
                unread_messages=unread_messages,
                language=language,
            )
        )

        for index, item in enumerate(visible_items):
            await message.answer(
                format_dialog_card(
                    item=item,
                    display_number=index + 1,
                    language=language,
                ),
                reply_markup=specialist_dialog_card_keyboard(
                    index=index,
                    language=language,
                ),
            )

        await message.answer(
            t("messages_hint", language),
            reply_markup=specialist_dialogs_keyboard(
                items_count=len(visible_items),
                page=0,
                view=view,
                has_next=has_next,
                language=language,
            ),
        )

    await state.set_state(None)

@billing_router.callback_query(F.data == "CLIENT_DIALOGS")
@billing_router.callback_query(F.data.startswith("CLIENT_DIALOGS:"))
async def show_client_dialogs(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    view = "active"
    page = 0

    if callback.data and callback.data.startswith("CLIENT_DIALOGS:"):
        parts = callback.data.split(":")
        if len(parts) >= 2 and parts[1] in{"new", "active", "completed", "archive"}:
            view = parts[1]
        if len(parts) >= 3 and parts[2].isdigit():
            page = int(parts[2])
    if callback.data == "CLIENT_DIALOGS":
        await state.update_data(
            client_messages_search_query=None,
        )

    state_data = await state.get_data()
    search_query = state_data.get(
        "client_messages_search_query",
    )
    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    async with get_session() as session:
        contact_service = ContactChatService(
            ContactChatRepository(session)
        )

        items = await contact_service.list_client_threads(
            user_id=user_id,
            view=view,
            limit=CLIENT_DIALOGS_PAGE_SIZE,
            offset=page * CLIENT_DIALOGS_PAGE_SIZE,
            language=language,
            search_query=search_query,
        )

        unread_messages = (
            await contact_service.count_unread_messages(
                user_id=user_id,
                participant_role="client",
            )
        )

        await contact_service.record_messages_opened(
            tenant_id=tenant_id,
            user_id=user_id,
            participant_role="client",
            view=view,
            page=page,
            items_count=len(items),
        )

    await state.update_data(
        client_dialog_thread_ids=[str(item.thread_id) for item in items],
        client_dialog_view=view,
        client_dialog_page=page,
    )
    async with get_session() as session:
        role_context = await UserService(session).get_role_switch_context(callback.from_user.id)

    show_role_switch = bool(
        role_context and len(role_context.available_roles) > 1
    )
    await callback.message.answer(
        format_client_dialogs_text(
            items,
            language,
            unread_messages=unread_messages,
        )
    )

    for index, item in enumerate(items):
        display_number = page * CLIENT_DIALOGS_PAGE_SIZE + index + 1
        await callback.message.answer(
            format_dialog_card(
                item=item,
                display_number=display_number,
                language=language,
            ),
            reply_markup=client_dialog_card_keyboard(
                index=index,
                language=language,
            ),
        )

    await callback.message.answer(
        t("messages_hint", language),
        reply_markup=client_dialogs_keyboard(
            items_count=len(items),
            page=page,
            view=view,
            language=language,
            show_role_switch=show_role_switch,
        ),
    )
    await callback.answer()

async def send_client_thread_detail(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    thread_id: str,
    language: str,
) -> None:
    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            detail = await ContactChatService(
                ContactChatRepository(session)
            ).get_thread_detail(
                thread_id=UUID(thread_id),
                user_id=user_id,
                language=language,
            )
    except Exception:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await state.update_data(
        active_contact_request_id=(
            str(detail.contact_request_id)
            if detail.contact_request_id
            else None
        ),
        active_thread_id=thread_id,
        active_thread_role="client",
    )
    await state.set_state(
        SpecialistSearchFSM.entering_thread_message,
    )

    chat_chunks = split_telegram_text(
        format_client_thread_detail_text(
            detail,
            language,
        )
    )
    attachment_items = [
        item
        for item in detail.messages
        if item.attachment
    ]
    chat_chunks = split_telegram_text(
        format_client_thread_detail_text(
            detail,
            language,
        )
    )

    for index, chunk in enumerate(chat_chunks):
        is_last_chunk = index == len(chat_chunks) - 1

        await callback.message.answer(
            chunk,
            reply_markup=(
                message_thread_keyboard(
                    language,
                    role="client",
                )
                if is_last_chunk and not attachment_items
                else None
            ),
        )

    for index, item in enumerate(attachment_items):
        is_last_attachment = (
            index == len(attachment_items) - 1
        )
        sender_name = (
            t("contact_chat_you_label", language)
            if item.is_sent_by_viewer
            else detail.specialist_name
        )
        sent_at = item.created_at.strftime(
            "%d.%m %H:%M"
        )

        await send_telegram_attachment(
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            attachment=item.attachment,
            caption=(
                f"{sender_name} · {sent_at}\n"
                f"{format_chat_message_body(item, language)}"
            ),
            reply_markup=(
                message_thread_keyboard(
                    language,
                    role="client",
                )
                if is_last_attachment
                else None
            ),
        )

    await callback.answer()

@billing_router.callback_query(F.data.startswith("CLIENT_DIALOG_OPEN:"))
async def open_client_dialog(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    data = await state.get_data()
    thread_ids = data.get("client_dialog_thread_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(thread_ids):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    thread_id = thread_ids[index]

    await send_client_thread_detail(
        callback=callback,
        state=state,
        thread_id=thread_id,
        language=language,
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
