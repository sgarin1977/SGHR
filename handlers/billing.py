import logging
import logging
from uuid import UUID
from services.geo_provider import GeoPlaceCandidate
from aiogram import F, Router
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
from database.repositories.event import EventRepository
from database.repositories.reviews import ReviewRepository
from database.repositories.geo_repository import GeoRepository
from database.repositories.rate_limit import RateLimitRepository
from database.repositories.translation import TranslationRepository
from database.models import (
    City,
    Country,
    Invoice,
    PaidFeature,
    Specialist,
    ContactRequest,
    ProfileVisibilitySetting,
    SpecialistService as SpecialistServiceModel,
    UserConsent,
)
from database.repositories.billing import BillingRepository
from database.repositories.specialist import SpecialistRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard_for_user, normalize_language, open_current_role_cabinet, send_global_main_menu
from handlers.search import SpecialistSearchFSM, complaint_reason_keyboard, contact_thread_keyboard
from services.billing import BillingError, BillingService
from services.specialist import (
    SpecialistProfileUpdateData,
    SpecialistRegistrationError,
    SpecialistService,
    SpecialistServiceItemData,
)
from services.user import UserService
from ui.texts import t
from services.geo_service import GeoService, GeoServiceError
from services.rate_limit import RateLimitError, RateLimitService
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
from sqlalchemy import delete, func, select

billing_router = Router()
logger = logging.getLogger(__name__)
MAX_SPECIALIST_CATEGORIES = 2
MAX_PROFESSIONS_PER_CATEGORY = 3
SPECIALIST_SERVICES_PAGE_SIZE = 5
OWNER_PORTFOLIO_PAGE_SIZE = 5
SPECIALIST_REVIEWS_PAGE_SIZE = 5

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
    entering_request_decline_reason = State()
    entering_service_title = State()
    entering_service_description = State()
    entering_service_price = State()
    confirming_service = State()


async def get_billing_user_context(telegram_id: int | str):
    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return None, None
        return user.id, user.tenant_id

async def get_current_specialist_for_telegram(telegram_id: int | str):
    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return None, None, None

        specialist = await SpecialistRepository(session).get_by_user_id(user.id)
        return user, specialist, user.tenant_id

async def get_billing_interface_language(
    telegram_id: int | str,
    fallback_language: str | None,
) -> str:
    language = normalize_language(fallback_language)

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return language

        settings = await TranslationRepository(session).get_language_settings(user.id)
        await session.commit()
        return normalize_language(settings.interface_language or user.language_code)

async def get_client_cabinet_counts(telegram_id: int | str) -> dict[str, int]:
    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return {
                "dialogs_unread": 0,
                "requests_count": 0,
                "requests_new": 0,
                "requests_accepted": 0,
            }

        role_context = await UserService(session).get_role_switch_context(telegram_id)
        dialogs_unread = 0
        if role_context:
            dialogs_unread = int((role_context.unread_counts or {}).get("client", 0))

        requests_result = await session.execute(
            select(ContactRequest.status, func.count(ContactRequest.id))
            .where(
                ContactRequest.from_user_id == user.id,
                ContactRequest.status.in_(["new", "accepted"]),
            )
            .group_by(ContactRequest.status)
        )

        by_status = {
            status: int(count or 0)
            for status, count in requests_result.all()
        }

        requests_new = by_status.get("new", 0)
        requests_accepted = by_status.get("accepted", 0)

        return {
            "dialogs_unread": dialogs_unread,
            "requests_count": requests_new + requests_accepted,
            "requests_new": requests_new,
            "requests_accepted": requests_accepted,
        }

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

def client_cabinet_keyboard(
    language: str,
    *,
    show_role_switch: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("menu_find_specialist", language),
                callback_data="M_FIND",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("client_dialogs_btn", language),
                callback_data="CLIENT_DIALOGS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("client_requests_btn", language),
                callback_data="CLIENT_REQUESTS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("cabinet_favorites", language),
                callback_data="CAB_FAVORITES",
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
                text=t("support_open_btn", language),
                callback_data="SUPPORT_MENU",
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

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

CLIENT_DIALOGS_PAGE_SIZE = 5
CLIENT_REQUESTS_PAGE_SIZE = 5
FAVORITES_PAGE_SIZE = 10

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
                text=t("client_dialogs_new", language),
                callback_data="CLIENT_DIALOGS:new:0",
            ),
            InlineKeyboardButton(
                text=t("client_dialogs_active", language),
                callback_data="CLIENT_DIALOGS:active:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("client_dialogs_archive", language),
                callback_data="CLIENT_DIALOGS:archive:0",
            ),
            InlineKeyboardButton(
                text=t("client_dialogs_hidden", language),
                callback_data="CLIENT_DIALOGS:hidden:0",
            ),
        ],
    ]

    if not items_count:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("menu_find_specialist", language),
                    callback_data="M_FIND",
                )
            ]
        )

    for index in range(items_count):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('client_dialog_open', language)}",
                    callback_data=f"CLIENT_DIALOG_OPEN:{index}",
                )
            ]
        )
    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_prev", language),
                callback_data=f"CLIENT_DIALOGS:{view}:{page - 1}",
            )
        )
    if items_count >= CLIENT_DIALOGS_PAGE_SIZE:
        nav_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_next", language),
                callback_data=f"CLIENT_DIALOGS:{view}:{page + 1}",
            )
        )
    if nav_row:
        rows.append(nav_row)

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

    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def client_requests_keyboard(
    *,
    items_count: int,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    if not items_count:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("menu_find_specialist", language),
                    callback_data="M_FIND",
                )
            ]
        )

    for index in range(items_count):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('client_request_open', language)}",
                    callback_data=f"CLIENT_REQUEST_OPEN:{index}",
                ),
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('client_request_dialog', language)}",
                    callback_data=f"CLIENT_REQUEST_DIALOG:{index}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('client_request_cancel', language)}",
                    callback_data=f"CLIENT_REQUEST_CANCEL:{index}",
                )
            ]
        )

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_prev", language),
                callback_data=f"CLIENT_REQUESTS:{page - 1}",
            )
        )
    if items_count >= CLIENT_REQUESTS_PAGE_SIZE:
        nav_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_next", language),
                callback_data=f"CLIENT_REQUESTS:{page + 1}",
            )
        )
    if nav_row:
        rows.append(nav_row)

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


def client_request_card_keyboard(
    *,
    request_id: str,
    has_thread: bool,
    can_cancel: bool,
    can_finish: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    if has_thread:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("client_request_dialog", language),
                    callback_data=f"CLIENT_REQUEST_CARD_DIALOG:{request_id}",
                )
            ]
        )

    action_row = []
    if can_cancel:
        action_row.append(
            InlineKeyboardButton(
                text=t("client_request_cancel", language),
                callback_data=f"CLIENT_REQUEST_CARD_CANCEL:{request_id}",
            )
        )
    if can_finish:
        action_row.append(
            InlineKeyboardButton(
                text=t("contact_finish_btn", language),
                callback_data=f"CLIENT_REQUEST_CARD_FINISH:{request_id}",
            )
        )
    if action_row:
        rows.append(action_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="CLIENT_REQUESTS",
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

def format_client_dialogs_text(items, language: str) -> str:
    if not items:
        return t("client_dialogs_empty", language)

    lines = [t("client_dialogs_title", language), ""]

    for index, item in enumerate(items, start=1):
        last_text = item.last_message_text or "-"
        if len(last_text) > 80:
            last_text = last_text[:77] + "..."

        profession = item.profession_name or "-"
        status = client_dialog_status_label(item.status, language)

        lines.append(
            f"{index}. {item.specialist_name}\n"
            f"{t('search_filter_profession_label', language)}: {profession}\n"
            f"{t('admin_status', language)}: {status}\n"
            f"{t('client_dialog_unread_label', language)}: {item.unread_count}\n"
            f"{t('client_dialog_last_label', language)}: {last_text}"
        )

    return "\n\n".join(lines)

def format_client_requests_text(items, language: str) -> str:
    if not items:
        return t("client_requests_empty", language)

    lines = [t("client_requests_title", language), ""]

    for index, item in enumerate(items, start=1):
        message = item.message or "-"
        if len(message) > 80:
            message = message[:77] + "..."

        lines.append(
            f"{index}. {item.specialist_name}\n"
            f"{t('search_filter_profession_label', language)}: {item.profession_name or '-'}\n"
            f"{t('admin_status', language)}: {client_dialog_status_label(item.status, language)}\n"
            f"{t('client_request_date', language)}: {item.created_at:%Y-%m-%d}\n"
            f"{message}"
        )

    return "\n\n".join(lines)

def format_client_request_detail_text(detail, language: str) -> str:
    return (
        f"{t('client_request_detail_title', language)}\n\n"
        f"{t('client_thread_specialist_label', language)}: {detail.specialist_name}\n"
        f"{t('search_filter_profession_label', language)}: {detail.profession_name or '-'}\n"
        f"{t('admin_status', language)}: {client_dialog_status_label(detail.status, language)}\n"
        f"{t('client_request_date', language)}: {detail.created_at:%Y-%m-%d}\n\n"
        f"{detail.message}"
    )

def format_client_thread_detail_text(detail, language: str) -> str:
    messages = detail.messages or []

    if messages:
        history = "\n".join(f"- {message}" for message in messages[-10:])
    else:
        history = t("client_thread_no_messages", language)

    return (
        f"{t('client_thread_detail_title', language)}\n\n"
        f"{t('client_thread_specialist_label', language)}: {detail.specialist_name}\n"
        f"{t('search_filter_profession_label', language)}: {detail.profession_name or '-'}\n"
        f"{t('admin_status', language)}: {client_dialog_status_label(detail.thread_status, language)}\n\n"
        f"{t('client_thread_request_label', language)}:\n"
        f"{detail.request_text or '-'}\n\n"
        f"{t('client_thread_history_label', language)}:\n"
        f"{history}"
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
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("specialist_new_requests_btn", language),
                callback_data="SPEC_REQUESTS",
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
                text=t("cabinet_profile", language),
                callback_data="CAB_PROFILE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("specialist_services_btn", language),
                callback_data="SPEC_SERVICES",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("portfolio_button", language),
                callback_data="CAB_PORTFOLIO",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("reviews_btn", language),
                callback_data="SPEC_REVIEWS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("billing_promotions", language),
                callback_data="BETA_DISABLED:promotion",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("menu_settings", language),
                callback_data="SPEC_SETTINGS",
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

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="BILL_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def favorites_list_keyboard(
    specialists: list[Specialist],
    language: str,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    rows = []

    for index, specialist in enumerate(specialists):
        rows.append(
            [
                InlineKeyboardButton(
                    text=specialist.display_name,
                    callback_data=f"CAB_FAV_VIEW:{index}",
                )
            ]
        )

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_prev", language),
                callback_data=f"CAB_FAVORITES:{page - 1}",
            )
        )
    if len(specialists) >= FAVORITES_PAGE_SIZE:
        nav_row.append(
            InlineKeyboardButton(
                text=t("client_dialogs_next", language),
                callback_data=f"CAB_FAVORITES:{page + 1}",
            )
        )
    if nav_row:
        rows.append(nav_row)

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
    price = t("search_price_not_set", language)
    if card.price_from and card.price_to:
        price = f"{card.price_from}-{card.price_to} {card.currency}"
    elif card.price_from:
        price = f"{t('search_price_from', language)} {card.price_from} {card.currency}"

    languages = ", ".join(card.languages) if card.languages else t("search_filter_not_set", language)
    city = card.city_name or t("search_filter_not_set", language)
    category = card.category_name or t("search_filter_not_set", language)
    profession = card.profession_name or t("search_filter_not_set", language)
    work_format = favorite_work_format_label(card.work_format, language)
    services = ", ".join(card.service_titles) if card.service_titles else t("search_filter_not_set", language)

    return (
        f"{card.display_name}\n\n"
        f"{t('search_filter_category_label', language)}: {category}\n"
        f"{t('search_filter_profession_label', language)}: {profession}\n"
        f"{t('search_filter_location_label', language)}: {city}\n"
        f"{t('search_filter_work_label', language)}: {work_format}\n"
        f"{t('search_services_label', language)}: {services}\n"
        f"{t('search_filter_price_label', language)}: {price}\n"
        f"{t('search_filter_language_label', language)}: {languages}\n"
        f"{t('search_rating', language)}: {card.rating} ({card.reviews_count})\n\n"
        f"{card.short_description}\n\n"
        f"{t('search_legal_warning', language)}"
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
                    text=t("cabinet_pause_profile", language),
                    callback_data="CAB_PROFILE_PAUSE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_visibility_profile", language),
                    callback_data="CAB_PROFILE_VISIBILITY",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_delete_profile", language),
                    callback_data="CAB_PROFILE_DELETE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="M_CABINET",
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
                text="<",
                callback_data=f"CAB_PORTFOLIO_PAGE:{page - 1}",
            )
        )

    if (page + 1) * OWNER_PORTFOLIO_PAGE_SIZE < total:
        nav_row.append(
            InlineKeyboardButton(
                text=">",
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
        service = PortfolioService(
            PortfolioRepository(session)
        )
        items = await service.list_owner_items(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
        )

        total = len(items)
        page = max(0, page)
        start = page * OWNER_PORTFOLIO_PAGE_SIZE
        end = start + OWNER_PORTFOLIO_PAGE_SIZE
        page_items = items[start:end]

        await EventRepository(session).create_event(
            tenant_id=tenant_id,
            user_id=owner_user_id,
            event_type="portfolio_list",
            entity_type="user",
            entity_id=owner_user_id,
            payload={
                "page": page,
                "count": len(page_items),
                "total": total,
            },
            platform="telegram",
        )
        await session.commit()

    if not items:
        await message.answer(
            (
                f"{t('portfolio_title', language)}\n\n"
                f"{t('portfolio_empty', language)}"
            ),
            reply_markup=portfolio_menu_keyboard(language, page=page, total=total),
        )
        return

    await message.answer(
        f"{t('portfolio_title', language)}\n{page + 1}/{max(1, (total + OWNER_PORTFOLIO_PAGE_SIZE - 1) // OWNER_PORTFOLIO_PAGE_SIZE)}",
        reply_markup=portfolio_menu_keyboard(language, page=page, total=total),
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
                    callback_data="CAB_PROFILE_EDIT",
                )
            ],
        ]
    )

def profile_languages_keyboard(
    selected: list[str],
    language: str,
) -> InlineKeyboardMarkup:
    def marker(code: str) -> str:
        return "* " if code in selected else ""

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
                    callback_data="CAB_PROFILE_EDIT",
                )
            ],
        ]
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
        f"{t('cabinet_choose_profession', language)}\n\n"
        f"{t('spec_selected_professions_title', language)}\n"
        f"{cabinet_selected_professions_text(selected_professions, language)}"
    )


def cabinet_profession_multi_keyboard(
    *,
    items,
    selected_ids: list[str],
    language: str,
) -> InlineKeyboardMarkup:
    selected_set = set(selected_ids)
    rows = []

    for index, item in enumerate(items):
        item_id = str(item.id)
        marker = "✓ " if item_id in selected_set else ""

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{localized_name(item, language)}",
                    callback_data=f"CAB_PROF:{index}",
                )
            ]
        )

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
                callback_data="CAB_EDIT_PROFESSION",
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
        city = await session.get(City, specialist.city_id) if specialist.city_id else None
        country_id = city.country_id if city else specialist.country_id
        country = await session.get(Country, country_id) if country_id else None

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

    contact_text = (specialist.extra_metadata or {}).get("contact_text") or "-"
    verified_text = (
        t("yes", language)
        if specialist.is_verified
        else t("search_filter_not_set", language)
    )
    rating = specialist.rating or 0
    reviews_count = specialist.reviews_count or 0

    return (
        f"{t('cabinet_profile_title', language)}\n\n"
        f"{t('cabinet_profile_name', language)}: {specialist.display_name}\n"
        f"{t('cabinet_profile_status', language)}: {client_dialog_status_label(specialist.status, language)}\n"
        f"{t('search_filter_profession_label', language)}: {profession_text}\n"
        f"{t('cabinet_profile_location', language)}: {location_text}\n"
        f"{t('cabinet_profile_description', language)}: {specialist.short_description}\n"
        f"{t('cabinet_profile_contacts', language)}: {contact_text}\n"
        f"{t('cabinet_profile_price', language)}: {specialist.price_from or '-'}-{specialist.price_to or '-'} {specialist.currency}\n"
        f"{t('cabinet_profile_verified', language)}: {verified_text}\n"
        f"{t('search_rating', language)}: {rating} ({reviews_count})"
    )
def specialist_status_notice(status: str | None, language: str) -> str:
    normalized = status or "unknown"

    if normalized == "active":
        return t("specialist_status_active_notice", language)
    if normalized == "pending_moderation":
        return t("specialist_status_pending_notice", language)
    if normalized == "rejected":
        return t("specialist_status_rejected_notice", language)
    if normalized == "paused":
        return t("specialist_status_paused_notice", language)

    return t("specialist_status_generic_notice", language).format(status=normalized)


def format_specialist_cabinet_text(
    *,
    profession_name: str,
    status: str,
    new_requests: int,
    unread_count: int,
    moderation_text: str,
    language: str,
) -> str:
    return (
        f"{t('specialist_cabinet_title', language)}\n\n"
        f"{t('search_filter_profession_label', language)}: {profession_name or '-'}\n"
        f"{t('admin_status', language)}: {client_dialog_status_label(status, language)}\n"
        f"{t('specialist_new_requests_label', language)}: {new_requests}\n"
        f"{t('specialist_unread_label', language)}: {unread_count}\n\n"
        f"{moderation_text}"
    )

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


def format_invoice_text(
    invoice: Invoice,
    manual_instructions: str,
    language: str,
) -> str:
    return (
        f"{t('billing_invoice_created', language)}\n\n"
        f"{t('billing_invoice_id', language)}: {invoice.id}\n"
        f"{t('billing_amount', language)}: {invoice.amount} {invoice.currency}\n"
        f"{t('admin_status', language)}: {invoice.status}\n\n"
        f"{t('billing_manual_instructions_title', language)}\n"
        f"{manual_instructions}"
    )

@billing_router.callback_query(F.data == "M_CABINET")
async def open_my_cabinet(callback: CallbackQuery, state: FSMContext):
    await open_current_role_cabinet(callback, state)


async def build_specialist_cabinet_payload(
    telegram_id: int | str,
    fallback_language: str | None,
) -> tuple[str, str, InlineKeyboardMarkup | None]:
    language = await get_billing_interface_language(
        telegram_id,
        fallback_language,
    )

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return language, t("billing_start_required", language), None

        specialist = await SpecialistRepository(session).get_by_user_id(user.id)
        if not specialist:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("menu_offer_services", language),
                            callback_data="SS_START",
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
            return language, t("specialist_no_profile_start", language), keyboard

        role_context = await UserService(session).get_role_switch_context(telegram_id)
        show_role_switch = bool(
            role_context and len(role_context.available_roles) > 1
        )
        unread_count = int((role_context.unread_counts or {}).get("specialist", 0)) if role_context else 0

        professions = await SpecialistRepository(session).list_active_specialist_professions(
            specialist.id,
        )
        profession_names = [
            localized_name(row.Profession, language)
            for row in professions
        ]
        profession_name = ", ".join(profession_names) or "-"

        requests_result = await session.execute(
            select(func.count(ContactRequest.id)).where(
                ContactRequest.specialist_id == specialist.id,
                ContactRequest.status == "new",
            )
        )
        new_requests = int(requests_result.scalar_one() or 0)

        moderation_text = specialist_status_notice(specialist.status, language)

        await EventRepository(session).create_event(
            event_type="specialist_menu",
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "status": specialist.status,
                "new_requests": new_requests,
                "unread_count": unread_count,
            },
            platform="telegram",
        )
        await session.commit()

    text = format_specialist_cabinet_text(
        profession_name=profession_name,
        status=specialist.status,
        new_requests=new_requests,
        unread_count=unread_count,
        moderation_text=moderation_text,
        language=language,
    )
    keyboard = cabinet_menu_keyboard(
        language,
        show_role_switch=show_role_switch,
    )

    return language, text, keyboard

async def show_specialist_cabinet(callback: CallbackQuery, state: FSMContext):
    language, text, keyboard = await build_specialist_cabinet_payload(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    if keyboard is None:
        await callback.answer(text, show_alert=True)
        return

    await state.clear()
    await callback.message.answer(
        text,
        reply_markup=keyboard,
    )
    await callback.answer()


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

@billing_router.callback_query(F.data == "SPEC_REQUESTS")
async def specialist_requests_entry(callback: CallbackQuery, state: FSMContext):
    await show_specialist_requests(callback, state, page=0)


@billing_router.callback_query(F.data.startswith("SPEC_REQUESTS_PAGE:"))
async def paginate_specialist_requests(callback: CallbackQuery, state: FSMContext):
    try:
        page = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        page = 0

    await show_specialist_requests(callback, state, page=max(page, 0))


async def show_specialist_requests(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    page: int,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    async with get_session() as session:
        specialist = await SpecialistRepository(session).get_by_user_id(user_id)
        if not specialist:
            await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
            return

        items = await ContactChatService(
            ContactChatRepository(session)
        ).list_specialist_requests(
            specialist_id=specialist.id,
            status="new",
            limit=6,
            offset=page * 5,
            language=language,
        )

        await EventRepository(session).create_event(
            event_type="specialist_requests_opened",
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "page": page,
                "visible_count": min(len(items), 5),
            },
            platform="telegram",
        )
        await session.commit()

    visible_items = items[:5]
    has_next = len(items) > 5

    await state.update_data(
        specialist_request_ids=[str(item.contact_request_id) for item in visible_items],
        specialist_request_thread_ids=[
            str(item.thread_id) if item.thread_id else None
            for item in visible_items
        ],
        specialist_requests_page=page,
    )

    await callback.message.answer(
        format_specialist_requests_text(visible_items, language),
        reply_markup=specialist_requests_keyboard(
            items=visible_items,
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

async def update_specialist_request_status_from_list(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    action: str,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    request_ids = data.get("specialist_request_ids") or []
    page = int(data.get("specialist_requests_page") or 0)

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(request_ids):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await ContactChatService(
                ContactChatRepository(session)
            ).set_contact_request_status(
                contact_request_id=UUID(request_ids[index]),
                actor_user_id=user_id,
                tenant_id=tenant_id,
                action=action,
            )
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(t("specialist_request_status_updated", language))
    await callback.answer()
    await show_specialist_requests(callback, state, page=page)


@billing_router.callback_query(F.data.startswith("SPEC_REQUEST_ACCEPT:"))
async def accept_specialist_request(callback: CallbackQuery, state: FSMContext):
    await update_specialist_request_status_from_list(
        callback,
        state,
        action="accept",
    )


@billing_router.callback_query(F.data.startswith("SPEC_REQUEST_REJECT:"))
async def reject_specialist_request(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    request_ids = data.get("specialist_request_ids") or []
    page = int(data.get("specialist_requests_page") or 0)

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(request_ids):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    await state.update_data(
        pending_reject_request_id=request_ids[index],
        pending_reject_requests_page=page,
    )
    await state.set_state(SpecialistCabinetFSM.entering_request_decline_reason)

    await callback.message.answer(t("specialist_request_decline_reason_prompt", language))
    await callback.answer()

@billing_router.message(SpecialistCabinetFSM.entering_request_decline_reason)
async def finish_specialist_request_reject(message: Message, state: FSMContext):
    language = await get_billing_interface_language(
        message.from_user.id,
        message.from_user.language_code,
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(t("specialist_request_decline_reason_required", language))
        return

    data = await state.get_data()
    request_id = data.get("pending_reject_request_id")
    page = int(data.get("pending_reject_requests_page") or 0)

    if not request_id:
        await state.clear()
        await message.answer(t("contact_request_not_found", language))
        return

    user_id, tenant_id = await get_billing_user_context(message.from_user.id)
    if not user_id or not tenant_id:
        await state.clear()
        await message.answer(t("billing_start_required", language))
        return

    try:
        async with get_session() as session:
            await ContactChatService(
                ContactChatRepository(session)
            ).set_contact_request_status(
                contact_request_id=UUID(request_id),
                actor_user_id=user_id,
                tenant_id=tenant_id,
                action="reject",
                decline_reason=reason,
            )
    except Exception as exc:
        await message.answer(str(exc))
        return

    await state.update_data(
        pending_reject_request_id=None,
        pending_reject_requests_page=None,
    )
    await state.set_state(None)

    await message.answer(t("specialist_request_declined", language))

@billing_router.callback_query(F.data.startswith("SPEC_REQUEST_OPEN:"))
async def open_specialist_request_from_list(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    request_ids = data.get("specialist_request_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(request_ids):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    await callback.answer(t("specialist_requests_title", language), show_alert=True)

def specialist_thread_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("contact_reply_btn", language),
                    callback_data="contact_reply",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_archive_btn", language),
                    callback_data="SPEC_THREAD_ARCHIVE",
                ),
                InlineKeyboardButton(
                    text=t("contact_hide_btn", language),
                    callback_data="SPEC_THREAD_HIDE",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_complete_btn", language),
                    callback_data="SPEC_THREAD_COMPLETE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_report_btn", language),
                    callback_data="SPEC_THREAD_REPORT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_back_to_dialogs_btn", language),
                    callback_data="SPEC_DIALOGS",
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
                text=t("client_dialogs_new", language),
                callback_data="SPEC_DIALOGS_VIEW:new:0",
            ),
            InlineKeyboardButton(
                text=t("client_dialogs_active", language),
                callback_data="SPEC_DIALOGS_VIEW:active:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("client_dialogs_archive", language),
                callback_data="SPEC_DIALOGS_VIEW:archive:0",
            ),
            InlineKeyboardButton(
                text=t("client_dialogs_hidden", language),
                callback_data="SPEC_DIALOGS_VIEW:hidden:0",
            ),
        ],
    ]

    for index in range(items_count):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('client_request_open', language)}",
                    callback_data=f"SPEC_DIALOG_OPEN:{index}",
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="<",
                callback_data=f"SPEC_DIALOGS_VIEW:{view}:{page - 1}",
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text=">",
                callback_data=f"SPEC_DIALOGS_VIEW:{view}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

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


def format_specialist_dialogs_text(
    *,
    dialogs,
    view: str,
    page: int,
    language: str,
) -> str:
    title = t("specialist_dialogs_title", language)
    if not dialogs:
        return f"{title}\n\n{t('specialist_dialogs_empty', language)}"

    view_label = {
        "new": t("client_dialogs_new", language),
        "active": t("client_dialogs_active", language),
        "archive": t("client_dialogs_archive", language),
        "hidden": t("client_dialogs_hidden", language),
    }.get(view, view)

    lines = [title, f"{t('client_dialogs_view_label', language)}: {view_label}", ""]
    for index, item in enumerate(dialogs, start=page * 5 + 1):
        last_message = item.last_message_text or "-"
        if len(last_message) > 80:
            last_message = f"{last_message[:77]}..."

        unread = item.unread_count or 0
        profession = item.profession_name or "-"
        lines.append(
            f"{index}. {profession}\n"
            f"{t('client_dialogs_unread', language)}: {unread}\n"
            f"{t('client_dialogs_last_message', language)}: {last_message}"
        )

    return "\n\n".join(lines)


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
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    async with get_session() as session:
        dialogs = await ContactChatService(
            ContactChatRepository(session)
        ).list_specialist_threads(
            user_id=user_id,
            view=view,
            limit=6,
            offset=page * 5,
            language=language,
        )

        await EventRepository(session).create_event(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="dialogs_opened",
            entity_type="specialist_dialogs",
            payload={
                "view": view,
                "page": page,
                "role": "specialist",
            },
            platform="telegram",
        )
        await session.commit()

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
            language=language,
        ),
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
            ).get_thread_detail(
                thread_id=UUID(thread_id),
                user_id=user_id,
                language=language,
            )

            await EventRepository(session).create_event(
                event_type="dialog_opened",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="conversation_thread",
                entity_id=UUID(thread_id),
                payload={
                    "role": "specialist",
                    "thread_status": detail.thread_status,
                    "request_status": detail.request_status,
                },
                platform="telegram",
            )
            await session.commit()
    except Exception:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await state.update_data(
        active_thread_id=thread_id,
        active_thread_role="specialist",
    )

    await callback.message.answer(
        format_client_thread_detail_text(detail, language),
        reply_markup=specialist_thread_keyboard(language),
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
async def specialist_dialogs_entry(callback: CallbackQuery, state: FSMContext):
    await show_specialist_dialogs(callback, state, view="active", page=0)


@billing_router.callback_query(F.data.startswith("SPEC_DIALOGS_VIEW:"))
async def specialist_dialogs_view(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    view = parts[1] if len(parts) > 1 else "active"
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0

    if view not in {"new", "active", "archive", "hidden"}:
        view = "active"
    if page < 0:
        page = 0

    await show_specialist_dialogs(callback, state, view=view, page=page)

async def change_specialist_thread_visibility(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    is_archived: bool | None = None,
    is_hidden: bool | None = None,
):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    thread_id = data.get("active_thread_id")

    if not thread_id:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await ContactChatService(ContactChatRepository(session)).set_thread_visibility(
                thread_id=UUID(thread_id),
                user_id=user_id,
                is_archived=is_archived,
                is_hidden=is_hidden,
            )
    except ContactChatError as exc:
        await callback.answer(t("contact_request_error", language).format(error=str(exc)), show_alert=True)
        return

    await callback.message.answer(
        t("contact_thread_archived" if is_archived else "contact_thread_hidden", language),
    )
    await show_specialist_dialogs(callback, state, view="archive" if is_archived else "hidden", page=0)


@billing_router.callback_query(F.data == "SPEC_THREAD_ARCHIVE")
async def archive_specialist_thread(callback: CallbackQuery, state: FSMContext):
    await change_specialist_thread_visibility(
        callback,
        state,
        is_archived=True,
    )


@billing_router.callback_query(F.data == "SPEC_THREAD_HIDE")
async def hide_specialist_thread(callback: CallbackQuery, state: FSMContext):
    await change_specialist_thread_visibility(
        callback,
        state,
        is_hidden=True,
    )

@billing_router.callback_query(F.data == "SPEC_THREAD_COMPLETE")
async def request_specialist_thread_completion(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()
    thread_id = data.get("active_thread_id")

    if not thread_id:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await ContactChatService(
                ContactChatRepository(session)
            ).request_thread_completion(
                tenant_id=tenant_id,
                thread_id=UUID(thread_id),
                actor_user_id=user_id,
                role="specialist",
            )

    except ContactChatError as exc:
        await callback.answer(
            t("contact_request_error", language).format(error=str(exc)),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("contact_thread_completion_requested", language),
        reply_markup=specialist_thread_keyboard(language),
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
        f"{page + 1}/{max(1, (total + SPECIALIST_SERVICES_PAGE_SIZE - 1) // SPECIALIST_SERVICES_PAGE_SIZE)}",
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
        total_result = await session.execute(
            select(func.count())
            .select_from(SpecialistServiceModel)
            .where(
                SpecialistServiceModel.specialist_id == specialist.id,
                SpecialistServiceModel.status != "deleted",
            )
        )
        total = int(total_result.scalar_one() or 0)

        result = await session.execute(
            select(SpecialistServiceModel)
            .where(
                SpecialistServiceModel.specialist_id == specialist.id,
                SpecialistServiceModel.status != "deleted",
            )
            .order_by(SpecialistServiceModel.created_at.desc())
            .offset(page * SPECIALIST_SERVICES_PAGE_SIZE)
            .limit(SPECIALIST_SERVICES_PAGE_SIZE)
        )
        services = list(result.scalars().all())

        await EventRepository(session).create_event(
            tenant_id=tenant_id,
            user_id=user.id,
            event_type="service_list",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "page": page,
                "count": len(services),
                "total": total,
            },
            platform="telegram",
        )
        await session.commit()

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

    async with get_session() as session:
        service = await session.get(SpecialistServiceModel, service_id)
        if (
            not service
            or service.specialist_id != specialist.id
            or service.status == "deleted"
        ):
            await callback.answer(t("specialist_service_not_found", language), show_alert=True)
            return

        await state.update_data(
            service_mode="edit",
            service_id=str(service.id),
            service_specialist_id=str(specialist.id),
            service_tenant_id=str(tenant_id),
            service_user_id=str(user.id),
            service_category_id=str(service.category_id) if service.category_id else None,
            service_profession_id=str(service.profession_id) if service.profession_id else None,
            service_title=service.title,
            service_description=service.description or "",
            service_price_from=float(service.price_from) if service.price_from is not None else None,
            service_price_to=float(service.price_to) if service.price_to is not None else None,
            service_currency=service.currency or "EUR",
            service_before={
                "title": service.title,
                "description": service.description,
                "price_from": float(service.price_from) if service.price_from is not None else None,
                "price_to": float(service.price_to) if service.price_to is not None else None,
                "currency": service.currency or "EUR",
                "status": service.status,
            },
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
            service, saved_mode = await SpecialistService(
                SpecialistRepository(session)
            ).save_service_item(
                SpecialistServiceItemData(
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(user_id),
                    specialist_id=UUID(specialist_id),
                    service_id=UUID(service_id) if service_id else None,
                    category_id=UUID(data["service_category_id"])
                    if data.get("service_category_id")
                    else None,
                    profession_id=UUID(data["service_profession_id"])
                    if data.get("service_profession_id")
                    else None,
                    title=title,
                    description=description,
                    price_from=data.get("service_price_from"),
                    price_to=data.get("service_price_to"),
                    currency=data.get("service_currency") or "EUR",
                )
            )

            if saved_mode == "edit":
                before_payload = data.get("service_before") or {}
                event_payload = {
                    "mode": "edit",
                    "before": before_payload,
                    "after": {
                        "title": service.title,
                        "description": service.description,
                        "price_from": data.get("service_price_from"),
                        "price_to": data.get("service_price_to"),
                        "currency": service.currency,
                        "status": service.status,
                    },
                }
            else:
                event_payload = {
                    "mode": "create",
                    "title": service.title,
                    "price_from": data.get("service_price_from"),
                    "price_to": data.get("service_price_to"),
                    "currency": service.currency,
                    "status": service.status,
                }

            await EventRepository(session).create_event(
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
                event_type="service_saved",
                entity_type="specialist_service",
                entity_id=service.id,
                payload=event_payload,
                platform="telegram",
            )
            await session.commit()
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
            service, before_status, after_status = await SpecialistService(
                SpecialistRepository(session)
            ).toggle_service_item_status(
                user_id=user.id,
                specialist_id=specialist.id,
                service_id=service_id,
            )

            await EventRepository(session).create_event(
                tenant_id=tenant_id,
                user_id=user.id,
                event_type="service_status_changed",
                entity_type="specialist_service",
                entity_id=service.id,
                payload={
                    "before": before_status,
                    "after": after_status,
                },
                platform="telegram",
            )
            await session.commit()
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
            service, before_status = await SpecialistService(
                SpecialistRepository(session)
            ).delete_service_item(
                user_id=user.id,
                specialist_id=specialist.id,
                service_id=service_id,
            )

            await EventRepository(session).create_event(
                tenant_id=tenant_id,
                user_id=user.id,
                event_type="service_deleted",
                entity_type="specialist_service",
                entity_id=service.id,
                payload={
                    "before": before_status,
                    "after": "deleted",
                },
                platform="telegram",
            )
            await session.commit()
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
        rating = "0.0"
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
        ).list_public_reviews_for_specialist(
            tenant_id=tenant_id,
            specialist_id=specialist.id,
            page=page,
            page_size=SPECIALIST_REVIEWS_PAGE_SIZE,
        )

        await EventRepository(session).create_event(
            tenant_id=tenant_id,
            user_id=user.id,
            event_type="reviews_viewed",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "page": review_page.page,
                "count": len(review_page.reviews),
                "total": review_page.total_count,
                "source": "specialist_cabinet",
            },
            platform="telegram",
        )
        await session.commit()

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
                    text=t("settings_visibility_btn", language),
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
                    text=t("settings_deletion_btn", language),
                    callback_data="CAB_PROFILE_DELETE",
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

        settings = await TranslationRepository(session).get_language_settings(user.id)
        language = normalize_language(settings.interface_language or user.language_code)
        await session.commit()

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

@billing_router.callback_query(F.data.startswith("SPEC_SET_UI_LANG:"))
async def set_specialist_interface_language(callback: CallbackQuery, state: FSMContext):
    fallback_language = normalize_language(callback.from_user.language_code)
    interface_language = normalize_language((callback.data or "").split(":", 1)[1])

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", fallback_language), show_alert=True)
            return

        await TranslationRepository(session).update_language_settings(
            user_id=user.id,
            interface_language=interface_language,
        )
        await UserService(session).update_interface_language(
            user_id=user.id,
            language_code=interface_language,
        )

        await EventRepository(session).create_event(
            event_type="settings_changed",
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            payload={
                "setting": "interface_language",
                "value": interface_language,
                "source": "specialist_settings",
            },
            platform="telegram",
        )
        await session.commit()

    await render_specialist_language_settings(callback)


@billing_router.callback_query(F.data.startswith("SPEC_SET_MSG_LANG:"))
async def set_specialist_message_language(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    message_language = (callback.data or "").split(":", 1)[1]

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        await TranslationRepository(session).update_language_settings(
            user_id=user.id,
            message_language=message_language,
        )

        await EventRepository(session).create_event(
            event_type="settings_changed",
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            payload={
                "setting": "message_language",
                "value": message_language,
                "source": "specialist_settings",
            },
            platform="telegram",
        )
        await session.commit()

    await render_specialist_language_settings(callback)


@billing_router.callback_query(F.data == "SPEC_SET_SHOW_ORIGINAL")
async def toggle_specialist_show_original(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
            return

        repository = TranslationRepository(session)
        settings = await repository.get_language_settings(user.id)
        new_value = not settings.show_original_button

        await repository.update_language_settings(
            user_id=user.id,
            show_original_button=new_value,
        )

        await EventRepository(session).create_event(
            event_type="settings_changed",
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            payload={
                "setting": "show_original_button",
                "value": new_value,
                "source": "specialist_settings",
            },
            platform="telegram",
        )
        await session.commit()

    await render_specialist_language_settings(callback)

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
        result = await session.execute(
            select(UserConsent)
            .where(
                UserConsent.tenant_id == tenant_id,
                UserConsent.user_id == user_id,
            )
            .order_by(UserConsent.accepted_at.desc())
        )
        consents = list(result.scalars().all())

    if consents:
        lines = [t("settings_consents_title", language), ""]
        for index, consent in enumerate(consents, start=1):
            status = (
                t("settings_consent_revoked", language)
                if consent.revoked_at
                else t("settings_consent_active", language)
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

def specialist_requests_keyboard(
    *,
    items,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, item in enumerate(items):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('client_request_open', language)}",
                    callback_data=f"SPEC_REQUEST_OPEN:{index}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('contact_accept_btn', language)}",
                    callback_data=f"SPEC_REQUEST_ACCEPT:{index}",
                ),
                InlineKeyboardButton(
                    text=f"{index + 1}. {t('contact_reject_btn', language)}",
                    callback_data=f"SPEC_REQUEST_REJECT:{index}",
                ),
            ]
        )

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="<",
                callback_data=f"SPEC_REQUESTS_PAGE:{page - 1}",
            )
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=">",
                callback_data=f"SPEC_REQUESTS_PAGE:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)

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


def format_specialist_requests_text(items, language: str) -> str:
    if not items:
        return t("specialist_requests_empty", language)

    lines = [t("specialist_requests_title", language), ""]
    for index, item in enumerate(items, start=1):
        message = (item.message or "-").strip()
        if len(message) > 160:
            message = message[:157].rstrip() + "..."

        lines.append(
            f"{index}. {item.client_name}\n"
            f"{t('search_filter_profession_label', language)}: {item.profession_name or '-'}\n"
            f"{t('client_request_date', language)}: {item.created_at:%Y-%m-%d}\n"
            f"{message}"
        )
        lines.append("")

    return "\n".join(lines).strip()

async def show_client_cabinet(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.answer(t("billing_start_required", language), show_alert=True)
            return

        role_context = await UserService(session).get_role_switch_context(callback.from_user.id)
        show_role_switch = bool(
            role_context and len(role_context.available_roles) > 1
        )

        await EventRepository(session).create_event(
            event_type="client_menu_opened",
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            payload={
                "active_role": user.active_role,
            },
            platform="telegram",
        )
        await session.commit()

    counts = await get_client_cabinet_counts(callback.from_user.id)

    await state.clear()
    await callback.message.answer(
        t("client_cabinet_title", language)
        + "\n\n"
    + t("client_cabinet_summary", language).format(**counts),
        reply_markup=client_cabinet_keyboard(
            language,
            show_role_switch=show_role_switch,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_FAVORITES")
@billing_router.callback_query(F.data.startswith("CAB_FAVORITES:"))
async def show_favorites(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    page = 0
    if callback.data and callback.data.startswith("CAB_FAVORITES:"):
        parts = callback.data.split(":")
        if len(parts) >= 2 and parts[1].isdigit():
            page = int(parts[1])
    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)

    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    async with get_session() as session:
        specialists = await FavoriteRepository(session).list_saved_specialists(
            tenant_id=tenant_id,
            user_id=user_id,
            limit=FAVORITES_PAGE_SIZE,
            offset=page * FAVORITES_PAGE_SIZE,
        )
        await EventRepository(session).create_event(
            event_type="favorites_opened",
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type="saved_specialist",
            payload={
                "page": page,
                "items_count": len(specialists),
            },
            platform="telegram",
        )
        await session.commit()
    await state.update_data(
        user_language=language,
        cabinet_favorite_ids=[str(item.id) for item in specialists],
        cabinet_favorites_page=page,
    )

    if not specialists:
        await callback.message.answer(
            t("favorites_empty", language),
            reply_markup=favorites_list_keyboard([], language, page=page),
        )
        await callback.answer()
        return

    await callback.message.answer(
        t("favorites_title", language),
        reply_markup=favorites_list_keyboard(specialists, language, page=page),
    )
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
        card = await GeoSearchService(
            SpecialistSearchRepository(session)
        ).get_public_card(
            specialist_id=UUID(specialist_id),
            requester_user_id=user_id,
            tenant_id=tenant_id,
            distance_km=None,
            log_event=True,
            language=language,
        )
        await EventRepository(session).create_event(
            event_type="favorite_viewed",
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type="specialist",
            entity_id=UUID(specialist_id),
            payload={
                "source": "favorites",
            },
            platform="telegram",
        )
        await session.commit()

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
        removed = await FavoriteRepository(session).remove_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=UUID(specialist_id),
        )
        if removed:
            await EventRepository(session).create_event(
                event_type="favorite_removed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="specialist",
                entity_id=UUID(specialist_id),
                payload={
                    "source": "favorites",
                },
                platform="telegram",
            )
            await session.commit()

    text_key = "favorite_removed" if removed else "favorites_not_found"
    await callback.answer(t(text_key, language), show_alert=True)

    async with get_session() as session:
        specialists = await FavoriteRepository(session).list_saved_specialists(
            tenant_id=tenant_id,
            user_id=user_id,
            limit=FAVORITES_PAGE_SIZE,
            offset=page * FAVORITES_PAGE_SIZE,
        )

    await state.update_data(
        cabinet_favorite_ids=[str(item.id) for item in specialists],
        selected_specialist_id=None,
    )
    await callback.message.answer(
        t("favorites_title", language) if specialists else t("favorites_empty", language),
        reply_markup=favorites_list_keyboard(specialists, language, page=page),
    )
@billing_router.callback_query(F.data == "CAB_PROFILE")
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

            await EventRepository(session).create_event(
                tenant_id=UUID(tenant_id),
                user_id=UUID(owner_user_id),
                event_type="portfolio_uploaded",
                entity_type="user",
                entity_id=UUID(owner_user_id),
                payload={
                    "filename": filename,
                    "mime_type": mime_type,
                    "size_bytes": len(content),
                    "has_caption": bool(caption),
                    "status": "pending_moderation",
                },
                platform="telegram",
            )
            await session.commit()

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
async def pause_specialist_profile(callback: CallbackQuery, state: FSMContext):
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
        try:
            db_specialist, before_status, after_status, action = await SpecialistService(
                SpecialistRepository(session)
            ).toggle_profile_status(
                user_id=user.id,
                specialist_id=specialist.id,
            )
        except (SpecialistRegistrationError, ValueError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        await EventRepository(session).create_event(
            tenant_id=tenant_id,
            user_id=user.id,
            event_type="profile_action",
            entity_type="specialist",
            entity_id=db_specialist.id,
            payload={
                "action": action,
                "before_status": before_status,
                "after_status": after_status,
            },
            platform="telegram",
        )
        await session.commit()

    notice_key = (
        "specialist_status_active_notice"
        if action == "resume"
        else "specialist_status_paused_notice"
    )

    await callback.message.answer(
        t(notice_key, language),
        reply_markup=specialist_profile_keyboard(language),
    )
    await callback.answer()
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

    current_visibility = None
    async with get_session() as session:
        result = await session.execute(
            select(ProfileVisibilitySetting).where(
                ProfileVisibilitySetting.user_id == user.id,
                ProfileVisibilitySetting.profile_type == "specialist",
            )
        )
        settings = result.scalar_one_or_none()
        if settings:
            current_visibility = settings.visibility_level

    await callback.message.answer(
        t("cabinet_visibility_current", language).format(
            visibility=profile_visibility_label(current_visibility, language),
        ),
        reply_markup=profile_visibility_keyboard(language),
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

    async with get_session() as session:
        try:
            db_specialist, before_visibility, after_visibility = await SpecialistService(
                SpecialistRepository(session)
            ).update_profile_visibility(
                user_id=user.id,
                specialist_id=specialist.id,
                visibility=visibility,
            )
        except (SpecialistRegistrationError, ValueError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        await EventRepository(session).create_event(
            tenant_id=tenant_id,
            user_id=user.id,
            event_type="profile_action",
            entity_type="specialist",
            entity_id=db_specialist.id,
            payload={
                "action": "visibility_changed",
                "before_visibility": before_visibility,
                "after_visibility": after_visibility,
            },
            platform="telegram",
        )
        await session.commit()

    await callback.message.answer(
        t("cabinet_visibility_updated", language).format(
            visibility=profile_visibility_label(visibility, language),
        ),
        reply_markup=specialist_profile_keyboard(language),
    )
    await callback.answer()

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
        await PrivacyService(PrivacyRepository(session)).schedule_profile_deletion(
            tenant_id=tenant_id,
            user_id=user.id,
        )

        await EventRepository(session).create_event(
            tenant_id=tenant_id,
            user_id=user.id,
            event_type="profile_action",
            entity_type="specialist",
            entity_id=specialist.id,
            payload={
                "action": "delete_requested",
            },
            platform="telegram",
        )
        await session.commit()

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

    async with get_session() as session:
        try:
            db_specialist, before_work_format, after_work_format, changed = await SpecialistService(
                SpecialistRepository(session)
            ).update_work_format(
                user_id=user.id,
                specialist_id=specialist.id,
                work_format=work_format,
            )
        except (SpecialistRegistrationError, ValueError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        if not changed:
            await callback.message.answer(
                t("cabinet_profile_no_changes", language),
                reply_markup=specialist_edit_keyboard(language),
            )
            await callback.answer()
            return

        await EventRepository(session).create_event(
            tenant_id=tenant_id,
            user_id=user.id,
            event_type="change_submitted",
            entity_type="specialist",
            entity_id=db_specialist.id,
            payload={
                "field": "work_format",
                "before": before_work_format,
                "after": after_work_format,
            },
            platform="telegram",
        )
        await session.commit()

    await callback.message.answer(
        t("cabinet_profile_updated", language),
        reply_markup=specialist_edit_keyboard(language),
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

    async with get_session() as session:
        selected = await SpecialistRepository(session).list_specialist_language_codes(
            specialist_id=specialist.id,
        )

    if not selected:
        selected = ["ru"]

    await state.update_data(
        cabinet_specialist_id=str(specialist.id),
        cabinet_user_id=str(user.id),
        cabinet_tenant_id=str(tenant_id),
        cabinet_selected_languages=selected,
    )

    await callback.message.answer(
        t("spec_languages_prompt", language),
        reply_markup=profile_languages_keyboard(selected, language),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("CAB_LANG_TOGGLE:"))
async def toggle_specialist_language(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    data = await state.get_data()

    code = (callback.data or "").split(":", 1)[1]
    if code not in {"ru", "en", "pt"}:
        await callback.answer()
        return

    selected = list(data.get("cabinet_selected_languages") or ["ru"])

    if code in selected:
        selected = [item for item in selected if item != code]
    else:
        selected.append(code)

    if not selected:
        await callback.answer(t("spec_profession_select_one", language), show_alert=True)
        return

    await state.update_data(cabinet_selected_languages=selected)

    await callback.message.answer(
        t("spec_languages_prompt", language),
        reply_markup=profile_languages_keyboard(selected, language),
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

    async with get_session() as session:
        try:
            before_languages, after_languages, changed = await SpecialistService(
                SpecialistRepository(session)
            ).update_languages(
                user_id=UUID(user_id),
                specialist_id=UUID(specialist_id),
                language_codes=selected,
            )
        except (SpecialistRegistrationError, ValueError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        if not changed:
            await state.set_state(None)
            await callback.message.answer(
                t("cabinet_profile_no_changes", language),
                reply_markup=specialist_edit_keyboard(language),
            )
            await callback.answer()
            return

        await EventRepository(session).create_event(
            tenant_id=UUID(tenant_id),
            user_id=UUID(user_id),
            event_type="change_submitted",
            entity_type="specialist",
            entity_id=UUID(specialist_id),
            payload={
                "field": "languages",
                "before": before_languages,
                "after": after_languages,
            },
            platform="telegram",
        )
        await session.commit()

    await state.set_state(None)
    await callback.message.answer(
        t("cabinet_profile_updated", language),
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
            await EventRepository(session).create_event(
                tenant_id=tenant_id,
                user_id=user.id,
                event_type="change_blocked",
                entity_type="specialist",
                entity_id=specialist.id,
                payload={
                    "field": field,
                    "reason": "critical_profile_change_requires_pending_schema",
                },
                platform="telegram",
            )
            await session.commit()

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
            await EventRepository(session).create_event(
                tenant_id=tenant_id,
                user_id=user.id,
                event_type="change_blocked",
                entity_type="specialist",
                entity_id=specialist.id,
                payload={
                    "field": field,
                    "reason": "critical_profile_change_requires_pending_schema",
                    "source": "stale_fsm_state",
                },
                platform="telegram",
            )
            await session.commit()

    await state.clear()
    await message.answer(
        t("cabinet_critical_edit_blocked", language),
        reply_markup=specialist_edit_keyboard(language),
    )

@billing_router.callback_query(F.data == "CAB_EDIT_LOCATION")
async def ask_edit_specialist_location(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    await block_critical_profile_edit(
        callback,
        state,
        field="location",
        language=language,
    )

@billing_router.callback_query(
    F.data.in_({"CAB_LOC_MANUAL", "CAB_LOC_COUNTRY", "CAB_LOC_GEO", "CAB_PROF_DONE"})
    | F.data.startswith("CAB_GEO_PLACE:")
    | F.data.startswith("CAB_GEO_COUNTRY:")
    | F.data.startswith("CAB_COUNTRY_PLACE:")
    | F.data.startswith("CAB_CAT:")
    | F.data.startswith("CAB_PROF:")
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
            await RateLimitService(
                RateLimitRepository(session)
            ).ensure_geo_change_allowed(
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
            )

            place = await GeoService(GeoRepository(session)).confirm_place(candidate)

            specialist = await SpecialistService(
                SpecialistRepository(session)
            ).update_profile(
                SpecialistProfileUpdateData(
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(user_id),
                    specialist_id=UUID(specialist_id),
                    country_id=place.country_id,
                    city_id=place.city_id,
                    latitude=place.latitude,
                    longitude=place.longitude,
                    service_radius_km=25,
                )
            )

            await EventRepository(session).create_event(
                event_type="geo_change",
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
                entity_type="city",
                entity_id=place.city_id,
                payload={
                    "source": "specialist_profile_edit",
                    "specialist_id": str(specialist.id),
                    "country_id": str(place.country_id),
                },
                platform="telegram",
            )
            await session.commit()

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

@billing_router.callback_query(F.data.startswith("CAB_GEO_COUNTRY:"))
@billing_router.callback_query(F.data.startswith("CAB_COUNTRY_PLACE:"))
async def choose_specialist_country_update(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()

    if (callback.data or "").startswith("CAB_COUNTRY_PLACE:"):
        candidates = data.get("cabinet_country_candidates") or []
    else:
        candidates = data.get("cabinet_geo_candidates") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
        candidate = candidates[index]
        place_candidate = GeoPlaceCandidate.from_state(candidate)
    except (IndexError, TypeError, ValueError, KeyError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    specialist_id = data.get("cabinet_specialist_id")

    if not user_id or not tenant_id or not specialist_id:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        await state.clear()
        return

    if not place_candidate.country_code or len(place_candidate.country_code) != 2:
        await callback.answer(t("cabinet_geo_candidates_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await RateLimitService(
                RateLimitRepository(session)
            ).ensure_geo_change_allowed(
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
            )

            country = await GeoRepository(session).ensure_country(place_candidate)

            specialist = await SpecialistService(
                SpecialistRepository(session)
            ).update_profile(
                SpecialistProfileUpdateData(
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(user_id),
                    specialist_id=UUID(specialist_id),
                    country_id=country.id,
                    city_id=None,
                    latitude=None,
                    longitude=None,
                    service_radius_km=0,
                    clear_city=True,
                    clear_coordinates=True,
                )
            )

            await EventRepository(session).create_event(
                event_type="geo_change",
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
                entity_type="country",
                entity_id=country.id,
                payload={
                    "source": "specialist_profile_edit",
                    "specialist_id": str(specialist.id),
                    "country_id": str(country.id),
                    "whole_country": True,
                },
                platform="telegram",
            )
            await session.commit()

    except RateLimitError:
        await callback.answer(t("error_rate_limited", language), show_alert=True)
        return
    except SpecialistRegistrationError as exc:
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

@billing_router.callback_query(F.data == "CAB_EDIT_CATEGORY")
async def ask_edit_specialist_category(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    user, specialist, tenant_id = await get_current_specialist_for_telegram(callback.from_user.id)

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        categories = await SpecialistRepository(session).list_active_categories(limit=50)

    await state.update_data(
        cabinet_specialist_id=str(specialist.id),
        cabinet_tenant_id=str(tenant_id),
        cabinet_user_id=str(user.id),
        cabinet_category_ids=[str(item.id) for item in categories],
    )
    await state.set_state(SpecialistCabinetFSM.choosing_category)

    selected_professions = (await state.get_data()).get("cabinet_selected_professions") or []

    await callback.message.answer(
        cabinet_category_prompt_text(selected_professions, language),
        reply_markup=cabinet_category_keyboard(
            items=categories,
            selected_professions=selected_professions,
            language=language,
        ),
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
        cabinet_profession_ids=[str(item.id) for item in professions],
    )
    await state.set_state(SpecialistCabinetFSM.choosing_profession)

    await callback.message.answer(
        cabinet_profession_prompt_text(selected_professions, language),
        reply_markup=cabinet_profession_multi_keyboard(
            items=professions,
            selected_ids=selected_profession_ids,
            language=language,
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

@billing_router.callback_query(F.data.startswith("CAB_PROF:"))
async def choose_specialist_profession_update(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()
    profession_ids = data.get("cabinet_profession_ids") or []
    category_id = data.get("cabinet_pending_category_id")
    category_name = data.get("cabinet_pending_category_name")
    selected_profession_ids = data.get("cabinet_selected_profession_ids") or []
    selected_professions = data.get("cabinet_selected_professions") or []

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

    await callback.message.answer(
        cabinet_profession_prompt_text(selected_professions, language),
        reply_markup=cabinet_profession_multi_keyboard(
            items=professions,
            selected_ids=selected_profession_ids,
            language=language,
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
            specialist = await SpecialistRepository(session).replace_specialist_professions(
                specialist_id=UUID(specialist_id),
                user_id=UUID(user_id),
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

    await state.set_state(None)
    await callback.message.answer(
        t("cabinet_profile_updated", language),
        reply_markup=specialist_edit_keyboard(language),
    )
    await callback.answer()

def cabinet_category_prompt_text(
    selected_professions: list[dict],
    language: str,
) -> str:
    if not selected_professions:
        return t("cabinet_choose_direction", language)

    return (
        f"{t('cabinet_choose_direction', language)}\n\n"
        f"{t('spec_selected_professions_title', language)}\n"
        f"{cabinet_selected_professions_text(selected_professions, language)}"
    )


def cabinet_category_keyboard(
    *,
    items,
    selected_professions: list[dict],
    language: str,
) -> InlineKeyboardMarkup:
    keyboard = indexed_items_keyboard(
        items,
        prefix="CAB_CAT",
        language=language,
    )

    rows = list(keyboard.inline_keyboard)

    if selected_professions:
        rows.insert(
            -1,
            [
                InlineKeyboardButton(
                    text=t("spec_profession_done_btn", language),
                    callback_data="CAB_PROF_DONE",
                )
            ],
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def save_specialist_profile_update(
    *,
    message: Message,
    state: FSMContext,
    display_name: str | None = None,
    short_description: str | None = None,
    contact_text: str | None = None,
    category_id: UUID | None = None,
    profession_id: UUID | None = None,
):
    data = await state.get_data()
    language = await get_billing_interface_language(message.from_user.id, message.from_user.language_code)

    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    specialist_id = data.get("cabinet_specialist_id")

    if not user_id or not tenant_id or not specialist_id:
        await message.answer(t("cabinet_profile_not_found", language))
        await state.clear()
        return

    field_name = None
    before_value = None
    after_value = None

    try:
        async with get_session() as session:
            db_specialist = await session.get(Specialist, UUID(specialist_id))
            if not db_specialist or db_specialist.user_id != UUID(user_id):
                await message.answer(t("cabinet_profile_not_found", language))
                await state.clear()
                return

            if display_name is not None:
                field_name = "display_name"
                before_value = db_specialist.display_name
                after_value = display_name
            elif short_description is not None:
                field_name = "short_description"
                before_value = db_specialist.short_description
                after_value = short_description
            elif contact_text is not None:
                field_name = "contact_text"
                before_value = (db_specialist.extra_metadata or {}).get("contact_text")
                after_value = contact_text
            if field_name and before_value == after_value:
                await state.set_state(None)
                await message.answer(
                    t("cabinet_profile_no_changes", language),
                    reply_markup=specialist_edit_keyboard(language),
                )
                return
            specialist = await SpecialistService(
                SpecialistRepository(session)
            ).update_profile(
                SpecialistProfileUpdateData(
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(user_id),
                    specialist_id=UUID(specialist_id),
                    display_name=display_name,
                    short_description=short_description,
                    contact_text=contact_text,
                    category_id=category_id,
                    profession_id=profession_id,
                )
            )
            if field_name:
                await EventRepository(session).create_event(
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(user_id),
                    event_type="change_submitted",
                    entity_type="specialist",
                    entity_id=UUID(specialist_id),
                    payload={
                        "field": field_name,
                        "before": before_value,
                        "after": after_value,
                    },
                    platform="telegram",
                )
                await session.commit()
    except SpecialistRegistrationError as exc:
        logger.warning(
            "cabinet_profile_update_failed telegram_id=%s specialist_id=%s error=%s",
            message.from_user.id,
            specialist_id,
            exc,
        )
        await message.answer(
            t("cabinet_profile_update_failed", language).format(error=str(exc)),
            reply_markup=specialist_edit_keyboard(language),
        )
        return

    logger.info(
        "cabinet_profile_updated telegram_id=%s specialist_id=%s",
        message.from_user.id,
        specialist.id,
    )

    await state.set_state(None)
    await message.answer(
        t("cabinet_profile_updated", language),
        reply_markup=specialist_edit_keyboard(language),
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
        t("billing_payment_claimed", language).format(status=result.status),
        reply_markup=billing_menu_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("BETA_DISABLED:"))
async def beta_disabled(callback: CallbackQuery):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )
    feature = (callback.data or "").split(":", 1)[1] if ":" in (callback.data or "") else "unknown"

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if user_id and tenant_id:
        async with get_session() as session:
            await EventRepository(session).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="placeholder_opened",
                entity_type="feature",
                payload={
                    "feature": feature,
                    "source": "specialist_cabinet",
                },
                platform="telegram",
            )
            await session.commit()

    await callback.message.answer(
        t("feature_disabled_beta_message", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
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
        ),
    )
    await callback.answer()

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
        if len(parts) >= 2 and parts[1] in {"new", "active", "archive", "hidden"}:
            view = parts[1]
        if len(parts) >= 3 and parts[2].isdigit():
            page = int(parts[2])

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    async with get_session() as session:
        items = await ContactChatService(
            ContactChatRepository(session)
        ).list_client_threads(
            user_id=user_id,
            view=view,
            limit=CLIENT_DIALOGS_PAGE_SIZE,
            offset=page * CLIENT_DIALOGS_PAGE_SIZE,
            language=language,
        )

        await EventRepository(session).create_event(
            event_type="dialogs_opened",
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type="conversation_thread",
            payload={
                "view": view,
                "page": page,
                "items_count": len(items),
                "participant_role": "client",
            },
            platform="telegram",
        )
        await session.commit()

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
        format_client_dialogs_text(items, language),
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

    await state.update_data(active_thread_id=thread_id)

    await callback.message.answer(
        format_client_thread_detail_text(detail, language),
        reply_markup=contact_thread_keyboard(language),
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

@billing_router.callback_query(F.data.startswith("CLIENT_REQUEST_DIALOG:"))
async def open_client_request_dialog(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    data = await state.get_data()
    thread_ids = data.get("client_request_thread_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(thread_ids) or not thread_ids[index]:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await send_client_thread_detail(
        callback=callback,
        state=state,
        thread_id=thread_ids[index],
        language=language,
    )

@billing_router.callback_query(F.data == "CLIENT_REQUESTS")
@billing_router.callback_query(F.data.startswith("CLIENT_REQUESTS:"))
async def show_client_requests(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    page = 0
    if callback.data and callback.data.startswith("CLIENT_REQUESTS:"):
        parts = callback.data.split(":")
        if len(parts) >= 2 and parts[1].isdigit():
            page = int(parts[1])

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    async with get_session() as session:
        items = await ContactChatService(
            ContactChatRepository(session)
        ).list_client_requests(
            user_id=user_id,
            limit=CLIENT_REQUESTS_PAGE_SIZE,
            offset=page * CLIENT_REQUESTS_PAGE_SIZE,
            language=language,
        )

        await EventRepository(session).create_event(
            event_type="request_list",
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type="contact_request",
            payload={
                "page": page,
                "items_count": len(items),
            },
            platform="telegram",
        )
        await session.commit()

    await state.update_data(
        client_request_ids=[str(item.contact_request_id) for item in items],
        client_request_thread_ids=[
            str(item.thread_id) if item.thread_id else None for item in items
        ],
        client_requests_page=page,
    )

    await callback.message.answer(
        format_client_requests_text(items, language),
        reply_markup=client_requests_keyboard(
            items_count=len(items),
            page=page,
            language=language,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("CLIENT_REQUEST_CANCEL:"))
async def cancel_client_request(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    data = await state.get_data()
    request_ids = data.get("client_request_ids") or []
    page = int(data.get("client_requests_page") or 0)

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(request_ids):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await ContactChatService(
                ContactChatRepository(session)
            ).cancel_contact_request(
                contact_request_id=UUID(request_ids[index]),
                actor_user_id=user_id,
                tenant_id=tenant_id,
            )
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(t("client_request_cancelled", language))
    await callback.answer()

    callback.data = f"CLIENT_REQUESTS:{page}"
    await show_client_requests(callback, state)

@billing_router.callback_query(F.data.startswith("CLIENT_REQUEST_OPEN:"))
async def open_client_request_card(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    data = await state.get_data()
    request_ids = data.get("client_request_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(request_ids):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            detail = await ContactChatService(
                ContactChatRepository(session)
            ).get_client_request_detail(
                contact_request_id=UUID(request_ids[index]),
                user_id=user_id,
                language=language,
            )

            await EventRepository(session).create_event(
                event_type="request_viewed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="contact_request",
                entity_id=detail.contact_request_id,
                payload={
                    "thread_id": str(detail.thread_id) if detail.thread_id else None,
                    "status": detail.status,
                },
                platform="telegram",
            )
            await session.commit()
    except Exception:
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    await state.update_data(
        active_contact_request_id=str(detail.contact_request_id),
        active_thread_id=str(detail.thread_id) if detail.thread_id else None,
    )
    await callback.message.answer(
        format_client_request_detail_text(detail, language),
        reply_markup=client_request_card_keyboard(
            request_id=str(detail.contact_request_id),
            has_thread=bool(detail.thread_id),
            can_cancel=detail.status in {"new", "accepted"},
            can_finish=detail.status in {"accepted", "reviewed"},
            language=language,
        ),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("CLIENT_REQUEST_CARD_DIALOG:"))
async def open_client_request_card_dialog(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        contact_request_id = UUID((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            detail = await ContactChatService(
                ContactChatRepository(session)
            ).get_client_request_detail(
                contact_request_id=contact_request_id,
                user_id=user_id,
                language=language,
            )
    except Exception:
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    if not detail.thread_id:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await send_client_thread_detail(
        callback=callback,
        state=state,
        thread_id=str(detail.thread_id),
        language=language,
    )


@billing_router.callback_query(F.data.startswith("CLIENT_REQUEST_CARD_CANCEL:"))
async def cancel_client_request_card(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        contact_request_id = UUID((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await ContactChatService(
                ContactChatRepository(session)
            ).cancel_contact_request(
                contact_request_id=contact_request_id,
                actor_user_id=user_id,
                tenant_id=tenant_id,
            )
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(t("client_request_cancelled", language))
    await callback.answer()

    callback.data = "CLIENT_REQUESTS"
    await show_client_requests(callback, state)
@billing_router.callback_query(F.data.startswith("CLIENT_REQUEST_CARD_FINISH:"))
async def finish_client_request_card(callback: CallbackQuery, state: FSMContext):
    language = await get_billing_interface_language(
        callback.from_user.id,
        callback.from_user.language_code,
    )

    try:
        contact_request_id = UUID((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            detail = await ContactChatService(
                ContactChatRepository(session)
            ).get_client_request_detail(
                contact_request_id=contact_request_id,
                user_id=user_id,
                language=language,
            )

            if not detail.thread_id:
                await callback.answer(t("contact_thread_not_found", language), show_alert=True)
                return

            await ContactChatService(
                ContactChatRepository(session)
            ).complete_thread(
                thread_id=detail.thread_id,
                actor_user_id=user_id,
            )
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(t("contact_thread_completed", language))
    await callback.answer()

    callback.data = f"CLIENT_REQUEST_OPEN:0"
    await callback.message.answer(
        t("client_request_status_updated", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("client_request_back_to_requests", language),
                        callback_data="CLIENT_REQUESTS",
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