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
from database.repositories.event import EventRepository
from database.repositories.geo_repository import GeoRepository
from database.repositories.legal import LegalRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard
from services.legal import LegalService, MissingLegalDocumentError
from services.geo_service import GeoService, GeoServiceError
from services.specialist import (
    SpecialistRegistrationData,
    SpecialistRegistrationError,
    SpecialistService,
)
from ui.texts import t

specialist_form_router = Router()

PER_PAGE = 8
LANGUAGE_OPTIONS = {
    "ru": "Русский",
    "en": "English",
    "pt": "Portugues",
}


class SpecialistForm(StatesGroup):
    choosing_category = State()
    choosing_profession = State()
    choosing_location_mode = State()
    entering_city_query = State()
    choosing_geo_place = State()
    waiting_geo = State()
    entering_display_name = State()
    entering_description = State()
    entering_price = State()
    choosing_languages = State()
    entering_contact = State()
    confirming = State()


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


def build_paged_keyboard(
    *,
    items,
    prefix: str,
    page_prefix: str,
    page: int,
    language: str,
    back_callback: str = "M",
) -> InlineKeyboardMarkup:
    start = page * PER_PAGE
    end = start + PER_PAGE

    rows = []
    for offset, item in enumerate(items[start:end]):
        item_index = start + offset
        rows.append(
            [
                InlineKeyboardButton(
                    text=item_name(item, language),
                    callback_data=f"{prefix}:{item_index}",
                )
            ]
        )

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="<", callback_data=f"{page_prefix}:{page - 1}")
        )
    if end < len(items):
        navigation.append(
            InlineKeyboardButton(text=">", callback_data=f"{page_prefix}:{page + 1}")
        )
    if navigation:
        rows.append(navigation)

    rows.append([InlineKeyboardButton(text=t("spec_back_btn", language), callback_data=back_callback)])
    rows.append([InlineKeyboardButton(text=t("spec_cancel_btn", language), callback_data="spec_cancel")])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def location_mode_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("spec_choose_city_btn", language), callback_data="spec_location_city")],
            [InlineKeyboardButton(text=t("spec_send_geo_btn", language), callback_data="spec_location_geo")],
            [InlineKeyboardButton(text=t("spec_back_btn", language), callback_data="spec_back_to_categories")],
            [InlineKeyboardButton(text=t("spec_cancel_btn", language), callback_data="spec_cancel")],
        ]
    )

def geo_candidates_keyboard(
    candidates: list[dict],
    language: str = "ru",
) -> InlineKeyboardMarkup:
    rows = []

    for index, candidate in enumerate(candidates[:8]):
        title = candidate.get("display_name") or candidate.get("name") or "-"
        rows.append(
            [
                InlineKeyboardButton(
                    text=title[:64],
                    callback_data=f"spec_geo_place:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("spec_back_btn", language),
                callback_data="spec_back_to_location_mode",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("spec_cancel_btn", language),
                callback_data="spec_cancel",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def language_keyboard(selected: list[str], language: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    for code, title in LANGUAGE_OPTIONS.items():
        mark = "[x]" if code in selected else "[ ]"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {title}",
                    callback_data=f"spec_lang_toggle:{code}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text=t("spec_done_btn", language), callback_data="spec_lang_done")])
    rows.append([InlineKeyboardButton(text=t("spec_cancel_btn", language), callback_data="spec_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def confirm_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("spec_confirm_btn", language), callback_data="spec_confirm")],
            [InlineKeyboardButton(text=t("spec_restart_btn", language), callback_data="register_specialist")],
            [InlineKeyboardButton(text=t("spec_cancel_btn", language), callback_data="spec_cancel")],
        ]
    )


def parse_price(text: str) -> tuple[float | None, float | None]:
    value = text.strip().replace(",", ".")
    if not value or value in {"-", "0"}:
        return None, None

    if "-" in value:
        left, right = value.split("-", 1)
        price_from = float(left.strip()) if left.strip() else None
        price_to = float(right.strip()) if right.strip() else None
        return price_from, price_to

    price = float(value)
    return price, None


async def get_current_user(session, telegram_id: int) -> User | None:
    user_repo = UserRepository(session)
    account = await user_repo.get_by_platform_account("telegram", str(telegram_id))

    if not account:
        return None

    return await session.get(User, account.user_id)


async def ensure_specialist_consents(session, user: User) -> bool:
    legal_service = LegalService(LegalRepository(session))
    missing = await legal_service.get_missing_specialist_consents(
        tenant_id=user.tenant_id,
        user_id=user.id,
        language=user.language_code or "ru",
    )
    return not missing


@specialist_form_router.callback_query(F.data == "register_specialist")
async def register_specialist(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    language = callback.from_user.language_code or "ru"

    async with get_session() as session:
        user = await get_current_user(session, callback.from_user.id)
        if not user:
            await callback.message.answer(t("spec_start_required", language))
            await callback.answer()
            return

        language = user.language_code or language

        try:
            has_consents = await ensure_specialist_consents(session, user)
        except MissingLegalDocumentError as exc:
            await callback.message.answer(
                t("spec_legal_docs_missing", language).format(error=exc)
            )
            await callback.answer()
            return

        if not has_consents:
            await callback.message.answer(
                t("spec_legal_consents_required", language),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t("spec_go_to_consents_btn", language),
                                callback_data="SS_START",
                            )
                        ]
                    ]
                ),
            )
            await callback.answer()
            return

        repository = SpecialistRepository(session)

        existing = await repository.get_by_user_id(user.id)
        if existing:
            await callback.message.answer(
                t("spec_profile_pending_exists", language)
                if existing.status == "pending_moderation"
                else t("spec_profile_exists", language)
            )
            await callback.answer()
            return

        await EventRepository(session).create_event(
            event_type="specialist_registration_started",
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            payload={
                "source": "telegram_fsm",
                "callback_data": callback.data,
            },
            platform="telegram",
        )

        categories = await repository.list_active_categories(limit=100)
        await session.commit()

    if not categories:
        await callback.message.answer(t("spec_categories_missing", language))
        await callback.answer()
        return

    await state.update_data(
        user_language=language,
        category_ids=[str(item.id) for item in categories],
    )

    await show_callback_message(
        callback,
        t("spec_category_prompt", language),
        build_paged_keyboard(
            items=categories,
            prefix="spec_category",
            page_prefix="spec_categories_page",
            page=0,
            language=language,
            back_callback="spec_cancel",
        ),
    )
    await state.set_state(SpecialistForm.choosing_category)
    await callback.answer()
@specialist_form_router.callback_query(F.data.startswith("spec_categories_page:"))
async def paginate_categories(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"

    async with get_session() as session:
        categories = await SpecialistRepository(session).list_active_categories(limit=100)

    await show_callback_message(
        callback,
        t("spec_category_prompt", language),
        build_paged_keyboard(
            items=categories,
            prefix="spec_category",
            page_prefix="spec_categories_page",
            page=page,
            language=language,
            back_callback="M",
        ),
    )
    await callback.answer()


@specialist_form_router.callback_query(F.data.startswith("spec_category:"))
async def choose_category(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"
    category_ids = data.get("category_ids") or []

    try:
        item_index = int(callback.data.split(":", 1)[1])
        category_id = UUID(category_ids[item_index])
    except (ValueError, IndexError, KeyError):
        await callback.message.answer(t("spec_category_not_found_restart", language))
        await callback.answer()
        return

    async with get_session() as session:
        repository = SpecialistRepository(session)
        category = await repository.get_active_category(category_id)
        professions = await repository.list_active_professions_by_category(category_id, limit=100)

    if not category:
        await callback.message.answer(t("spec_category_not_found", language))
        await callback.answer()
        return

    if not professions:
        await callback.message.answer(t("spec_professions_missing", language))
        await callback.answer()
        return

    await state.update_data(
        category_id=str(category.id),
        category_name=item_name(category, language),
        profession_ids=[str(item.id) for item in professions],
    )

    await show_callback_message(
        callback,
        t("spec_profession_prompt", language),
        build_paged_keyboard(
            items=professions,
            prefix="spec_profession",
            page_prefix="spec_professions_page",
            page=0,
            language=language,
            back_callback="spec_back_to_categories",
        ),
    )
    await state.set_state(SpecialistForm.choosing_profession)
    await callback.answer()
@specialist_form_router.callback_query(F.data == "spec_back_to_categories")
async def back_to_categories(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"

    async with get_session() as session:
        categories = await SpecialistRepository(session).list_active_categories(limit=100)

    await show_callback_message(
        callback,
        t("spec_category_prompt", language),
        build_paged_keyboard(
            items=categories,
            prefix="spec_category",
            page_prefix="spec_categories_page",
            page=0,
            language=language,
            back_callback="M",
        ),
    )
    await state.set_state(SpecialistForm.choosing_category)
    await callback.answer()
    await state.update_data(category_ids=[str(item.id) for item in categories])

@specialist_form_router.callback_query(F.data.startswith("spec_professions_page:"))
async def paginate_professions(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    category_id = UUID(data["category_id"])
    language = data.get("user_language") or callback.from_user.language_code or "ru"

    async with get_session() as session:
        professions = await SpecialistRepository(session).list_active_professions_by_category(
            category_id,
            limit=100,
        )

    await state.update_data(profession_ids=[str(item.id) for item in professions])

    await show_callback_message(
        callback,
        t("spec_profession_prompt", language),
        build_paged_keyboard(
            items=professions,
            prefix="spec_profession",
            page_prefix="spec_professions_page",
            page=page,
            language=language,
            back_callback="spec_back_to_categories",
        ),
    )
    await callback.answer()

@specialist_form_router.callback_query(F.data.startswith("spec_profession:"))
async def choose_profession(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"
    profession_ids = data.get("profession_ids") or []

    try:
        item_index = int(callback.data.split(":", 1)[1])
        profession_id = UUID(profession_ids[item_index])
    except (ValueError, IndexError, KeyError):
        await callback.message.answer(t("spec_profession_not_found_back", language))
        await callback.answer()
        return

    async with get_session() as session:
        profession = await SpecialistRepository(session).get_active_profession(profession_id)

    if not profession:
        await callback.message.answer(t("spec_profession_not_found", language))
        await callback.answer()
        return

    await state.update_data(
        profession_id=str(profession.id),
        profession_name=item_name(profession, language),
    )

    await show_callback_message(
        callback,
        t("spec_location_prompt", language),
        location_mode_keyboard(language),
    )
    await state.set_state(SpecialistForm.choosing_location_mode)
    await callback.answer()

@specialist_form_router.callback_query(F.data == "spec_location_city")
async def choose_city_mode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"

    await show_callback_message(
        callback,
        t("spec_city_search_prompt", language),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("spec_back_btn", language),
                        callback_data="spec_back_to_location_mode",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("spec_cancel_btn", language),
                        callback_data="spec_cancel",
                    )
                ],
            ]
        ),
    )
    await state.set_state(SpecialistForm.entering_city_query)
    await callback.answer()

@specialist_form_router.callback_query(F.data == "spec_back_to_location_mode")
async def back_to_location_mode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"
    await show_callback_message(
        callback,
        t("spec_location_prompt", language),
        location_mode_keyboard(language),
    )
    await state.set_state(SpecialistForm.choosing_location_mode)
    await callback.answer()


@specialist_form_router.message(SpecialistForm.entering_city_query)
async def search_city_query(message: Message, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or message.from_user.language_code or "ru"
    query = (message.text or "").strip()

    if len(query) < 2:
        await message.answer(t("spec_city_query_too_short", language))
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
    except GeoServiceError as exc:
        await message.answer(
            t("spec_geo_provider_error", language).format(error=exc)
        )
        return

    if not candidates:
        await message.answer(t("spec_geo_candidates_not_found", language))
        return

    candidate_state = [candidate.to_state() for candidate in candidates]
    await state.update_data(geo_candidates=candidate_state)

    await message.answer(
        t("spec_geo_candidates_prompt", language),
        reply_markup=geo_candidates_keyboard(candidate_state, language),
    )
    await state.set_state(SpecialistForm.choosing_geo_place)


@specialist_form_router.callback_query(F.data.startswith("spec_geo_place:"))
async def choose_geo_place(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"
    candidates = data.get("geo_candidates") or []

    try:
        item_index = int((callback.data or "").split(":", 1)[1])
        candidate = candidates[item_index]
    except (ValueError, IndexError, KeyError, TypeError):
        await callback.message.answer(t("spec_geo_candidate_not_found", language))
        await callback.answer()
        return

    try:
        async with get_session() as session:
            place = await GeoService(GeoRepository(session)).confirm_place(candidate)
    except GeoServiceError as exc:
        await callback.message.answer(
            t("spec_geo_provider_error", language).format(error=exc)
        )
        await callback.answer()
        return

    await state.update_data(
        city_id=str(place.city_id),
        country_id=str(place.country_id),
        city_name=place.display_name or place.city_name,
        latitude=place.latitude,
        longitude=place.longitude,
        service_radius_km=25,
        geo_candidates=[],
    )

    await show_callback_message(callback, t("spec_display_name_prompt", language))
    await state.set_state(SpecialistForm.entering_display_name)
    await callback.answer()
    
@specialist_form_router.callback_query(F.data == "spec_location_geo")
async def geo_location_prompt(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"

    await callback.message.answer(
        t("spec_geo_prompt", language),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=t("spec_send_geo_btn", language),
                        request_location=True,
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await state.set_state(SpecialistForm.waiting_geo)
    await callback.answer()

@specialist_form_router.message(SpecialistForm.waiting_geo)
async def receive_geo_location(message: Message, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or message.from_user.language_code or "ru"

    if not message.location:
        await message.answer(t("spec_geo_required", language))
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
    except GeoServiceError as exc:
        await message.answer(
            t("spec_geo_provider_error", language).format(error=exc),
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if not candidates:
        await message.answer(
            t("spec_geo_candidates_not_found", language),
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    candidate_state = [candidate.to_state() for candidate in candidates]
    await state.update_data(
        geo_candidates=candidate_state,
        raw_latitude=message.location.latitude,
        raw_longitude=message.location.longitude,
    )

    await message.answer(
        t("spec_geo_candidates_prompt", language),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        t("spec_geo_reverse_confirm_prompt", language),
        reply_markup=geo_candidates_keyboard(candidate_state, language),
    )
    await state.set_state(SpecialistForm.choosing_geo_place)


@specialist_form_router.message(SpecialistForm.entering_display_name)
async def enter_display_name(message: Message, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or message.from_user.language_code or "ru"
    display_name = (message.text or "").strip()
    if len(display_name) < 2:
        await message.answer(t("spec_display_name_too_short", language))
        return

    await state.update_data(display_name=display_name)
    await message.answer(t("spec_description_prompt", language))
    await state.set_state(SpecialistForm.entering_description)


@specialist_form_router.message(SpecialistForm.entering_description)
async def enter_description(message: Message, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or message.from_user.language_code or "ru"
    description = (message.text or "").strip()
    if len(description) < 20:
        await message.answer(t("spec_description_too_short", language))
        return

    await state.update_data(short_description=description)
    await message.answer(t("spec_price_prompt", language))
    await state.set_state(SpecialistForm.entering_price)


@specialist_form_router.message(SpecialistForm.entering_price)
async def enter_price(message: Message, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or message.from_user.language_code or "ru"
    try:
        price_from, price_to = parse_price(message.text or "")
    except ValueError:
        await message.answer(t("spec_price_invalid", language))
        return

    await state.update_data(
        price_from=price_from,
        price_to=price_to,
        currency="EUR",
        price_unit="service",
    )

    await message.answer(
    t("spec_languages_prompt", language),
    reply_markup=language_keyboard(["ru"], language),
    )
    await state.update_data(languages=["ru"])
    await state.set_state(SpecialistForm.choosing_languages)


@specialist_form_router.callback_query(F.data.startswith("spec_lang_toggle:"))
async def toggle_language(callback: CallbackQuery, state: FSMContext):
    language_code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"
    selected = list(data.get("languages") or [])

    if language_code in selected:
        selected.remove(language_code)
    else:
        selected.append(language_code)

    if not selected:
        selected = ["ru"]

    await state.update_data(languages=selected)
    await show_callback_message(
        callback,
        t("spec_languages_prompt", language),
        language_keyboard(selected, language),
    )
    await callback.answer()


@specialist_form_router.callback_query(F.data == "spec_lang_done")
async def finish_languages(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"
    await show_callback_message(
        callback,
        t("spec_contact_prompt", language),
    )
    await state.set_state(SpecialistForm.entering_contact)
    await callback.answer()


@specialist_form_router.message(SpecialistForm.entering_contact)
async def enter_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or message.from_user.language_code or "ru"
    contact_text = (message.text or "").strip()
    if not contact_text:
        await message.answer(t("spec_contact_required", language))
        return

    await state.update_data(contact_text=contact_text)
    data = await state.get_data()

    price_text = t("spec_price_not_set", language)
    if data.get("price_from") and data.get("price_to"):
        price_text = f"{data['price_from']}-{data['price_to']} EUR"
    elif data.get("price_from"):
        price_text = f"{data['price_from']} EUR"

    summary = t("spec_summary", language).format(
        category=data.get("category_name"),
        profession=data.get("profession_name"),
        location=data.get("city_name"),
        display_name=data.get("display_name"),
        description=data.get("short_description"),
        price=price_text,
        languages=", ".join(data.get("languages") or ["ru"]),
        contact=data.get("contact_text"),
    )

    await message.answer(summary, reply_markup=confirm_keyboard(language))
    await state.set_state(SpecialistForm.confirming)


@specialist_form_router.callback_query(F.data == "spec_confirm")
async def confirm_specialist(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"
    async with get_session() as session:
        user = await get_current_user(session, callback.from_user.id)
        if not user:
            await callback.message.answer(t("spec_start_required", language))
            await callback.answer()
            return

        service = SpecialistService(SpecialistRepository(session))

        try:
            specialist = await service.create_pending_profile(
                SpecialistRegistrationData(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    language=language,
                    category_id=UUID(data["category_id"]),
                    profession_id=UUID(data["profession_id"]),
                    country_id=UUID(data["country_id"]) if data.get("country_id") else None,
                    city_id=UUID(data["city_id"]) if data.get("city_id") else None,
                    display_name=data["display_name"],
                    short_description=data["short_description"],
                    full_description=data["short_description"],
                    price_from=data.get("price_from"),
                    price_to=data.get("price_to"),
                    currency=data.get("currency") or "EUR",
                    price_unit=data.get("price_unit") or "service",
                    latitude=data.get("latitude"),
                    longitude=data.get("longitude"),
                    service_radius_km=data.get("service_radius_km") or 0,
                    languages=data.get("languages") or ["ru"],
                    service_title=data["display_name"],
                    service_description=data["short_description"],
                    contact_text=data["contact_text"],
                )
            )
        except SpecialistRegistrationError as exc:
            await callback.message.answer(
            t("spec_create_failed", language).format(error=exc))
            await callback.answer()
            return

    await state.clear()
    await callback.message.answer(
    t("spec_created", language).format(specialist_id=specialist.id),
    reply_markup=get_main_menu_keyboard(language),)
    await callback.answer()


@specialist_form_router.callback_query(F.data == "spec_cancel")
async def cancel_specialist_registration(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = data.get("user_language") or callback.from_user.language_code or "ru"
    await state.clear()
    await callback.message.answer(
    t("spec_cancelled", language),
    reply_markup=get_main_menu_keyboard(language),)
    await callback.answer()