import logging
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
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

from database.models import User
from database.repositories.contact import ContactChatRepository
from database.repositories.event import EventRepository
from database.repositories.geo_repository import GeoRepository
from database.repositories.rate_limit import RateLimitRepository
from database.repositories.search import SpecialistSearchRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from database.repositories.moderation import ModerationRepository
from database.repositories.translation import TranslationRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard
from services.contact_chat import ContactChatError, ContactChatService
from services.geo_search import GeoSearchService, SpecialistPublicCard
from services.geo_service import GeoService, GeoServiceError
from services.moderation import ModerationError, ModerationService
from services.rate_limit import RateLimitError, RateLimitService
from services.translation import TranslationError, TranslationService
from ui.texts import t
from database.repositories.favorites import FavoriteRepository

search_router = Router()
logger = logging.getLogger(__name__)

PER_PAGE = 5
DEFAULT_RADIUS_KM = 25


class SpecialistSearchFSM(StatesGroup):
    choosing_category = State()
    choosing_profession = State()
    entering_location_query = State()
    choosing_geo_place = State()
    waiting_geo = State()
    choosing_filters = State()
    viewing_results = State()
    entering_contact_message = State()
    confirming_contact_message = State()
    entering_thread_message = State()
    entering_report_comment = State()

def normalize_language(language: str | None) -> str:
    return language if language in {"ru", "en", "pt", "uk"} else "ru"


def item_name(item, language: str = "ru") -> str:
    localized = getattr(item, f"name_{language}", None)
    return localized or getattr(item, "name_ru", None) or getattr(item, "name", None) or str(item.id)


def work_format_label(value: str | None, language: str) -> str:
    labels = {
        None: t("search_filter_any", language),
        "at_client": t("search_work_at_client", language),
        "at_specialist": t("search_work_at_specialist", language),
        "remote": t("search_work_remote", language),
        "mixed": t("search_work_mixed", language),
    }
    return labels.get(value, value or t("search_filter_any", language))


def language_filter_label(value: str | None, language: str) -> str:
    labels = {
        None: t("search_filter_any", language),
        "ru": t("search_language_ru", language),
        "pt": t("search_language_pt", language),
        "en": t("search_language_en", language),
    }
    return labels.get(value, value or t("search_filter_any", language))


def sort_label(value: str | None, language: str) -> str:
    labels = {
        "distance": t("search_sort_distance", language),
        "relevance": t("search_sort_relevance", language),
    }
    return labels.get(value or "distance", value or "distance")


def geo_candidate_label(candidate: dict, index: int) -> str:
    name = candidate.get("name") or candidate.get("display_name") or "-"
    country = candidate.get("country_name") or candidate.get("country_code") or "-"
    place_type = candidate.get("place_type") or candidate.get("osm_type") or "place"
    return f"{index + 1}. {name}, {country} - {place_type}"

def dedupe_geo_candidate_states(candidates: list[dict], limit: int = 8) -> list[dict]:
    unique_candidates = []
    seen = set()

    for candidate in candidates:
        name = str(candidate.get("name") or "").strip().lower()
        country_code = str(candidate.get("country_code") or "").strip().upper()
        country_name = str(candidate.get("country_name") or "").strip().lower()
        place_type = str(candidate.get("place_type") or "").strip().lower()
        display_name = str(candidate.get("display_name") or "").strip().lower()

        human_key = (
            name,
            country_code or country_name,
            place_type,
        )

        fallback_key = (
            display_name,
            place_type,
        )

        dedupe_key = human_key if name else fallback_key

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        unique_candidates.append(candidate)

        if len(unique_candidates) >= limit:
            break

    return unique_candidates

async def show_callback_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


async def get_requester_context(platform_user_id: int | str) -> tuple[UUID | None, UUID | None]:
    async with get_session() as session:
        account = await UserRepository(session).get_by_platform_account(
            "telegram",
            str(platform_user_id),
        )
        if not account:
            return None, None

        user = await session.get(User, account.user_id)
        if not user:
            return account.user_id, None

        return user.id, user.tenant_id


def callback_index(callback: CallbackQuery) -> int | None:
    try:
        return int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return None

def telegram_chat_id(platform_user_id: str | int | None) -> int | None:
    if platform_user_id is None:
        return None

    try:
        return int(str(platform_user_id).strip())
    except (TypeError, ValueError):
        return None

def paged_keyboard(
    *,
    items,
    item_prefix: str,
    page_prefix: str,
    page: int,
    language: str,
    back_callback: str = "search_filters",
) -> InlineKeyboardMarkup:
    start = page * PER_PAGE
    end = start + PER_PAGE

    rows = []
    for index, item in enumerate(items[start:end], start=start):
        rows.append(
            [
                InlineKeyboardButton(
                    text=item_name(item, language),
                    callback_data=f"{item_prefix}:{index}",
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"{page_prefix}:{page - 1}"))
    if end < len(items):
        nav.append(InlineKeyboardButton(text=">", callback_data=f"{page_prefix}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data=back_callback)])
    rows.append([InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def profession_keyboard(
    *,
    professions,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    keyboard = paged_keyboard(
        items=professions,
        item_prefix="search_profession",
        page_prefix="search_professions_page",
        page=page,
        language=language,
        back_callback="search_filters",
    )
    keyboard.inline_keyboard.insert(
        0,
        [
            InlineKeyboardButton(
                text=t("search_all_professions", language),
                callback_data="search_profession_all",
            )
        ],
    )
    return keyboard


def results_keyboard(page: int, has_next: bool, results_count: int, language: str) -> InlineKeyboardMarkup:
    rows = []

    for index in range(results_count):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{page * PER_PAGE + index + 1}. {t('search_details_btn', language)}",
                    callback_data=f"search_result:{index}",
                ),
                InlineKeyboardButton(
                    text=t("contact", language),
                    callback_data=f"search_result_contact:{index}",
                ),
            ]
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"search_results_page:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"search_results_page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")])
    rows.append([InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def card_keyboard(language: str, results_page: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("contact", language), callback_data="search_contact_pending")],
            [InlineKeyboardButton(text=t("favorite", language), callback_data="search_favorite_pending")],
            [InlineKeyboardButton(text=t("report", language), callback_data="search_report_pending")],
            [InlineKeyboardButton(text=t("search_back", language), callback_data=f"search_results_page:{results_page}")],
            [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

def complaint_reason_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("complaint_reason_fake", language),
                    callback_data="search_report_reason:fake",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("complaint_reason_contact", language),
                    callback_data="search_report_reason:contact",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("complaint_reason_abuse", language),
                    callback_data="search_report_reason:abuse",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("complaint_reason_other", language),
                    callback_data="search_report_reason:other",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_back", language),
                    callback_data="search_contact_cancel",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="search_menu",
                )
            ],
        ]
    )

def contact_request_action_keyboard(contact_token: str, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("contact_accept_btn", language),
                    callback_data=f"contact_accept:{contact_token}",
                ),
                InlineKeyboardButton(
                    text=t("contact_reject_btn", language),
                    callback_data=f"contact_reject:{contact_token}",
                ),
            ]
        ]
    )


def contact_thread_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("contact_reply_btn", language), callback_data="contact_reply")],
            [InlineKeyboardButton(text=t("contact_show_original_btn", language), callback_data="contact_show_original")],
            [InlineKeyboardButton(text=t("contact_finish_btn", language), callback_data="contact_finish")],
            [InlineKeyboardButton(text=t("contact_report_btn", language), callback_data="search_report_pending")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

def contact_message_confirm_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("contact_send_confirm", language), callback_data="contact_send_confirm")],
            [InlineKeyboardButton(text=t("search_back", language), callback_data="contact_disclaimer_continue")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

async def translate_message_for_notification(
    *,
    session,
    message_id: UUID,
    receiver_user_id: UUID,
) -> tuple[str, bool, str]:
    try:
        translation_service = TranslationService(TranslationRepository(session))
        translated = await translation_service.translate_message(message_id)
        display = await translation_service.get_message_for_receiver(
            message_id=message_id,
            receiver_user_id=receiver_user_id,
        )
        return display.display_text, translated.used_translation, translated.translation_status
    except TranslationError:
        message = await TranslationRepository(session).get_message(message_id)
        return (message.original_text if message else ""), False, "failed"


def format_search_filters_summary(data: dict, language: str) -> str:
    category = data.get("category_name") or t("search_filter_not_set", language)
    profession = data.get("profession_name") or t("search_filter_not_set", language)
    city = data.get("city_name") or t("search_filter_not_set", language)
    radius = (
        t("search_radius_country", language)
        if data.get("country_wide")
        else f"{data.get('radius_km') or DEFAULT_RADIUS_KM} km"
    )
    language_code = language_filter_label(data.get("language_code"), language)
    work_format = work_format_label(data.get("work_format"), language)
    sort_by = sort_label(data.get("sort_by"), language)
    price_min = data.get("price_min")
    price_max = data.get("price_max")

    if price_min is None and price_max is None:
        price = t("search_filter_not_set", language)
    elif price_max is not None and price_min is None:
        price = t("search_filter_price_up_to", language).format(amount=price_max)
    elif price_min is not None and price_max is None:
        price = t("search_filter_price_from", language).format(amount=price_min)
    else:
        price = f"{price_min}-{price_max}"

    return (
        f"{t('search_filters_title', language)}\n\n"
        f"{t('search_filter_category_label', language)}: {category}\n"
        f"{t('search_filter_profession_label', language)}: {profession}\n"
        f"{t('search_filter_location_label', language)}: {city}\n"
        f"{t('search_filter_radius_label', language)}: {radius}\n"
        f"{t('search_filter_work_label', language)}: {work_format}\n"
        f"{t('search_filter_language_label', language)}: {language_code}\n"
        f"{t('search_filter_price_label', language)}: {price}\n"
        f"{t('search_filter_sort_label', language)}: {sort_by}"
    )


def search_filters_keyboard(data: dict, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("search_filter_category", language), callback_data="search_filter_category"),
                InlineKeyboardButton(text=t("search_filter_profession", language), callback_data="search_filter_profession"),
            ],
            [
                InlineKeyboardButton(text=t("search_filter_location", language), callback_data="search_filter_location"),
                InlineKeyboardButton(text=t("search_filter_radius", language), callback_data="search_filter_radius"),
            ],
            [
                InlineKeyboardButton(text=t("search_filter_work_format", language), callback_data="search_filter_work_format"),
                InlineKeyboardButton(text=t("search_filter_language", language), callback_data="search_filter_language"),
            ],
            [
                InlineKeyboardButton(text=t("search_filter_price", language), callback_data="search_filter_price"),
                InlineKeyboardButton(text=t("search_filter_sort", language), callback_data="search_filter_sort"),
            ],
            [InlineKeyboardButton(text=t("search_reset_filters", language), callback_data="search_reset_filters")],
            [InlineKeyboardButton(text=t("search_show_results", language), callback_data="search_show_results")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

def search_location_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("search_location_city", language), callback_data="search_location_city")],
            [InlineKeyboardButton(text=t("search_location_geo", language), callback_data="search_location_geo")],
            [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

@search_router.callback_query(F.data == "search_filter_location")
async def open_location_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await show_callback_message(
        callback,
        t("search_location_prompt", language),
        search_location_keyboard(language),
    )
    await callback.answer()

def search_radius_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="5 km", callback_data="search_radius:5"),
                InlineKeyboardButton(text="10 km", callback_data="search_radius:10"),
                InlineKeyboardButton(text="25 km", callback_data="search_radius:25"),
            ],
            [
                InlineKeyboardButton(text="50 km", callback_data="search_radius:50"),
                InlineKeyboardButton(text="100 km", callback_data="search_radius:100"),
                InlineKeyboardButton(text=t("search_radius_country", language), callback_data="search_radius:country"),
            ],
            [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def search_work_format_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("search_filter_any", language), callback_data="search_work:any")],
            [InlineKeyboardButton(text=t("search_work_at_client", language), callback_data="search_work:at_client")],
            [InlineKeyboardButton(text=t("search_work_at_specialist", language), callback_data="search_work:at_specialist")],
            [InlineKeyboardButton(text=t("search_work_remote", language), callback_data="search_work:remote")],
            [InlineKeyboardButton(text=t("search_work_mixed", language), callback_data="search_work:mixed")],
            [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def search_language_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("search_filter_any", language), callback_data="search_lang:any"),
                InlineKeyboardButton(text=t("search_language_ru", language), callback_data="search_lang:ru"),
            ],
            [
                InlineKeyboardButton(text=t("search_language_pt", language), callback_data="search_lang:pt"),
                InlineKeyboardButton(text=t("search_language_en", language), callback_data="search_lang:en"),
            ],
            [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def search_price_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("search_filter_price_any", language), callback_data="search_price:any")],
            [InlineKeyboardButton(text=t("search_filter_price_up_to_25", language), callback_data="search_price:0_25")],
            [InlineKeyboardButton(text=t("search_filter_price_up_to_50", language), callback_data="search_price:0_50")],
            [InlineKeyboardButton(text=t("search_filter_price_up_to_100", language), callback_data="search_price:0_100")],
            [InlineKeyboardButton(text=t("search_filter_price_manual_later", language), callback_data="search_price:any")],
            [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def search_sort_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("search_sort_distance", language), callback_data="search_sort:distance")],
            [InlineKeyboardButton(text=t("search_sort_relevance", language), callback_data="search_sort:relevance")],
            [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def search_geo_candidates_keyboard(candidates: list[dict], language: str) -> InlineKeyboardMarkup:
    rows = []
    for index, candidate in enumerate(candidates[:8]):
        rows.append(
            [
                InlineKeyboardButton(
                    text=geo_candidate_label(candidate, index),
                    callback_data=f"search_geo_place:{index}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")])
    rows.append([InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def next_empty_radius_suggestion(data: dict) -> tuple[int | None, bool]:
    if data.get("country_wide"):
        return None, True

    radius = int(data.get("radius_km") or DEFAULT_RADIUS_KM)

    if radius < 25:
        return 25, False

    if radius < 50:
        return 50, False

    if radius < 100:
        return 100, False

    return None, True


def empty_results_keyboard(data: dict, language: str) -> InlineKeyboardMarkup:
    next_radius, next_country_wide = next_empty_radius_suggestion(data)

    rows = []

    if next_radius is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("search_empty_increase_radius_to", language).format(
                        radius=next_radius,
                    ),
                    callback_data="search_empty_increase_radius",
                )
            ]
        )
    elif next_country_wide and not data.get("country_wide"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("search_empty_increase_radius_country", language),
                    callback_data="search_empty_increase_radius",
                )
            ]
        )

    rows.extend(
        [
            [InlineKeyboardButton(text=t("search_empty_reset_profession", language), callback_data="search_empty_reset_profession")],
            [InlineKeyboardButton(text=t("search_empty_reset_all", language), callback_data="search_reset_filters")],
            [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_empty_results_text(data: dict, language: str) -> str:
    return (
        f"{t('search_empty_summary', language)}\n\n"
        f"{format_search_filters_summary(data, language)}"
    )


def format_specialist_result(result, index: int, language: str) -> str:
    specialist = result.specialist

    price = t("search_price_not_set", language)
    if specialist.price_from and specialist.price_to:
        price = f"{specialist.price_from}-{specialist.price_to} {specialist.currency}"
    elif specialist.price_from:
        price = f"{t('search_price_from', language)} {specialist.price_from} {specialist.currency}"

    distance = f"{result.distance_km:.1f} km" if result.distance_km is not None else None
    city = result.city_name or t("search_filter_not_set", language)
    profession = result.profession_name or t("search_filter_not_set", language)
    languages = ", ".join(result.languages) if result.languages else t("search_filter_not_set", language)
    location_parts = [city]
    if distance:
        location_parts.append(distance)

    work_format = work_format_label(getattr(specialist, "work_format", None), language)
    status = t("search_verified_label", language) if specialist.is_verified else t("search_status_active", language)

    description = (specialist.short_description or "").strip()
    if len(description) > 160:
        description = description[:157].rstrip() + "..."

    return (
        f"{index}. {specialist.display_name}\n"
        f"{profession} · {' · '.join(location_parts)}\n"
        f"{t('search_filter_work_label', language)}: {work_format}\n"
        f"{t('search_filter_language_label', language)}: {languages}\n"
        f"{t('search_filter_price_label', language)}: {price}\n"
        f"{t('search_status_label', language)}: {status}\n\n"
        f"{description}"
    )

def format_public_card(card: SpecialistPublicCard, language: str) -> str:
    price = t("search_price_not_set", language)
    if card.price_from and card.price_to:
        price = f"{card.price_from}-{card.price_to} {card.currency}"
    elif card.price_from:
        price = f"{t('search_price_from', language)} {card.price_from} {card.currency}"

    labels = []
    if card.is_verified:
        labels.append(t("search_verified_label", language))
    if card.is_premium:
        labels.append(t("search_premium_label", language))

    label_text = f" ({', '.join(labels)})" if labels else ""
    languages = ", ".join(card.languages) if card.languages else t("search_filter_not_set", language)
    distance = f"\n{t('search_distance', language)}: {card.distance_km:.1f} km" if card.distance_km is not None else ""
    city = card.city_name or t("search_filter_not_set", language)
    category = card.category_name or t("search_filter_not_set", language)
    profession = card.profession_name or t("search_filter_not_set", language)
    work_format = work_format_label(card.work_format, language)
    status = t("search_verified_label", language) if card.is_verified else t("search_status_active", language)
    services = ", ".join(card.service_titles) if card.service_titles else t("search_filter_not_set", language)
    return (
        f"{card.display_name}{label_text}\n\n"
        f"{t('search_filter_category_label', language)}: {category}\n"
        f"{t('search_filter_profession_label', language)}: {profession}\n"
        f"{t('search_filter_location_label', language)}: {city}"
        f"{distance}\n"
        f"{t('search_filter_work_label', language)}: {work_format}\n"
        f"{t('search_services_label', language)}: {services}\n"
        f"{t('search_filter_price_label', language)}: {price}\n"
        f"{t('search_filter_language_label', language)}: {languages}\n"
        f"{t('search_status_label', language)}: {status}\n"
        f"{t('search_rating', language)}: {card.rating} ({card.reviews_count})\n\n"
        f"{card.short_description}\n\n"
        f"{t('search_legal_warning', language)}"
    )
async def show_filters(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await show_callback_message(
        callback,
        format_search_filters_summary(data, language),
        search_filters_keyboard(data, language),
    )
    await state.set_state(SpecialistSearchFSM.choosing_filters)
    await callback.answer()


async def render_results(
    *,
    event: CallbackQuery | Message,
    state: FSMContext,
    page: int,
):
    data = await state.get_data()
    language = normalize_language(data.get("user_language"))

    category_id = UUID(data["category_id"]) if data.get("category_id") else None
    profession_id = UUID(data["profession_id"]) if data.get("profession_id") else None
    city_id = UUID(data["city_id"]) if data.get("city_id") else None
    country_id = UUID(data["country_id"]) if data.get("country_id") else None
    has_geo = data.get("latitude") is not None and data.get("longitude") is not None

    if not city_id and not has_geo:
        if isinstance(event, CallbackQuery):
            await show_callback_message(
                event,
                t("search_location_prompt", language),
                search_location_keyboard(language),
            )
            await state.set_state(SpecialistSearchFSM.choosing_filters)
            await event.answer()
        else:
            await event.answer(
                t("search_location_prompt", language),
                reply_markup=search_location_keyboard(language),
            )
            await state.set_state(SpecialistSearchFSM.choosing_filters)
        return
    has_geo = data.get("latitude") is not None and data.get("longitude") is not None
    country_wide = bool(data.get("country_wide"))
    language_code = data.get("language_code")
    verified_only = bool(data.get("verified_only"))
    price_min = data.get("price_min")
    price_max = data.get("price_max")
    premium_only = bool(data.get("premium_only"))
    work_format = data.get("work_format")
    rating_min = data.get("rating_min")
    sort_by = data.get("sort_by") or "distance"

    requester_user_id = None
    tenant_id = None
    platform_user_id = event.from_user.id if event.from_user else None
    if platform_user_id is not None:
        requester_user_id, tenant_id = await get_requester_context(platform_user_id)

    async with get_session() as session:
        service = GeoSearchService(SpecialistSearchRepository(session))

        if has_geo:
            results = await service.search_by_radius(
                latitude=float(data["latitude"]),
                longitude=float(data["longitude"]),
                radius_km=float(data.get("radius_km") or DEFAULT_RADIUS_KM),
                category_id=category_id,
                country_id=country_id,
                country_wide=country_wide,
                interface_language=language,
                profession_id=profession_id,
                language_code=language_code,
                verified_only=verified_only,
                limit=PER_PAGE + 1,
                offset=page * PER_PAGE,
                requester_user_id=requester_user_id,
                tenant_id=tenant_id,
                log_event=True,
                price_min=price_min,
                price_max=price_max,
                premium_only=premium_only,
                work_format=work_format,
                rating_min=rating_min,
                sort_by=sort_by,
            )
        elif city_id:
            results = await service.search_by_city(
                city_id=city_id,
                category_id=category_id,
                profession_id=profession_id,
                price_min=price_min,
                price_max=price_max,
                country_id=country_id,
                interface_language=language,
                language_code=language_code,
                verified_only=verified_only,
                premium_only=premium_only,
                work_format=work_format,
                rating_min=rating_min,
                limit=PER_PAGE + 1,
                offset=page * PER_PAGE,
                requester_user_id=requester_user_id,
                tenant_id=tenant_id,
                log_event=True,
                sort_by=sort_by,
            )
        else:
            results = []

    logger.info(
        "search_results_rendered telegram_id=%s results=%s page=%s has_geo=%s city_id=%s category_id=%s profession_id=%s sort_by=%s",
        platform_user_id,
        len(results),
        page,
        has_geo,
        city_id,
        category_id,
        profession_id,
        sort_by,
    )

    has_next = len(results) > PER_PAGE
    visible_results = results[:PER_PAGE]

    await state.update_data(
        results_page=page,
        result_specialist_ids=[str(item.specialist.id) for item in visible_results],
        result_distances=[item.distance_km for item in visible_results],
    )

    if not visible_results:
        text = format_empty_results_text(data, language)
        keyboard = empty_results_keyboard(data, language)
    else:
        start_number = page * PER_PAGE + 1
        text = f"{t('search_results_title', language)}:\n\n" + "\n\n".join(
            format_specialist_result(result, start_number + index, language)
            for index, result in enumerate(visible_results)
        )
        keyboard = results_keyboard(
            page=page,
            has_next=has_next,
            results_count=len(visible_results),
            language=language,
        )

    await state.set_state(SpecialistSearchFSM.viewing_results)

    if isinstance(event, CallbackQuery):
        await show_callback_message(event, text, keyboard)
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard)


@search_router.callback_query(F.data.in_({"M_FIND", "search_start"}))
async def start_search(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    language = normalize_language(callback.from_user.language_code)

    await state.update_data(
        user_language=language,
        category_id=None,
        category_name=None,
        country_wide=False,
        profession_id=None,
        profession_name=None,
        country_id=None,
        city_id=None,
        city_name=None,
        latitude=None,
        longitude=None,
        radius_km=DEFAULT_RADIUS_KM,
        work_format=None,
        language_code=None,
        price_min=None,
        price_max=None,
        sort_by="distance",
        page=0,
    )

    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_filters")
async def back_to_search_filters(callback: CallbackQuery, state: FSMContext):
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_filter_category")
async def open_category_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    async with get_session() as session:
        categories = await SpecialistRepository(session).list_active_categories(limit=100)

    if not categories:
        await callback.message.answer(t("search_categories_missing", language))
        await callback.answer()
        return

    await state.update_data(
        category_ids=[str(category.id) for category in categories],
        category_page=0,
    )

    await show_callback_message(
        callback,
        t("search_choose_category", language),
        paged_keyboard(
            items=categories,
            item_prefix="search_category",
            page_prefix="search_categories_page",
            page=0,
            language=language,
            back_callback="search_filters",
        ),
    )
    await state.set_state(SpecialistSearchFSM.choosing_category)
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_categories_page:"))
async def paginate_categories(callback: CallbackQuery, state: FSMContext):
    page = callback_index(callback)
    if page is None:
        await callback.answer()
        return

    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    async with get_session() as session:
        categories = await SpecialistRepository(session).list_active_categories(limit=100)

    await state.update_data(category_ids=[str(category.id) for category in categories])

    await show_callback_message(
        callback,
        t("search_choose_category", language),
        paged_keyboard(
            items=categories,
            item_prefix="search_category",
            page_prefix="search_categories_page",
            page=page,
            language=language,
            back_callback="search_filters",
        ),
    )
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_category:"))
async def choose_category(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    category_ids = data.get("category_ids") or []

    if index is None or index >= len(category_ids):
        await callback.answer()
        return

    async with get_session() as session:
        category = await SpecialistRepository(session).get_active_category(
            UUID(category_ids[index])
        )

    if not category:
        await callback.message.answer(t("search_category_not_found", language))
        await callback.answer()
        return

    await state.update_data(
        category_id=str(category.id),
        category_name=item_name(category, language),
        profession_id=None,
        profession_name=None,
        page=0,
    )
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_filter_profession")
async def open_profession_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    category_id = UUID(data["category_id"]) if data.get("category_id") else None

    async with get_session() as session:
        if category_id:
            professions = await SpecialistRepository(session).list_active_professions_by_category(
                category_id,
                limit=100,
            )
        else:
            professions = await SpecialistRepository(session).list_active_professions(limit=100)

    if not professions:
        await callback.message.answer(t("search_professions_missing", language))
        await callback.answer()
        return

    await state.update_data(
        profession_ids=[str(profession.id) for profession in professions],
        profession_page=0,
    )

    await show_callback_message(
        callback,
        t("search_choose_profession", language),
        profession_keyboard(
            professions=professions,
            page=0,
            language=language,
        ),
    )
    await state.set_state(SpecialistSearchFSM.choosing_profession)
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_professions_page:"))
async def paginate_professions(callback: CallbackQuery, state: FSMContext):
    page = callback_index(callback)
    if page is None:
        await callback.answer()
        return

    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    category_id = UUID(data["category_id"]) if data.get("category_id") else None

    async with get_session() as session:
        if category_id:
            professions = await SpecialistRepository(session).list_active_professions_by_category(
                category_id,
                limit=100,
            )
        else:
            professions = await SpecialistRepository(session).list_active_professions(limit=100)
    await state.update_data(profession_ids=[str(item.id) for item in professions])

    await show_callback_message(
        callback,
        t("search_choose_profession", language),
        profession_keyboard(
            professions=professions,
            page=page,
            language=language,
        ),
    )
    await callback.answer()


@search_router.callback_query(F.data == "search_profession_all")
async def choose_all_professions(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        profession_id=None,
        profession_name=None,
        page=0,
    )
    await show_filters(callback, state)


@search_router.callback_query(F.data.startswith("search_profession:"))
async def choose_profession(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    profession_ids = data.get("profession_ids") or []

    if index is None or index >= len(profession_ids):
        await callback.answer()
        return

    async with get_session() as session:
        profession = await SpecialistRepository(session).get_active_profession(
            UUID(profession_ids[index])
        )

    if not profession:
        await callback.message.answer(t("search_profession_not_found", language))
        await callback.answer()
        return

    await state.update_data(
        profession_id=str(profession.id),
        profession_name=item_name(profession, language),
        page=0,
    )
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_filter_location")
async def open_location_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await show_callback_message(
    callback,
    t("search_location_prompt", language),
    search_location_keyboard(language),
)
    await callback.answer()


@search_router.callback_query(F.data == "search_location_city")
async def start_location_city_search(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await show_callback_message(
        callback,
        t("search_location_city_prompt", language),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )
    await state.set_state(SpecialistSearchFSM.entering_location_query)
    await callback.answer()


@search_router.message(SpecialistSearchFSM.entering_location_query)
async def receive_location_query(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or message.from_user.language_code)
    query = (message.text or "").strip()

    if len(query) < 2:
        await message.answer(t("search_location_query_too_short", language))
        return

    try:
        async with get_session() as session:
            candidates = await GeoService(
                GeoRepository(session)
            ).search_places(
                query=query,
                language=language,
                limit=8,
            )

        logger.info(
            "search_geo_query_completed telegram_id=%s candidates=%s",
            message.from_user.id,
            len(candidates),
        )
    except GeoServiceError as exc:
        logger.warning(
            "search_geo_query_failed telegram_id=%s error=%s",
            message.from_user.id,
            exc,
        )
        await message.answer(t("search_geo_provider_error", language).format(error=str(exc)))
        return

    if not candidates:
        await message.answer(t("search_geo_candidates_not_found", language))
        return

    candidate_state = dedupe_geo_candidate_states(
    [candidate.to_state() for candidate in candidates],
    limit=8,
)
    await state.update_data(search_geo_candidates=candidate_state)

    await message.answer(
        t("search_geo_candidates_prompt", language),
        reply_markup=search_geo_candidates_keyboard(candidate_state, language),
    )
    await state.set_state(SpecialistSearchFSM.choosing_geo_place)


@search_router.callback_query(F.data == "search_location_geo")
async def start_location_geo_search(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await callback.message.answer(
        t("search_geo_prompt", language),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=t("search_send_geo_btn", language),
                        request_location=True,
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await state.set_state(SpecialistSearchFSM.waiting_geo)
    await callback.answer()


@search_router.message(SpecialistSearchFSM.waiting_geo)
async def receive_geo(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or message.from_user.language_code)

    if not message.location:
        await message.answer(t("search_geo_required", language))
        return

    try:
        async with get_session() as session:
            candidates = await GeoService(
                GeoRepository(session)
            ).nearby_places(
                latitude=message.location.latitude,
                longitude=message.location.longitude,
                language=language,
                limit=4,
            )

        logger.info(
            "search_geo_nearby_completed telegram_id=%s candidates=%s",
            message.from_user.id,
            len(candidates),
        )
    except GeoServiceError as exc:
        logger.warning(
            "search_geo_nearby_failed telegram_id=%s error=%s",
            message.from_user.id,
            exc,
        )
        await message.answer(
            t("search_geo_provider_error", language).format(error=str(exc)),
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if not candidates:
        await message.answer(
            t("search_geo_candidates_not_found", language),
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    candidate_state = dedupe_geo_candidate_states(
    [candidate.to_state() for candidate in candidates],
    limit=4,
)
    await state.update_data(search_geo_candidates=candidate_state)

    await message.answer(
        t("search_geo_nearby_prompt", language),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        t("search_geo_candidates_prompt", language),
        reply_markup=search_geo_candidates_keyboard(candidate_state, language),
    )
    await state.set_state(SpecialistSearchFSM.choosing_geo_place)


@search_router.callback_query(F.data.startswith("search_geo_place:"))
async def choose_search_geo_place(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    candidates = data.get("search_geo_candidates") or []

    if index is None or index >= len(candidates):
        await callback.answer(t("search_geo_candidate_not_found", language), show_alert=True)
        return

    candidate = candidates[index]

    try:
        actor_user_id, tenant_id = await get_requester_context(callback.from_user.id)
        async with get_session() as session:
            if actor_user_id and tenant_id:
                await RateLimitService(
                    RateLimitRepository(session)
                ).ensure_geo_change_allowed(
                    tenant_id=tenant_id,
                    user_id=actor_user_id,
                )

            place = await GeoService(GeoRepository(session)).confirm_place(candidate)

            if actor_user_id and tenant_id:
                await EventRepository(session).create_event(
                    event_type="geo_change",
                    tenant_id=tenant_id,
                    user_id=actor_user_id,
                    entity_type="city",
                    entity_id=place.city_id,
                    payload={
                        "source": "search_filter",
                        "country_id": str(place.country_id),
                    },
                    platform="telegram",
                )
                await session.commit()

        logger.info(
            "search_geo_place_confirmed telegram_id=%s city_id=%s country_id=%s",
            callback.from_user.id,
            place.city_id,
            place.country_id,
        )

    except RateLimitError as exc:
        logger.warning(
            "search_geo_change_rate_limited telegram_id=%s error=%s",
            callback.from_user.id,
            exc,
        )
        await callback.answer(t("error_rate_limited", language), show_alert=True)
        return
    
    except GeoServiceError as exc:
        logger.warning(
            "search_geo_place_confirm_failed telegram_id=%s error=%s",
            callback.from_user.id,
            exc,
        )
        await callback.answer(
            t("search_geo_provider_error", language).format(error=str(exc)),
            show_alert=True,
        )
        return

    await state.update_data(
        country_id=str(place.country_id),
        city_id=str(place.city_id),
        city_name=place.city_name,
        latitude=place.latitude,
        longitude=place.longitude,
        radius_km=data.get("radius_km") or DEFAULT_RADIUS_KM,
        search_geo_candidates=[],
        page=0,
    )
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_filter_radius")
async def open_radius_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    await show_callback_message(callback, t("search_radius_prompt", language), search_radius_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_radius:"))
async def choose_radius(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value == "country":
        await state.update_data(country_wide=True, page=0)
        await show_filters(callback, state)
        return

    try:
        radius_km = int(value)
    except ValueError:
        await callback.answer()
        return

    radius_km = max(5, min(radius_km, 100))
    await state.update_data(radius_km=radius_km, country_wide=False, page=0)
    await show_filters(callback, state)

@search_router.callback_query(F.data == "search_filter_work_format")
async def open_work_format_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    await show_callback_message(callback, t("search_work_prompt", language), search_work_format_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_work:"))
async def choose_work_format_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]
    work_format = None if value == "any" else value

    if work_format not in {None, "at_client", "at_specialist", "remote", "mixed"}:
        await callback.answer()
        return

    await state.update_data(work_format=work_format, page=0)
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_filter_language")
async def open_language_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    await show_callback_message(callback, t("search_language_prompt", language), search_language_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_lang:"))
async def choose_language_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]
    language_code = None if value == "any" else value

    if language_code not in {None, "ru", "pt", "en"}:
        await callback.answer()
        return

    await state.update_data(language_code=language_code, page=0)
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_filter_price")
async def open_price_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    await show_callback_message(callback, t("search_price_prompt", language), search_price_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_price:"))
async def choose_price_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value == "any":
        price_min = None
        price_max = None
    elif value == "0_25":
        price_min = None
        price_max = 25
    elif value == "0_50":
        price_min = None
        price_max = 50
    elif value == "0_100":
        price_min = None
        price_max = 100
    else:
        await callback.answer()
        return

    await state.update_data(price_min=price_min, price_max=price_max, page=0)
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_filter_sort")
async def open_sort_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    await show_callback_message(callback, t("search_sort_prompt", language), search_sort_keyboard(language))
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_sort:"))
async def choose_sort_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value not in {"distance", "relevance"}:
        await callback.answer()
        return

    await state.update_data(sort_by=value, page=0)
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_reset_filters")
async def reset_search_filters(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await state.update_data(
        user_language=language,
        category_id=None,
        category_name=None,
        profession_id=None,
        profession_name=None,
        country_id=None,
        country_wide=False,
        city_id=None,
        city_name=None,
        latitude=None,
        longitude=None,
        radius_km=DEFAULT_RADIUS_KM,
        work_format=None,
        language_code=None,
        price_min=None,
        price_max=None,
        sort_by="distance",
        page=0,
    )
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_empty_increase_radius")
async def empty_increase_radius(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    next_radius, next_country_wide = next_empty_radius_suggestion(data)

    if next_radius is not None:
        await state.update_data(
            radius_km=next_radius,
            country_wide=False,
            page=0,
        )
    elif next_country_wide and not data.get("country_wide"):
        await state.update_data(
            country_wide=True,
            page=0,
        )
    else:
        await callback.answer()
        return

    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_empty_reset_profession")
async def empty_reset_profession(callback: CallbackQuery, state: FSMContext):
    await state.update_data(profession_id=None, profession_name=None, page=0)
    await show_filters(callback, state)


@search_router.callback_query(F.data == "search_show_results")
async def show_filtered_results(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    has_location = bool(data.get("city_id")) or (
        data.get("latitude") is not None and data.get("longitude") is not None
    )

    if not has_location:
        await show_callback_message(
            callback,
            t("search_location_prompt", language),
            search_location_keyboard(language),
        )
        await state.set_state(SpecialistSearchFSM.choosing_filters)
        await callback.answer()
        return

    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data.startswith("search_results_page:"))
async def paginate_results(callback: CallbackQuery, state: FSMContext):
    page = callback_index(callback)
    if page is None:
        await callback.answer()
        return
    await render_results(event=callback, state=state, page=page)

@search_router.callback_query(F.data.startswith("search_result_contact:"))
async def contact_from_result(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    specialist_ids = data.get("result_specialist_ids") or []
    distances = data.get("result_distances") or []

    if index is None or index >= len(specialist_ids):
        await callback.answer()
        return

    distance_km = distances[index] if index < len(distances) else None

    await state.update_data(
        selected_specialist_id=specialist_ids[index],
        selected_specialist_distance=distance_km,
    )

    await contact_start(callback, state)

@search_router.callback_query(F.data.startswith("search_result:"))
async def show_specialist_card(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    specialist_ids = data.get("result_specialist_ids") or []
    distances = data.get("result_distances") or []

    if index is None or index >= len(specialist_ids):
        await callback.answer()
        return

    distance_km = distances[index] if index < len(distances) else None
    requester_user_id, tenant_id = await get_requester_context(callback.from_user.id)

    async with get_session() as session:
        card = await GeoSearchService(SpecialistSearchRepository(session)).get_public_card(
            specialist_id=UUID(specialist_ids[index]),
            requester_user_id=requester_user_id,
            tenant_id=tenant_id,
            distance_km=distance_km,
            log_event=True,
            language=language,
        )

    if not card:
        await callback.answer()
        return

    await state.update_data(
        selected_specialist_id=specialist_ids[index],
        selected_specialist_distance=distance_km,
    )

    results_page = int(data.get("results_page") or 0)
    await show_callback_message(
        callback,
        format_public_card(card, language),
        card_keyboard(language, results_page),
    )
    await callback.answer()
@search_router.callback_query(F.data == "search_contact_pending")
async def contact_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    if not data.get("selected_specialist_id"):
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    await show_callback_message(
        callback,
        t("contact_disclaimer_text", language),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("contact_disclaimer_continue", language), callback_data="contact_disclaimer_continue")],
                [InlineKeyboardButton(text=t("search_back", language), callback_data=f"search_results_page:{int(data.get('results_page') or 0)}")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )
    await callback.answer()


@search_router.callback_query(F.data == "contact_disclaimer_continue")
async def contact_disclaimer_continue(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    if not data.get("selected_specialist_id"):
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    await show_callback_message(
        callback,
        t("contact_request_prompt", language),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_back", language), callback_data=f"search_results_page:{int(data.get('results_page') or 0)}")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )
    await state.set_state(SpecialistSearchFSM.entering_contact_message)
    await callback.answer()

@search_router.callback_query(F.data == "search_contact_cancel")
async def cancel_contact_flow(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("results_page") or 0)

    await state.update_data(
        pending_contact_message=None,
        pending_report_reason=None,
    )

    logger.info(
        "contact_flow_cancelled telegram_id=%s page=%s",
        callback.from_user.id,
        page,
    )

    await render_results(
        event=callback,
        state=state,
        page=page,
    )

@search_router.message(SpecialistSearchFSM.entering_contact_message)
async def receive_contact_message(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or message.from_user.language_code)
    text = (message.text or "").strip()

    if not data.get("selected_specialist_id"):
        await message.answer(t("search_contact_no_specialist", language))
        await state.set_state(SpecialistSearchFSM.viewing_results)
        return

    if len(text) < 10:
        await message.answer(t("contact_message_too_short", language))
        return

    await state.update_data(pending_contact_message=text)
    await state.set_state(SpecialistSearchFSM.confirming_contact_message)

    await message.answer(
        t("contact_message_confirm_prompt", language).format(message=text),
        reply_markup=contact_message_confirm_keyboard(language),
    )

@search_router.callback_query(F.data == "contact_send_confirm")
async def confirm_contact_message(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    specialist_id = data.get("selected_specialist_id")
    message_text = (data.get("pending_contact_message") or "").strip()

    if not specialist_id:
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        await state.set_state(SpecialistSearchFSM.viewing_results)
        return

    if len(message_text) < 10:
        await callback.answer(t("contact_message_too_short", language), show_alert=True)
        await state.set_state(SpecialistSearchFSM.entering_contact_message)
        return

    requester_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not requester_user_id or not tenant_id:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    specialist_platform_user_id = None
    specialist_language = language
    specialist_notification_message = message_text
    specialist_used_translation = False
    specialist_translation_status = "not_needed"
    result = None

    try:
        async with get_session() as session:
            result = await ContactChatService(ContactChatRepository(session)).create_contact_request(
                tenant_id=tenant_id,
                from_user_id=requester_user_id,
                specialist_id=UUID(specialist_id),
                message=message_text,
                original_language=language,
            )

            specialist_user = await session.get(User, result.specialist_user_id)
            if specialist_user:
                specialist_language = normalize_language(specialist_user.language_code)

            specialist_account = await UserRepository(session).get_telegram_account_by_user_id(
                result.specialist_user_id
            )
            if specialist_account:
                specialist_platform_user_id = specialist_account.platform_user_id

            (
                specialist_notification_message,
                specialist_used_translation,
                specialist_translation_status,
            ) = await translate_message_for_notification(
                session=session,
                message_id=result.first_message_id,
                receiver_user_id=result.specialist_user_id,
            )

        logger.info(
            "contact_request_created telegram_id=%s request_id=%s thread_id=%s specialist_id=%s",
            callback.from_user.id,
            result.contact_request_id,
            result.thread_id,
            specialist_id,
        )
    except ContactChatError as exc:
        logger.warning(
            "contact_request_failed telegram_id=%s specialist_id=%s error=%s",
            callback.from_user.id,
            specialist_id,
            exc,
        )
        await callback.message.answer(
            t("contact_request_error", language).format(error=str(exc))
        )
        await callback.answer()
        return

    specialist_chat_id = telegram_chat_id(specialist_platform_user_id)

    specialist_notification_key = (
        "contact_translated_message_received"
        if specialist_used_translation
        else "contact_request_specialist_notification"
    )
    if specialist_translation_status == "failed":
        specialist_notification_key = "contact_translation_failed_original_shown"

    if specialist_chat_id and result.contact_token:
        try:
            await callback.message.bot.send_message(
                chat_id=specialist_chat_id,
                text=t(specialist_notification_key, specialist_language).format(
                    message=specialist_notification_message,
                ),
                reply_markup=contact_request_action_keyboard(
                    result.contact_token,
                    specialist_language,
                ),
            )
            logger.info(
                "contact_request_notification_sent request_id=%s specialist_chat_id=%s specialist_id=%s",
                result.contact_request_id,
                specialist_chat_id,
                specialist_id,
            )
        except Exception:
            logger.exception(
                "contact_request_notification_failed request_id=%s specialist_chat_id=%s specialist_id=%s",
                result.contact_request_id,
                specialist_chat_id,
                specialist_id,
            )
    else:
        logger.warning(
            "contact_request_notification_skipped request_id=%s specialist_chat_id=%s token_present=%s specialist_id=%s",
            result.contact_request_id,
            specialist_chat_id,
            bool(result.contact_token),
            specialist_id,
        )

    await state.update_data(
        active_contact_request_id=str(result.contact_request_id),
        active_thread_id=str(result.thread_id),
        pending_contact_message=None,
    )
    await state.set_state(SpecialistSearchFSM.viewing_results)

    await callback.message.answer(
        t("contact_request_created", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )

    if result.message_masked:
        await callback.message.answer(t("contact_detection_warning", language))

    await callback.answer()

def callback_token(callback: CallbackQuery) -> str | None:
    try:
        return (callback.data or "").split(":", 1)[1]
    except (IndexError, TypeError):
        return None


@search_router.callback_query(F.data.startswith("contact_accept:"))
async def accept_contact_request(callback: CallbackQuery, state: FSMContext):
    token = callback_token(callback)
    language = normalize_language(callback.from_user.language_code)

    if not token:
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    actor_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not actor_user_id or not tenant_id:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            result = await ContactChatService(ContactChatRepository(session)).set_contact_request_status_by_token(
                contact_token=token,
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
                action="accept",
            )

        logger.info(
            "contact_request_accepted telegram_id=%s actor_user_id=%s thread_id=%s",
            callback.from_user.id,
            actor_user_id,
            result.thread_id,
        )
    except ContactChatError as exc:
        logger.warning(
            "contact_request_accept_failed telegram_id=%s actor_user_id=%s error=%s",
            callback.from_user.id,
            actor_user_id,
            exc,
        )
        await callback.answer(t("contact_request_error", language).format(error=str(exc)), show_alert=True)
        return

    await state.update_data(active_thread_id=str(result.thread_id))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        t("contact_request_accepted_specialist", language),
        reply_markup=contact_thread_keyboard(language),
    )
    await callback.answer()


@search_router.callback_query(F.data.startswith("contact_reject:"))
async def reject_contact_request(callback: CallbackQuery, state: FSMContext):
    token = callback_token(callback)
    language = normalize_language(callback.from_user.language_code)

    if not token:
        await callback.answer(t("contact_request_not_found", language), show_alert=True)
        return

    actor_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not actor_user_id or not tenant_id:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await ContactChatService(ContactChatRepository(session)).set_contact_request_status_by_token(
                contact_token=token,
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
                action="reject",
            )

        logger.info(
            "contact_request_rejected telegram_id=%s actor_user_id=%s",
            callback.from_user.id,
            actor_user_id,
        )
    except ContactChatError as exc:
        logger.warning(
            "contact_request_reject_failed telegram_id=%s actor_user_id=%s error=%s",
            callback.from_user.id,
            actor_user_id,
            exc,
        )
        await callback.answer(t("contact_request_error", language).format(error=str(exc)), show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("contact_request_rejected_specialist", language))
    await callback.answer()


@search_router.callback_query(F.data == "contact_reply")
async def start_thread_reply(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    if not data.get("active_thread_id"):
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    await show_callback_message(
        callback,
        t("contact_reply_prompt", language),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")]
            ]
        ),
    )
    await state.set_state(SpecialistSearchFSM.entering_thread_message)
    await callback.answer()


@search_router.message(SpecialistSearchFSM.entering_thread_message)
async def receive_thread_message(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or message.from_user.language_code)
    thread_id = data.get("active_thread_id")

    if not thread_id:
        await message.answer(t("contact_thread_not_found", language))
        await state.set_state(SpecialistSearchFSM.viewing_results)
        return

    sender_user_id, tenant_id = await get_requester_context(message.from_user.id)
    if not sender_user_id or not tenant_id:
        await message.answer(t("search_contact_user_not_found", language))
        return

    receiver_platform_user_id = None
    receiver_platform_user_id = None
    receiver_language = language
    receiver_notification_message = (message.text or "").strip()
    receiver_used_translation = False
    receiver_translation_status = "not_needed"

    try:
        async with get_session() as session:
            result = await ContactChatService(ContactChatRepository(session)).send_thread_message(
                thread_id=UUID(thread_id),
                sender_user_id=sender_user_id,
                text=message.text or "",
                original_language=language,
            )

            receiver_user = await session.get(User, result.receiver_user_id)
            if receiver_user:
                receiver_language = normalize_language(receiver_user.language_code)

            receiver_account = await UserRepository(session).get_telegram_account_by_user_id(
                result.receiver_user_id
            )
            if receiver_account:
                receiver_platform_user_id = receiver_account.platform_user_id

            receiver_notification_message, receiver_used_translation, receiver_translation_status = await translate_message_for_notification(
                session=session,
                message_id=result.message_id,
                receiver_user_id=result.receiver_user_id,
            )

        logger.info(
            "contact_thread_message_sent telegram_id=%s thread_id=%s message_id=%s receiver_user_id=%s",
            message.from_user.id,
            result.thread_id,
            result.message_id,
            result.receiver_user_id,
        )

    except ContactChatError as exc:
        logger.warning(
            "contact_thread_message_failed telegram_id=%s thread_id=%s error=%s",
            message.from_user.id,
            thread_id,
            exc,
        )
        await message.answer(t("contact_request_error", language).format(error=str(exc)))
        return

    receiver_chat_id = telegram_chat_id(receiver_platform_user_id)

    receiver_notification_key = (
        "contact_translated_message_received"
        if receiver_used_translation
        else "contact_thread_message_received"
    )
    if receiver_translation_status == "failed":
        receiver_notification_key = "contact_translation_failed_original_shown"

    if receiver_chat_id:
        await message.bot.send_message(
            chat_id=receiver_chat_id,
            text=t(receiver_notification_key, receiver_language).format(
                message=receiver_notification_message,
            ),
            reply_markup=contact_thread_keyboard(receiver_language),
        )
    await state.update_data(active_thread_id=str(result.thread_id))
    await state.set_state(SpecialistSearchFSM.viewing_results)

    await message.answer(
        t("contact_message_sent", language),
        reply_markup=contact_thread_keyboard(language),
    )
    if result.message_masked:
        await message.answer(t("contact_detection_warning", language))

@search_router.callback_query(F.data == "search_favorite_pending")
async def favorite_pending(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    specialist_id = data.get("selected_specialist_id")

    if not specialist_id:
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            is_saved = await FavoriteRepository(session).toggle_specialist(
                tenant_id=tenant_id,
                user_id=user_id,
                specialist_id=UUID(specialist_id),
            )
    except ValueError as exc:
        logger.warning(
            "favorite_toggle_failed telegram_id=%s specialist_id=%s error=%s",
            callback.from_user.id,
            specialist_id,
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return

    logger.info(
        "favorite_toggled telegram_id=%s user_id=%s specialist_id=%s is_saved=%s",
        callback.from_user.id,
        user_id,
        specialist_id,
        is_saved,
    )

    text_key = "favorite_saved" if is_saved else "favorite_removed"
    await callback.answer(t(text_key, language), show_alert=True)


@search_router.callback_query(F.data == "search_report_pending")
async def report_pending(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    if not data.get("selected_specialist_id"):
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    await show_callback_message(
        callback,
        t("complaint_reason_prompt", language),
        complaint_reason_keyboard(language),
    )
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_report_reason:"))
async def choose_report_reason(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    reason = callback.data.split(":", 1)[1]

    if reason not in {"fake", "contact", "abuse", "other"}:
        await callback.answer()
        return

    if not data.get("selected_specialist_id"):
        await callback.answer(t("search_contact_no_specialist", language), show_alert=True)
        return

    await state.update_data(pending_report_reason=reason)

    if reason == "other":
        await state.set_state(SpecialistSearchFSM.entering_report_comment)
        await callback.message.answer(t("complaint_comment_prompt", language))
        await callback.answer()
        return

    await create_search_complaint(
        event=callback,
        state=state,
        reason=reason,
        comment=None,
    )


@search_router.message(SpecialistSearchFSM.entering_report_comment)
async def receive_report_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or message.from_user.language_code)
    comment = (message.text or "").strip()

    if len(comment) < 3:
        await message.answer(t("complaint_comment_too_short", language))
        return

    reason = data.get("pending_report_reason") or "other"

    await create_search_complaint(
        event=message,
        state=state,
        reason=reason,
        comment=comment,
    )


async def create_search_complaint(
    *,
    event: CallbackQuery | Message,
    state: FSMContext,
    reason: str,
    comment: str | None,
):
    data = await state.get_data()
    language = normalize_language(
        data.get("user_language")
        or event.from_user.language_code
    )
    specialist_id = data.get("selected_specialist_id")

    if not specialist_id:
        if isinstance(event, CallbackQuery):
            await event.answer(t("search_contact_no_specialist", language), show_alert=True)
        else:
            await event.answer(t("search_contact_no_specialist", language))
        return

    reporter_user_id, tenant_id = await get_requester_context(event.from_user.id)
    if not reporter_user_id or not tenant_id:
        if isinstance(event, CallbackQuery):
            await event.answer(t("search_contact_user_not_found", language), show_alert=True)
        else:
            await event.answer(t("search_contact_user_not_found", language))
        return

    try:
        async with get_session() as session:
            await ModerationService(
                ModerationRepository(session)
            ).create_complaint(
                tenant_id=tenant_id,
                reporter_user_id=reporter_user_id,
                target_type="specialist",
                target_id=UUID(specialist_id),
                reason=reason,
                comment=comment,
            )

        logger.info(
            "complaint_created telegram_id=%s reporter_user_id=%s specialist_id=%s reason=%s",
            event.from_user.id,
            reporter_user_id,
            specialist_id,
            reason,
        )
    except ModerationError as exc:
        logger.warning(
            "complaint_create_failed telegram_id=%s specialist_id=%s reason=%s error=%s",
            event.from_user.id,
            specialist_id,
            reason,
            exc,
        )
        if isinstance(event, CallbackQuery):
            await event.answer(str(exc), show_alert=True)
        else:
            await event.answer(str(exc))
        return

    await state.update_data(
        pending_report_reason=None,
        page=data.get("page") or 0,
    )
    await state.set_state(SpecialistSearchFSM.viewing_results)

    target_message = event.message if isinstance(event, CallbackQuery) else event
    await target_message.answer(
        t("complaint_created", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("search_back_to_filters", language),
                        callback_data="search_filters",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("search_menu", language),
                        callback_data="search_menu",
                    )
                ],
            ]
        ),
    )

    if isinstance(event, CallbackQuery):
        await event.answer()


@search_router.callback_query(F.data == "search_menu")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await state.clear()
    await callback.message.answer(
        t("search_main_menu", language),
        reply_markup=get_main_menu_keyboard(),
    )
    await callback.answer()


@search_router.callback_query(F.data == "contact_show_original")
async def show_original_message(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    thread_id = data.get("active_thread_id")

    if not thread_id:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    viewer_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not viewer_user_id:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            original = await TranslationService(
                TranslationRepository(session)
            ).get_original_message_for_thread(
                thread_id=UUID(thread_id),
                viewer_user_id=viewer_user_id,
            )
    except TranslationError as exc:
        logger.warning(
            "contact_show_original_failed telegram_id=%s thread_id=%s error=%s",
            callback.from_user.id,
            thread_id,
            exc,
        )
        await callback.answer(
            t("contact_original_not_found", language).format(error=str(exc)),
            show_alert=True,
        )
        return

    original_text_key = (
        "contact_translation_failed_original_shown"
        if original.translation_status == "failed"
        else "contact_original_message"
    )

    await callback.message.answer(
        t(original_text_key, language).format(
            message=original.original_text,
        ),
        reply_markup=contact_thread_keyboard(language),
    )
    await callback.answer()

@search_router.callback_query(F.data == "contact_finish")
async def finish_contact_thread(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    thread_id = data.get("active_thread_id")

    if not thread_id:
        await callback.answer(t("contact_thread_not_found", language), show_alert=True)
        return

    actor_user_id, tenant_id = await get_requester_context(callback.from_user.id)
    if not actor_user_id:
        await callback.answer(t("search_contact_user_not_found", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            await ContactChatService(ContactChatRepository(session)).complete_thread(
                thread_id=UUID(thread_id),
                actor_user_id=actor_user_id,
            )

        logger.info(
            "contact_thread_completed telegram_id=%s thread_id=%s actor_user_id=%s",
            callback.from_user.id,
            thread_id,
            actor_user_id,
        )
    except ContactChatError as exc:
        logger.warning(
            "contact_thread_complete_failed telegram_id=%s thread_id=%s error=%s",
            callback.from_user.id,
            thread_id,
            exc,
        )
        await callback.answer(t("contact_request_error", language).format(error=str(exc)), show_alert=True)
        return

    await state.update_data(active_thread_id=None)
    await callback.message.answer(
        t("contact_thread_completed", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_back_to_filters", language), callback_data="search_filters")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )
    await callback.answer()