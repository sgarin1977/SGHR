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

from database.repositories.search import SpecialistSearchRepository
from database.repositories.specialist import SpecialistRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard
from services.geo_search import GeoSearchService


search_router = Router()

PER_PAGE = 5
DEFAULT_RADIUS_KM = 100


class SpecialistSearchFSM(StatesGroup):
    choosing_category = State()
    choosing_mode = State()
    choosing_city = State()
    waiting_geo = State()


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
    for item in items[start:end]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=item_name(item, language),
                    callback_data=f"{item_prefix}:{item.id}",
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

    rows.append([InlineKeyboardButton(text="Назад", callback_data=back_callback)])
    rows.append([InlineKeyboardButton(text="В меню", callback_data="search_menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Выбрать город", callback_data="search_mode_city")],
            [InlineKeyboardButton(text="Найти рядом", callback_data="search_mode_geo")],
            [InlineKeyboardButton(text="Назад", callback_data="search_start")],
            [InlineKeyboardButton(text="В меню", callback_data="search_menu")],
        ]
    )


def results_keyboard(page: int, has_next: bool) -> InlineKeyboardMarkup:
    nav = []

    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"search_results_page:{page - 1}"))

    if has_next:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"search_results_page:{page + 1}"))

    rows = []
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Новый поиск", callback_data="search_start")])
    rows.append([InlineKeyboardButton(text="В меню", callback_data="search_menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_specialist_result(result, index: int) -> str:
    specialist = result.specialist

    price = "цена не указана"
    if specialist.price_from and specialist.price_to:
        price = f"{specialist.price_from}-{specialist.price_to} {specialist.currency}"
    elif specialist.price_from:
        price = f"{specialist.price_from} {specialist.currency}"

    distance = ""
    if result.distance_km is not None:
        distance = f"\nРасстояние: {result.distance_km:.1f} км"

    return (
        f"{index}. {specialist.display_name}\n"
        f"{specialist.short_description}\n"
        f"Цена: {price}"
        f"{distance}"
    )


async def render_results(
    *,
    event: CallbackQuery | Message,
    state: FSMContext,
    page: int,
):
    data = await state.get_data()

    category_id = UUID(data["category_id"]) if data.get("category_id") else None
    city_id = UUID(data["city_id"]) if data.get("city_id") else None
    mode = data.get("search_mode")

    async with get_session() as session:
        service = GeoSearchService(SpecialistSearchRepository(session))

        if mode == "city":
            results = await service.search_by_city(
                city_id=city_id,
                category_id=category_id,
                limit=PER_PAGE + 1,
                offset=page * PER_PAGE,
            )
        elif mode == "geo":
            results = await service.search_by_radius(
                latitude=float(data["latitude"]),
                longitude=float(data["longitude"]),
                radius_km=float(data.get("radius_km") or DEFAULT_RADIUS_KM),
                category_id=category_id,
                limit=PER_PAGE + 1,
                offset=page * PER_PAGE,
            )
        else:
            results = []

    has_next = len(results) > PER_PAGE
    visible_results = results[:PER_PAGE]

    if not visible_results:
        text = "Специалисты не найдены. Попробуйте другую категорию, город или радиус."
    else:
        start_number = page * PER_PAGE + 1
        text = "Найденные специалисты:\n\n" + "\n\n".join(
            format_specialist_result(result, start_number + index)
            for index, result in enumerate(visible_results)
        )

    await state.update_data(results_page=page)

    keyboard = results_keyboard(page=page, has_next=has_next)

    if isinstance(event, CallbackQuery):
        await show_callback_message(event, text, keyboard)
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard)


@search_router.callback_query(F.data.in_({"M_FIND", "search_start"}))
async def start_search(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    language = callback.from_user.language_code or "ru"

    async with get_session() as session:
        categories = await SpecialistRepository(session).list_active_categories(limit=100)

    if not categories:
        await callback.message.answer("Категории специалистов не настроены.")
        await callback.answer()
        return

    await state.update_data(user_language=language)

    await show_callback_message(
        callback,
        "Выберите категорию специалиста:",
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
    page = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"

    async with get_session() as session:
        categories = await SpecialistRepository(session).list_active_categories(limit=100)

    await show_callback_message(
        callback,
        "Выберите категорию специалиста:",
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
    category_id = UUID(callback.data.split(":", 1)[1])
    language = callback.from_user.language_code or "ru"

    async with get_session() as session:
        category = await SpecialistRepository(session).get_active_category(category_id)

    if not category:
        await callback.message.answer("Категория не найдена или отключена.")
        await callback.answer()
        return

    await state.update_data(
        category_id=str(category.id),
        category_name=item_name(category, language),
    )

    await show_callback_message(
        callback,
        "Как искать специалиста?",
        search_mode_keyboard(),
    )
    await state.set_state(SpecialistSearchFSM.choosing_mode)
    await callback.answer()


@search_router.callback_query(F.data == "search_mode_city")
async def choose_city_mode(callback: CallbackQuery, state: FSMContext):
    language = callback.from_user.language_code or "ru"

    async with get_session() as session:
        cities = await SpecialistRepository(session).list_active_cities(limit=100)

    if not cities:
        await callback.message.answer("Города не настроены.")
        await callback.answer()
        return

    await show_callback_message(
        callback,
        "Выберите город:",
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
    page = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"

    async with get_session() as session:
        cities = await SpecialistRepository(session).list_active_cities(limit=100)

    await show_callback_message(
        callback,
        "Выберите город:",
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
    city_id = UUID(callback.data.split(":", 1)[1])

    async with get_session() as session:
        city = await SpecialistRepository(session).get_active_city(city_id)

    if not city:
        await callback.message.answer("Город не найден или отключен.")
        await callback.answer()
        return

    await state.update_data(
        search_mode="city",
        city_id=str(city.id),
        city_name=city.name,
    )

    await render_results(event=callback, state=state, page=0)


@search_router.callback_query(F.data == "search_mode_geo")
async def choose_geo_mode(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Отправьте вашу геолокацию Telegram для поиска рядом.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отправить геолокацию", request_location=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await state.set_state(SpecialistSearchFSM.waiting_geo)
    await callback.answer()


@search_router.message(SpecialistSearchFSM.waiting_geo)
async def receive_geo(message: Message, state: FSMContext):
    if not message.location:
        await message.answer("Пожалуйста, отправьте геолокацию Telegram.")
        return

    await state.update_data(
        search_mode="geo",
        latitude=message.location.latitude,
        longitude=message.location.longitude,
        radius_km=DEFAULT_RADIUS_KM,
    )

    await message.answer(
        "Ищу специалистов рядом...",
        reply_markup=ReplyKeyboardRemove(),
    )
    await render_results(event=message, state=state, page=0)


@search_router.callback_query(F.data.startswith("search_results_page:"))
async def paginate_results(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":", 1)[1])
    await render_results(event=callback, state=state, page=page)


@search_router.callback_query(F.data == "search_menu")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "Главное меню SGHR Beta.",
        reply_markup=get_main_menu_keyboard(),
    )
    await callback.answer()