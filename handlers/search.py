from uuid import UUID

from sqlalchemy import select
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

from database.models import User, UserAccount
from database.repositories.contact import ContactChatRepository
from database.repositories.search import SpecialistSearchRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard
from services.contact_chat import ContactChatError, ContactChatService
from services.geo_search import GeoSearchService, SpecialistPublicCard
from ui.texts import t


search_router = Router()

PER_PAGE = 5
DEFAULT_RADIUS_KM = 25


class SpecialistSearchFSM(StatesGroup):
    choosing_category = State()
    choosing_profession = State()
    choosing_mode = State()
    choosing_city = State()
    waiting_geo = State()
    choosing_filters = State()
    viewing_results = State()
    entering_contact_message = State()
    entering_thread_message = State()

    


def normalize_language(language: str | None) -> str:
    return language if language in {"ru", "en", "pt"} else "ru"


def item_name(item, language: str = "ru") -> str:
    localized = getattr(item, f"name_{language}", None)
    return localized or getattr(item, "name_ru", None) or getattr(item, "name", None) or str(item.id)


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


def paged_keyboard(
    *,
    items,
    item_prefix: str,
    page_prefix: str,
    page: int,
    language: str,
    back_callback: str = "search_start",
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

    rows.append([InlineKeyboardButton(text=t("search_back", language), callback_data=back_callback)])
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
        back_callback="search_start",
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



def search_mode_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("search_choose_city_btn", language), callback_data="search_mode_city")],
            [InlineKeyboardButton(text=t("search_nearby_btn", language), callback_data="search_mode_geo")],
            [InlineKeyboardButton(text=t("search_back", language), callback_data="search_start")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )


def results_keyboard(page: int, has_next: bool, results_count: int, language: str) -> InlineKeyboardMarkup:
    rows = []

    for index in range(results_count):
        rows.append(
            [
                InlineKeyboardButton(
                    text=str(page * PER_PAGE + index + 1),
                    callback_data=f"search_result:{index}",
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"search_results_page:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"search_results_page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=t("search_new", language), callback_data="search_start")])
    rows.append([InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def card_keyboard(language: str) -> InlineKeyboardMarkup:
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
                    text=t("favorite", language),
                    callback_data="search_favorite_pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("report", language),
                    callback_data="search_report_pending",
                )
            ],
            [InlineKeyboardButton(text=t("search_back", language), callback_data="search_results_page:0")],
            [InlineKeyboardButton(text=t("search_new", language), callback_data="search_start")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
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
            [
                InlineKeyboardButton(
                    text=t("contact_reply_btn", language),
                    callback_data="contact_reply",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_show_original_btn", language),
                    callback_data="contact_show_original_pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_finish_btn", language),
                    callback_data="contact_finish",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("contact_report_btn", language),
                    callback_data="search_report_pending",
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


def filters_keyboard(data: dict, language: str) -> InlineKeyboardMarkup:
    radius = int(float(data.get("radius_km") or DEFAULT_RADIUS_KM))
    selected_language = data.get("language_code")
    verified_only = bool(data.get("verified_only"))
    premium_only = bool(data.get("premium_only"))
    work_format = data.get("work_format")
    rating_min = data.get("rating_min")

    radius_row = [
        InlineKeyboardButton(
            text=("[x] " if radius == value else "[ ] ") + f"{value} km",
            callback_data=f"search_radius:{value}",
        )
        for value in (5, 25, 50, 100)
    ]

    language_row_1 = [
        InlineKeyboardButton(
            text=("[x] " if selected_language is None else "[ ] ") + t("search_filter_language_any", language),
            callback_data="search_lang:any",
        ),
        InlineKeyboardButton(
            text=("[x] " if selected_language == "ru" else "[ ] ") + "ru",
            callback_data="search_lang:ru",
        ),
    ]

    language_row_2 = [
        InlineKeyboardButton(
            text=("[x] " if selected_language == "en" else "[ ] ") + "en",
            callback_data="search_lang:en",
        ),
        InlineKeyboardButton(
            text=("[x] " if selected_language == "pt" else "[ ] ") + "pt",
            callback_data="search_lang:pt",
        ),
    ]

    price_min = data.get("price_min")
    price_max = data.get("price_max")

    def price_mark(min_value, max_value) -> str:
        return "[x] " if price_min == min_value and price_max == max_value else "[ ] "

    price_row_1 = [
        InlineKeyboardButton(
            text=price_mark(None, None) + t("search_filter_price_any", language),
            callback_data="search_price:any",
        ),
        InlineKeyboardButton(
            text=price_mark(None, 50) + t("search_filter_price_up_to_50", language),
            callback_data="search_price:0_50",
        ),
    ]

    price_row_2 = [
        InlineKeyboardButton(
            text=price_mark(50, 100) + t("search_filter_price_50_100", language),
            callback_data="search_price:50_100",
        ),
        InlineKeyboardButton(
            text=price_mark(100, None) + t("search_filter_price_from_100", language),
            callback_data="search_price:100_plus",
        ),
    ]

    work_row_1 = [
        InlineKeyboardButton(
            text=("[x] " if work_format is None else "[ ] ") + t("search_filter_work_any", language),
            callback_data="search_work:any",
        ),
        InlineKeyboardButton(
            text=("[x] " if work_format == "remote" else "[ ] ") + t("search_filter_work_remote", language),
            callback_data="search_work:remote",
        ),
    ]

    work_row_2 = [
        InlineKeyboardButton(
            text=("[x] " if work_format == "onsite" else "[ ] ") + t("search_filter_work_onsite", language),
            callback_data="search_work:onsite",
        ),
        InlineKeyboardButton(
            text=("[x] " if work_format == "mixed" else "[ ] ") + t("search_filter_work_mixed", language),
            callback_data="search_work:mixed",
        ),
    ]

    rating_row = [
        InlineKeyboardButton(
            text=("[x] " if rating_min is None else "[ ] ") + t("search_filter_rating_any", language),
            callback_data="search_rating:any",
        ),
        InlineKeyboardButton(
            text=("[x] " if rating_min == 4 else "[ ] ") + t("search_filter_rating_4", language),
            callback_data="search_rating:4",
        ),
    ]

    premium_text = (
        t("search_filter_premium_only", language)
        if premium_only
        else t("search_filter_premium_all", language)
    )
    verified_text = (
        t("search_filter_verified_only", language)
        if verified_only
        else t("search_filter_verified_all", language)
    )

    return InlineKeyboardMarkup(
         inline_keyboard=[
            radius_row,
            language_row_1,
            language_row_2,
            price_row_1,
            price_row_2,
            work_row_1,
            work_row_2,
            rating_row,
            [
                InlineKeyboardButton(
                    text=verified_text,
                    callback_data="search_verified_toggle",
                )
            ],
            [
                InlineKeyboardButton(
                    text=premium_text,
                    callback_data="search_premium_toggle",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_show_results", language),
                    callback_data="search_show_results",
                )
            ],
            [InlineKeyboardButton(text=t("search_back", language), callback_data="search_start")],
            [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
        ]
    )

def format_specialist_result(result, index: int, language: str) -> str:
    specialist = result.specialist

    price = t("search_price_not_set", language)
    if specialist.price_from and specialist.price_to:
        price = f"{specialist.price_from}-{specialist.price_to} {specialist.currency}"
    elif specialist.price_from:
        price = f"{specialist.price_from} {specialist.currency}"

    distance = ""
    if result.distance_km is not None:
        distance = f"\n{t('search_distance', language)}: {result.distance_km:.1f} km"

    labels = []
    if specialist.is_verified:
        labels.append(t("search_verified_label", language))
    if specialist.is_premium:
        labels.append(t("search_premium_label", language))
    
    label_text = f" ({', '.join(labels)})" if labels else ""

    return (
        f"{index}. {specialist.display_name}{label_text}\n"
        f"{specialist.short_description}\n"
        f"{price}"
        f"{distance}"
    )


def format_public_card(card: SpecialistPublicCard, language: str) -> str:
    price = t("search_price_not_set", language)
    if card.price_from and card.price_to:
        price = f"{card.price_from}-{card.price_to} {card.currency}"
    elif card.price_from:
        price = f"{card.price_from} {card.currency}"

    labels = []
    if card.is_verified:
        labels.append(t("search_verified_label", language))
    if card.is_premium:
        labels.append(t("search_premium_label", language))

    label_text = f" ({', '.join(labels)})" if labels else ""
    languages = ", ".join(card.languages) if card.languages else "-"

    distance = ""
    if card.distance_km is not None:
        distance = f"\n{t('search_distance', language)}: {card.distance_km:.1f} km"

    city = card.city_name or "-"

    return (
        f"{card.display_name}{label_text}\n\n"
        f"{card.short_description}\n\n"
        f"{city}\n"
        f"{price}\n"
        f"{languages}\n"
        f"{t('search_rating', language)}: {card.rating} ({card.reviews_count})"
        f"{distance}"
    )

async def show_filters(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await show_callback_message(
        callback,
        t("search_filters_prompt", language),
        filters_keyboard(data, language),
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
    mode = data.get("search_mode")
    language_code = data.get("language_code")
    verified_only = bool(data.get("verified_only"))
    price_min = data.get("price_min")
    price_max = data.get("price_max")
    premium_only = bool(data.get("premium_only"))
    work_format = data.get("work_format")
    rating_min = data.get("rating_min")

    requester_user_id = None
    tenant_id = None
    platform_user_id = event.from_user.id if event.from_user else None
    if platform_user_id is not None:
        requester_user_id, tenant_id = await get_requester_context(platform_user_id)

    async with get_session() as session:
        service = GeoSearchService(SpecialistSearchRepository(session))

        if mode == "city":
            results = await service.search_by_city(
            city_id=city_id,
            category_id=category_id,
            profession_id=profession_id,
            price_min=price_min,
            price_max=price_max,
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
    )
        elif mode == "geo":
            results = await service.search_by_radius(
                latitude=float(data["latitude"]),
                longitude=float(data["longitude"]),
                radius_km=float(data.get("radius_km") or DEFAULT_RADIUS_KM),
                category_id=category_id,
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
            )
        else:
            results = []

    has_next = len(results) > PER_PAGE
    visible_results = results[:PER_PAGE]

    await state.update_data(
        results_page=page,
        result_specialist_ids=[str(item.specialist.id) for item in visible_results],
        result_distances=[item.distance_km for item in visible_results],
    )

    if not visible_results:
        text = t("search_no_results", language)
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

    async with get_session() as session:
        categories = await SpecialistRepository(session).list_active_categories(limit=100)

    if not categories:
        await callback.message.answer(t("search_categories_missing", language))
        await callback.answer()
        return

    await state.update_data(
        user_language=language,
        category_ids=[str(category.id) for category in categories],
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
            back_callback="search_menu",
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
            back_callback="search_menu",
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
        await callback.message.answer(t("search_category_not_found", language))
        await callback.answer()
        return

    category_id = UUID(category_ids[index])

    async with get_session() as session:
        category = await SpecialistRepository(session).get_active_category(category_id)

    if not category:
        await callback.message.answer(t("search_category_not_found", language))
        await callback.answer()
        return

    async with get_session() as session:
        professions = await SpecialistRepository(session).list_active_professions_by_category(
            category.id,
            limit=100,
        )

    if not professions:
        await callback.message.answer(t("search_professions_missing", language))
        await callback.answer()
        return

    await state.update_data(
        category_id=str(category.id),
        category_name=item_name(category, language),
        profession_ids=[str(item.id) for item in professions],
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

    category_id = UUID(data["category_id"])

    async with get_session() as session:
        professions = await SpecialistRepository(session).list_active_professions_by_category(
            category_id,
            limit=100,
        )

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
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await state.update_data(profession_id=None)

    await show_callback_message(
        callback,
        t("search_mode_prompt", language),
        search_mode_keyboard(language),
    )
    await state.set_state(SpecialistSearchFSM.choosing_mode)
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_profession:"))
async def choose_profession(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    profession_ids = data.get("profession_ids") or []

    if index is None or index >= len(profession_ids):
        await callback.message.answer(t("search_profession_not_found", language))
        await callback.answer()
        return

    profession_id = UUID(profession_ids[index])

    async with get_session() as session:
        profession = await SpecialistRepository(session).get_active_profession(profession_id)

    if not profession:
        await callback.message.answer(t("search_profession_not_found", language))
        await callback.answer()
        return

    await state.update_data(
        profession_id=str(profession.id),
        profession_name=item_name(profession, language),
    )

    await show_callback_message(
        callback,
        t("search_mode_prompt", language),
        search_mode_keyboard(language),
    )
    await state.set_state(SpecialistSearchFSM.choosing_mode)
    await callback.answer()



@search_router.callback_query(F.data == "search_mode_city")
async def choose_city_mode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    async with get_session() as session:
        cities = await SpecialistRepository(session).list_active_cities(limit=100)

    if not cities:
        await callback.message.answer(t("search_cities_missing", language))
        await callback.answer()
        return

    await state.update_data(city_ids=[str(city.id) for city in cities])

    await show_callback_message(
        callback,
        t("search_choose_city", language),
        paged_keyboard(
            items=cities,
            item_prefix="search_city",
            page_prefix="search_cities_page",
            page=0,
            language=language,
            back_callback="search_start",
        ),
    )
    await state.set_state(SpecialistSearchFSM.choosing_city)
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_cities_page:"))
async def paginate_cities(callback: CallbackQuery, state: FSMContext):
    page = callback_index(callback)
    if page is None:
        await callback.answer()
        return

    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    async with get_session() as session:
        cities = await SpecialistRepository(session).list_active_cities(limit=100)

    await state.update_data(city_ids=[str(city.id) for city in cities])

    await show_callback_message(
        callback,
        t("search_choose_city", language),
        paged_keyboard(
            items=cities,
            item_prefix="search_city",
            page_prefix="search_cities_page",
            page=page,
            language=language,
            back_callback="search_start",
        ),
    )
    await callback.answer()


@search_router.callback_query(F.data.startswith("search_city:"))
async def choose_city(callback: CallbackQuery, state: FSMContext):
    index = callback_index(callback)
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)
    city_ids = data.get("city_ids") or []

    if index is None or index >= len(city_ids):
        await callback.message.answer(t("search_city_not_found", language))
        await callback.answer()
        return

    city_id = UUID(city_ids[index])

    async with get_session() as session:
        city = await SpecialistRepository(session).get_active_city(city_id)

    if not city:
        await callback.message.answer(t("search_city_not_found", language))
        await callback.answer()
        return

    await state.update_data(
        search_mode="city",
        city_id=str(city.id),
        city_name=item_name(city, language),
        premium_only=False,
        work_format=None,
    )

    await state.update_data(
        radius_km=DEFAULT_RADIUS_KM,
        language_code=None,
        verified_only=False,
        price_min=None,
        price_max=None,
        rating_min=None,
    )
    await show_filters(callback, state)
    
@search_router.callback_query(F.data == "search_mode_geo")
async def choose_geo_mode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await callback.message.answer(
        t("search_geo_prompt", language),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=t("search_send_geo_btn", language), request_location=True)]
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

    await state.update_data(
        search_mode="geo",
        latitude=message.location.latitude,
        longitude=message.location.longitude,
        radius_km=DEFAULT_RADIUS_KM,
    )

    await state.update_data(
        language_code=None,
        verified_only=False,
        price_min=None,
        price_max=None,
        premium_only=False,
        work_format=None,
        rating_min=None,
    )

    await message.answer(
        t("search_loading_nearby", language),
        reply_markup=ReplyKeyboardRemove(),
    )

    await message.answer(
        t("search_filters_prompt", language),
        reply_markup=filters_keyboard(await state.get_data(), language),
    )
    await state.set_state(SpecialistSearchFSM.choosing_filters)

@search_router.callback_query(F.data.startswith("search_radius:"))
async def choose_radius(callback: CallbackQuery, state: FSMContext):
    try:
        radius_km = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer()
        return

    radius_km = max(5, min(radius_km, 100))
    await state.update_data(radius_km=radius_km)
    await show_filters(callback, state)


@search_router.callback_query(F.data.startswith("search_lang:"))
async def choose_language_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]
    language_code = None if value == "any" else value
    await state.update_data(language_code=language_code)
    await show_filters(callback, state)


@search_router.callback_query(F.data.startswith("search_price:"))
async def choose_price_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value == "any":
        price_min = None
        price_max = None
    elif value == "0_50":
        price_min = None
        price_max = 50
    elif value == "50_100":
        price_min = 50
        price_max = 100
    elif value == "100_plus":
        price_min = 100
        price_max = None
    else:
        await callback.answer()
        return

    await state.update_data(price_min=price_min, price_max=price_max)
    await show_filters(callback, state)

@search_router.callback_query(F.data == "search_verified_toggle")
async def toggle_verified_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(verified_only=not bool(data.get("verified_only")))
    await show_filters(callback, state)

@search_router.callback_query(F.data == "search_premium_toggle")
async def toggle_premium_filter(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(premium_only=not bool(data.get("premium_only")))
    await show_filters(callback, state)


@search_router.callback_query(F.data.startswith("search_work:"))
async def choose_work_format_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]
    work_format = None if value == "any" else value

    if work_format not in {None, "remote", "onsite", "mixed"}:
        await callback.answer()
        return

    await state.update_data(work_format=work_format)
    await show_filters(callback, state)

@search_router.callback_query(F.data.startswith("search_rating:"))
async def choose_rating_filter(callback: CallbackQuery, state: FSMContext):
    value = (callback.data or "").split(":", 1)[1]

    if value == "any":
        rating_min = None
    elif value == "4":
        rating_min = 4
    else:
        await callback.answer()
        return

    await state.update_data(rating_min=rating_min)
    await show_filters(callback, state)

@search_router.callback_query(F.data == "search_show_results")
async def show_filtered_results(callback: CallbackQuery, state: FSMContext):
    await render_results(event=callback, state=state, page=0)

@search_router.callback_query(F.data.startswith("search_results_page:"))
async def paginate_results(callback: CallbackQuery, state: FSMContext):
    page = callback_index(callback)
    if page is None:
        await callback.answer()
        return

    await render_results(event=callback, state=state, page=page)


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

    await show_callback_message(
        callback,
        format_public_card(card, language),
        card_keyboard(language),
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
                [
                    InlineKeyboardButton(
                        text=t("contact_disclaimer_continue", language),
                        callback_data="contact_disclaimer_continue",
                    )
                ],
                [InlineKeyboardButton(text=t("search_back", language), callback_data="search_results_page:0")],
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
                [InlineKeyboardButton(text=t("search_back", language), callback_data="search_results_page:0")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )
    await state.set_state(SpecialistSearchFSM.entering_contact_message)
    await callback.answer()

@search_router.message(SpecialistSearchFSM.entering_contact_message)
async def receive_contact_message(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or message.from_user.language_code)
    specialist_id = data.get("selected_specialist_id")

    if not specialist_id:
        await message.answer(t("search_contact_no_specialist", language))
        await state.set_state(SpecialistSearchFSM.viewing_results)
        return

    requester_user_id, tenant_id = await get_requester_context(message.from_user.id)
    if not requester_user_id or not tenant_id:
        await message.answer(t("search_contact_user_not_found", language))
        return

    specialist_platform_user_id = None
    specialist_language = language
    result = None

    try:
        async with get_session() as session:
            result = await ContactChatService(ContactChatRepository(session)).create_contact_request(
                tenant_id=tenant_id,
                from_user_id=requester_user_id,
                specialist_id=UUID(specialist_id),
                message=message.text or "",
                original_language=language,
            )

            specialist_user = await session.get(User, result.specialist_user_id)
            if specialist_user:
                specialist_language = normalize_language(specialist_user.language_code)

            specialist_account_result = await session.execute(
                select(UserAccount).where(
                    UserAccount.user_id == result.specialist_user_id,
                    UserAccount.platform == "telegram",
                )
            )
            specialist_account = specialist_account_result.scalar_one_or_none()
            if specialist_account:
                specialist_platform_user_id = specialist_account.platform_user_id

    except ContactChatError as exc:
        await message.answer(
            t("contact_request_error", language).format(error=str(exc))
        )
        return

    if specialist_platform_user_id and result.contact_token:
        await message.bot.send_message(
            chat_id=int(specialist_platform_user_id),
            text=t("contact_request_specialist_notification", specialist_language).format(
                message=(message.text or "").strip(),
            ),
            reply_markup=contact_request_action_keyboard(
                result.contact_token,
                specialist_language,
            ),
        )
    await state.update_data(
        active_contact_request_id=str(result.contact_request_id),
        active_thread_id=str(result.thread_id),
    )
    await state.set_state(SpecialistSearchFSM.viewing_results)

    await message.answer(
        t("contact_request_created", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_new", language), callback_data="search_start")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )

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
    except ContactChatError as exc:
        await callback.answer(
            t("contact_request_error", language).format(error=str(exc)),
            show_alert=True,
        )
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
    except ContactChatError as exc:
        await callback.answer(
            t("contact_request_error", language).format(error=str(exc)),
            show_alert=True,
        )
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
                [
                    InlineKeyboardButton(
                        text=t("search_menu", language),
                        callback_data="search_menu",
                    )
                ]
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
    receiver_language = language

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

            receiver_account_result = await session.execute(
                select(UserAccount).where(
                    UserAccount.user_id == result.receiver_user_id,
                    UserAccount.platform == "telegram",
                )
            )
            receiver_account = receiver_account_result.scalar_one_or_none()
            if receiver_account:
                receiver_platform_user_id = receiver_account.platform_user_id

    except ContactChatError as exc:
        await message.answer(
            t("contact_request_error", language).format(error=str(exc))
        )
        return

    if receiver_platform_user_id:
        await message.bot.send_message(
            chat_id=int(receiver_platform_user_id),
            text=t("contact_thread_message_received", receiver_language).format(
                message=(message.text or "").strip(),
            ),
            reply_markup=contact_thread_keyboard(receiver_language),
        )

    await state.update_data(active_thread_id=str(result.thread_id))
    await state.set_state(SpecialistSearchFSM.viewing_results)

    await message.answer(
        t("contact_message_sent", language),
        reply_markup=contact_thread_keyboard(language),
    )

@search_router.callback_query(F.data == "search_favorite_pending")
async def favorite_pending(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await callback.answer(
        t("search_favorite_placeholder", language),
        show_alert=True,
    )


@search_router.callback_query(F.data == "search_report_pending")
async def report_pending(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await callback.answer(
        t("search_report_placeholder", language),
        show_alert=True,
    )


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

@search_router.callback_query(F.data == "contact_show_original_pending")
async def show_original_pending(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(data.get("user_language") or callback.from_user.language_code)

    await callback.answer(
        t("contact_show_original_pending", language),
        show_alert=True,
    )


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
    except ContactChatError as exc:
        await callback.answer(
            t("contact_request_error", language).format(error=str(exc)),
            show_alert=True,
        )
        return

    await state.update_data(active_thread_id=None)
    await callback.message.answer(
        t("contact_thread_completed", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t("search_new", language), callback_data="search_start")],
                [InlineKeyboardButton(text=t("search_menu", language), callback_data="search_menu")],
            ]
        ),
    )
    await callback.answer()