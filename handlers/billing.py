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
from database.repositories.geo_repository import GeoRepository
from database.repositories.rate_limit import RateLimitRepository
from database.models import City, Country, Invoice, PaidFeature, Specialist
from database.repositories.billing import BillingRepository
from database.repositories.specialist import SpecialistRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard, normalize_language
from services.billing import BillingError, BillingService
from services.specialist import (
    SpecialistProfileUpdateData,
    SpecialistRegistrationError,
    SpecialistService,
)
from services.user import UserService
from ui.texts import t
from services.geo_service import GeoService, GeoServiceError
from services.rate_limit import RateLimitError, RateLimitService

billing_router = Router()
logger = logging.getLogger(__name__)


class SpecialistCabinetFSM(StatesGroup):
    entering_display_name = State()
    entering_description = State()
    entering_contact = State()
    choosing_category = State()
    choosing_profession = State()
    entering_location_query = State()
    choosing_geo_place = State()
    waiting_geo = State()

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

def billing_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("billing_promotions", language),
                    callback_data="BILL_FEATURES",
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

def cabinet_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("cabinet_specialist_profile", language),
                    callback_data="CAB_PROFILE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_promotions", language),
                    callback_data="BILL_PANEL",
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
                    text=t("cabinet_view_profile", language),
                    callback_data="CAB_PROFILE_VIEW",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("cabinet_edit_profile", language),
                    callback_data="CAB_PROFILE_EDIT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_promotions", language),
                    callback_data="BILL_PANEL",
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
                    text=t("cabinet_edit_direction", language),
                    callback_data="CAB_EDIT_CATEGORY",
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


def geo_candidates_keyboard(candidates: list[dict], language: str) -> InlineKeyboardMarkup:
    rows = []
    country_rows = []
    seen_countries = set()

    for index, candidate in enumerate(candidates):
        name = candidate.get("name") or candidate.get("display_name") or "-"
        country = candidate.get("country_name") or candidate.get("country_code") or "-"
        country_code = str(candidate.get("country_code") or "").upper()
        place_type = candidate.get("place_type") or candidate.get("osm_type") or "place"

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{index + 1}. {name}, {country} - {place_type}",
                    callback_data=f"CAB_GEO_PLACE:{index}",
                )
            ]
        )

        country_key = country_code or country
        if country_key and country_key not in seen_countries:
            seen_countries.add(country_key)
            country_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{t('cabinet_location_whole_country', language)}: {country}",
                        callback_data=f"CAB_GEO_COUNTRY:{index}",
                    )
                ]
            )

    rows.extend(country_rows)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="CAB_PROFILE_EDIT",
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
) -> str:
    if not specialist:
        return t("cabinet_profile_not_found", language)

    contact_text = (specialist.extra_metadata or {}).get("contact_text") or "-"
    return (
        f"{t('cabinet_profile_title', language)}\n\n"
        f"{t('cabinet_profile_name', language)}: {specialist.display_name}\n"
        f"{t('cabinet_profile_status', language)}: {specialist.status}\n"
        f"{t('cabinet_profile_description', language)}: {specialist.short_description}\n"
        f"{t('cabinet_profile_contacts', language)}: {contact_text}\n"
        f"{t('cabinet_profile_price', language)}: {specialist.price_from or '-'}-{specialist.price_to or '-'} {specialist.currency}\n"
        f"{t('cabinet_profile_location', language)}: {location_text}"
    )
    if not specialist:
        return t("cabinet_profile_not_found", language)

    contact_text = (specialist.extra_metadata or {}).get("contact_text") or "-"
    return (
        f"{t('cabinet_profile_title', language)}\n\n"
        f"{t('cabinet_profile_name', language)}: {specialist.display_name}\n"
        f"{t('cabinet_profile_status', language)}: {specialist.status}\n"
        f"{t('cabinet_profile_description', language)}: {specialist.short_description}\n"
        f"{t('cabinet_profile_contacts', language)}: {contact_text}\n"
        f"{t('cabinet_profile_price', language)}: {specialist.price_from or '-'}-{specialist.price_to or '-'} {specialist.currency}\n"
        f"{t('cabinet_profile_location', language)}: {specialist.city_id or '-'}"
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
async def show_cabinet(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    await state.clear()
    await callback.message.answer(
        t("menu_my_cabinet", language),
        reply_markup=cabinet_menu_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_PROFILE")
async def show_specialist_profile_menu(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    user, specialist, tenant_id = await get_current_specialist_for_telegram(callback.from_user.id)

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    location_text = await get_specialist_location_text(specialist, language)

    await callback.message.answer(
        format_specialist_profile_text(specialist, language, location_text),
        reply_markup=specialist_profile_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_PROFILE_VIEW")
async def view_specialist_profile(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    user, specialist, tenant_id = await get_current_specialist_for_telegram(callback.from_user.id)

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    location_text = await get_specialist_location_text(specialist, language)

    await callback.message.answer(
        format_specialist_profile_text(specialist, language, location_text),
        reply_markup=specialist_profile_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_PROFILE_EDIT")
async def edit_specialist_profile_menu(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
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
    language = normalize_language(callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_enter_name", language),
        reply_markup=profile_edit_back_keyboard(language),
    )
    await state.set_state(SpecialistCabinetFSM.entering_display_name)
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_EDIT_DESCRIPTION")
async def ask_edit_specialist_description(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_enter_description", language),
        reply_markup=profile_edit_back_keyboard(language),
    )
    await state.set_state(SpecialistCabinetFSM.entering_description)
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_EDIT_CONTACT")
async def ask_edit_specialist_contact(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_enter_contact", language),
        reply_markup=profile_edit_back_keyboard(language),
    )
    await state.set_state(SpecialistCabinetFSM.entering_contact)
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_EDIT_LOCATION")
async def ask_edit_specialist_location(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
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
        t("cabinet_location_prompt", language),
        reply_markup=location_edit_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_LOC_MANUAL")
async def ask_edit_specialist_location_manual(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    await callback.message.answer(
        t("cabinet_location_query_prompt", language),
        reply_markup=profile_edit_back_keyboard(language),
    )
    await state.set_state(SpecialistCabinetFSM.entering_location_query)
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_LOC_GEO")
async def ask_edit_specialist_location_geo(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
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
    language = normalize_language(message.from_user.language_code)
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
    except GeoServiceError as exc:
        await message.answer(
            t("cabinet_geo_provider_error", language).format(error=str(exc))
        )
        return

    if not candidates:
        await message.answer(t("cabinet_geo_candidates_not_found", language))
        return

    candidate_state = [candidate.to_state() for candidate in candidates]
    await state.update_data(cabinet_geo_candidates=candidate_state)

    await message.answer(
        t("cabinet_geo_candidates_prompt", language),
        reply_markup=geo_candidates_keyboard(candidate_state, language),
    )
    await state.set_state(SpecialistCabinetFSM.choosing_geo_place)


@billing_router.message(SpecialistCabinetFSM.waiting_geo)
async def receive_specialist_location_geo(message: Message, state: FSMContext):
    language = normalize_language(message.from_user.language_code)

    if not message.location:
        await message.answer(t("cabinet_geo_required", language))
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
            t("cabinet_geo_provider_error", language).format(error=str(exc)),
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if not candidates:
        await message.answer(
            t("cabinet_geo_candidates_not_found", language),
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    candidate_state = [candidate.to_state() for candidate in candidates]
    await state.update_data(cabinet_geo_candidates=candidate_state)

    await message.answer(
        t("cabinet_geo_candidates_prompt", language),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        t("cabinet_geo_candidates_prompt", language),
        reply_markup=geo_candidates_keyboard(candidate_state, language),
    )
    await state.set_state(SpecialistCabinetFSM.choosing_geo_place)

@billing_router.callback_query(F.data.startswith("CAB_GEO_PLACE:"))
async def choose_specialist_location_update(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
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
        reply_markup=specialist_profile_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(F.data.startswith("CAB_GEO_COUNTRY:"))
async def choose_specialist_country_update(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
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
        reply_markup=specialist_profile_keyboard(language),
    )
    await callback.answer()

@billing_router.callback_query(F.data == "CAB_EDIT_CATEGORY")
async def ask_edit_specialist_category(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
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

    await callback.message.answer(
        t("cabinet_choose_direction", language),
        reply_markup=indexed_items_keyboard(
            categories,
            prefix="CAB_CAT",
            language=language,
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("CAB_CAT:"))
async def choose_specialist_category_update(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    category_ids = data.get("cabinet_category_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
        category_id = category_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        professions = await SpecialistRepository(session).list_active_professions_by_category(
            UUID(category_id),
            limit=50,
        )

    await state.update_data(
        cabinet_pending_category_id=category_id,
        cabinet_profession_ids=[str(item.id) for item in professions],
    )
    await state.set_state(SpecialistCabinetFSM.choosing_profession)

    await callback.message.answer(
        t("cabinet_choose_profession", language),
        reply_markup=indexed_items_keyboard(
            professions,
            prefix="CAB_PROF",
            language=language,
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "CAB_EDIT_PROFESSION")
async def ask_edit_specialist_profession(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    user, specialist, tenant_id = await get_current_specialist_for_telegram(callback.from_user.id)

    if not user:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    if not specialist:
        await callback.answer(t("cabinet_profile_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        professions = await SpecialistRepository(session).list_active_professions_by_category(
            specialist.category_id,
            limit=50,
        )

    await state.update_data(
        cabinet_specialist_id=str(specialist.id),
        cabinet_tenant_id=str(tenant_id),
        cabinet_user_id=str(user.id),
        cabinet_pending_category_id=str(specialist.category_id),
        cabinet_profession_ids=[str(item.id) for item in professions],
    )
    await state.set_state(SpecialistCabinetFSM.choosing_profession)

    await callback.message.answer(
        t("cabinet_choose_profession", language),
        reply_markup=indexed_items_keyboard(
            professions,
            prefix="CAB_PROF",
            language=language,
        ),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("CAB_PROF:"))
async def choose_specialist_profession_update(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    profession_ids = data.get("cabinet_profession_ids") or []
    category_id = data.get("cabinet_pending_category_id")

    try:
        index = int((callback.data or "").split(":", 1)[1])
        profession_id = profession_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await save_specialist_profile_update(
        message=callback.message,
        state=state,
        category_id=UUID(category_id) if category_id else None,
        profession_id=UUID(profession_id),
    )
    await callback.answer()

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
    language = normalize_language(message.from_user.language_code)

    user_id = data.get("cabinet_user_id")
    tenant_id = data.get("cabinet_tenant_id")
    specialist_id = data.get("cabinet_specialist_id")

    if not user_id or not tenant_id or not specialist_id:
        await message.answer(t("cabinet_profile_not_found", language))
        await state.clear()
        return

    try:
        async with get_session() as session:
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
        reply_markup=specialist_profile_keyboard(language),
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
    language = normalize_language(callback.from_user.language_code)
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
    language = normalize_language(callback.from_user.language_code)
    await state.clear()
    await callback.message.answer(
        t("search_main_menu", language),
        reply_markup=get_main_menu_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "BILL_FEATURES")
async def list_billing_features(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
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
    language = normalize_language(callback.from_user.language_code)
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
    language = normalize_language(callback.from_user.language_code)
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