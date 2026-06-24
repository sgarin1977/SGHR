import uuid

from sqlalchemy import delete, select
from pathlib import Path

from database.models import (
    City,
    Country,
    Profession,
    Specialist,
    SpecialistCategory,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistService,
    User,
    UserAccount,
    UserRoleMapping,
    LegalDocument,
    UserConsent,
    EventLog,
)
from database.repositories.search import (
    SpecialistSearchFilters,
    SpecialistSearchRepository,
)
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from services.geo_search import GeoSearchService
from services.specialist import SpecialistRegistrationData
from services.specialist import SpecialistService as SpecialistRegistrationService
from utils.geo import haversine_distance_km, is_within_radius_km
from database.repositories.legal import LegalRepository
from services.legal import REQUIRED_SPECIALIST_CONSENTS, LegalService

LEGAL_TEST_VERSION = "test-beta-0.5"


def test_haversine_distance_between_lisbon_and_porto():
    lisbon_lat = 38.7223
    lisbon_lon = -9.1393
    porto_lat = 41.1579
    porto_lon = -8.6291

    distance = haversine_distance_km(
        lisbon_lat,
        lisbon_lon,
        porto_lat,
        porto_lon,
    )

    assert 270 <= distance <= 290


def test_is_within_radius_km():
    origin_lat = 38.7223
    origin_lon = -9.1393

    nearby_lat = 38.7078
    nearby_lon = -9.1366

    far_lat = 41.1579
    far_lon = -8.6291

    assert is_within_radius_km(
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        target_lat=nearby_lat,
        target_lon=nearby_lon,
        radius_km=5,
    )

    assert not is_within_radius_km(
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        target_lat=far_lat,
        target_lon=far_lon,
        radius_km=100,
    )


async def cleanup_test_user(session, platform_user_id: str):
    await session.rollback()

    account_result = await session.execute(
        select(UserAccount).where(
            UserAccount.platform == "telegram",
            UserAccount.platform_user_id == platform_user_id,
        )
    )
    account = account_result.scalar_one_or_none()

    if not account:
        await session.rollback()
        return

    user_id = account.user_id

    specialist_result = await session.execute(
        select(Specialist).where(Specialist.user_id == user_id)
    )
    specialist = specialist_result.scalar_one_or_none()

    if specialist:
        await session.execute(
            delete(SpecialistService).where(SpecialistService.specialist_id == specialist.id)
        )
        await session.execute(
            delete(SpecialistLanguage).where(SpecialistLanguage.specialist_id == specialist.id)
        )
        await session.execute(
            delete(SpecialistLocation).where(SpecialistLocation.specialist_id == specialist.id)
        )
        await session.execute(delete(Specialist).where(Specialist.id == specialist.id))

    await session.execute(delete(UserConsent).where(UserConsent.user_id == user_id))
    await session.execute(delete(EventLog).where(EventLog.user_id == user_id))    
    await session.execute(delete(UserRoleMapping).where(UserRoleMapping.user_id == user_id))
    await session.execute(delete(UserAccount).where(UserAccount.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()


async def get_reference_data(session):
    category_result = await session.execute(
        select(SpecialistCategory).where(SpecialistCategory.is_active.is_(True)).limit(1)
    )
    category = category_result.scalar_one_or_none()
    assert category is not None, "No active specialist category found. Seed beta taxonomy first."

    profession_result = await session.execute(
        select(Profession).where(
            Profession.category_id == category.id,
            Profession.is_active.is_(True),
        ).limit(1)
    )
    profession = profession_result.scalar_one_or_none()
    assert profession is not None, "No active profession found for selected category."

    city_result = await session.execute(
        select(City).where(
            City.is_active.is_(True),
            City.latitude.isnot(None),
            City.longitude.isnot(None),
        ).limit(1)
    )
    city = city_result.scalar_one_or_none()
    assert city is not None, "No active city with coordinates found. Seed beta cities first."

    country = await session.get(Country, city.country_id)
    assert country is not None

    return {
    "category_id": category.id,
    "profession_id": profession.id,
    "country_id": country.id,
    "city_id": city.id,
    "city_latitude": city.latitude,
    "city_longitude": city.longitude,
}


async def create_test_user(session):
    platform_user_id = f"test-search-{uuid.uuid4()}"

    user_repo = UserRepository(session)
    user_id = await user_repo.create_telegram_user_core(
        platform_user_id=platform_user_id,
        username="test_search_specialist",
        first_name="Search",
        last_name="Specialist",
        language_code="ru",
        role="client",
    )

    user = await session.get(User, user_id)
    assert user is not None
    assert user.tenant_id is not None

    return platform_user_id, user.id, user.tenant_id

async def cleanup_legal_documents(session, tenant_id):
    await session.rollback()

    await session.execute(
        delete(UserConsent).where(
            UserConsent.tenant_id == tenant_id,
            UserConsent.version == LEGAL_TEST_VERSION,
        )
    )
    await session.execute(
        delete(LegalDocument).where(
            LegalDocument.tenant_id == tenant_id,
            LegalDocument.version == LEGAL_TEST_VERSION,
        )
    )
    await session.commit()


async def ensure_legal_documents(session, tenant_id):
    for doc_type in REQUIRED_SPECIALIST_CONSENTS:
        session.add(
            LegalDocument(
                tenant_id=tenant_id,
                doc_type=doc_type,
                version=LEGAL_TEST_VERSION,
                language="ru",
                title=f"{doc_type} beta 0.5 test title",
                content_text=f"{doc_type} beta 0.5 test content",
                status="active",
            )
        )

    await session.commit()


async def accept_specialist_consents(session, tenant_id, user_id):
    await ensure_legal_documents(session, tenant_id)

    service = LegalService(LegalRepository(session))
    await service.accept_required_specialist_consents(
        tenant_id=tenant_id,
        user_id=user_id,
        language="ru",
        platform="telegram",
    )

async def create_active_search_specialist(session):
    platform_user_id, user_id, tenant_id = await create_test_user(session)
    refs = await get_reference_data(session)

    await cleanup_legal_documents(session, tenant_id)
    await accept_specialist_consents(session, tenant_id, user_id)

    service = SpecialistRegistrationService(SpecialistRepository(session))
    specialist = await service.create_pending_profile(
        SpecialistRegistrationData(
            tenant_id=tenant_id,
            user_id=user_id,
            category_id=refs["category_id"],
            profession_id=refs["profession_id"],
            country_id=refs["country_id"],
            city_id=refs["city_id"],
            display_name="Searchable Beta Specialist",
            short_description="Experienced searchable beta specialist for geo tests.",
            full_description="Detailed searchable beta specialist profile.",
            price_from=40,
            price_to=80,
            currency="EUR",
            price_unit="service",
            work_format="mixed",        
            latitude=refs["city_latitude"],
            longitude=refs["city_longitude"],
            service_radius_km=25,
            languages=["ru", "en"],
            service_title="Searchable beta service",
            service_description="Service created by beta 0.5 search test.",
            contact_text="Contact inside SGHR beta chat",
        )
    )

    specialist.status = "active"
    await session.commit()

    return platform_user_id, user_id, tenant_id, specialist, refs


async def test_search_specialists_by_city_and_category(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
    await create_active_search_specialist(db_session))

    try:
        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_city(
    city_id=refs["city_id"],
    category_id=refs["category_id"],
    limit=10,
    offset=0,
)

        result_ids = {item.specialist.id for item in results}

        assert specialist.id in result_ids
        assert all(item.specialist.status == "active" for item in results)
        assert all(item.specialist.city_id == refs["city_id"] for item in results)
        assert all(item.specialist.category_id == refs["category_id"] for item in results)

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)      


async def test_search_specialists_by_radius(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
    await create_active_search_specialist(db_session)
)
    try:
        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_radius(
            latitude=float(refs["city_latitude"]),
            longitude=float(refs["city_longitude"]),
            radius_km=5,
            category_id=refs["category_id"],
            limit=10,
            offset=0,
        )

        result_by_id = {
            item.specialist.id: item
            for item in results
        }

        assert specialist.id in result_by_id
        assert result_by_id[specialist.id].distance_km is not None
        assert result_by_id[specialist.id].distance_km <= 5

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)       

async def test_country_wide_search_includes_specialist_without_coordinates(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        # Імітуємо реєстрацію спеціаліста з локацією «Вся країна».
        specialist.city_id = None
        specialist.latitude = None
        specialist.longitude = None
        specialist.service_radius_km = 0

        location_result = await db_session.execute(
            select(SpecialistLocation).where(
                SpecialistLocation.specialist_id == specialist.id,
                SpecialistLocation.is_current.is_(True),
            )
        )

        for location in location_result.scalars().all():
            location.city_id = None
            location.latitude = None
            location.longitude = None

        await db_session.commit()

        repository = SpecialistSearchRepository(db_session)

        country_results = await repository.search_within_radius(
            latitude=float(refs["city_latitude"]),
            longitude=float(refs["city_longitude"]),
            radius_km=5,
            country_wide=True,
            country_id=refs["country_id"],
            category_id=refs["category_id"],
            limit=200,
        )

        radius_results = await repository.search_within_radius(
            latitude=float(refs["city_latitude"]),
            longitude=float(refs["city_longitude"]),
            radius_km=5,
            country_wide=False,
            country_id=refs["country_id"],
            category_id=refs["category_id"],
            limit=200,
        )

        country_result_by_id = {
            found_specialist.id: distance
            for found_specialist, distance in country_results
        }
        radius_result_ids = {
            found_specialist.id
            for found_specialist, _distance in radius_results
        }

        assert specialist.id in country_result_by_id
        assert country_result_by_id[specialist.id] is None
        assert specialist.id not in radius_result_ids

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_profile_location_edit_replaces_current_specialist_location(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        repository = SpecialistRepository(db_session)

        await repository.update_specialist_profile_fields(
            specialist_id=specialist.id,
            user_id=user_id,
            country_id=refs["country_id"],
            city_id=None,
            latitude=None,
            longitude=None,
            clear_city=True,
            clear_coordinates=True,
            service_radius_km=0,
        )

        location_result = await db_session.execute(
            select(SpecialistLocation)
            .where(
                SpecialistLocation.specialist_id == specialist.id,
            )
            .order_by(SpecialistLocation.created_at)
        )
        locations = list(location_result.scalars().all())

        current_locations = [
            location
            for location in locations
            if location.is_current
        ]
        old_locations = [
            location
            for location in locations
            if not location.is_current
        ]

        await db_session.refresh(specialist)

        assert len(locations) == 2
        assert len(current_locations) == 1
        assert len(old_locations) == 1

        current_location = current_locations[0]

        assert current_location.country_id == refs["country_id"]
        assert current_location.city_id is None
        assert current_location.latitude is None
        assert current_location.longitude is None
        assert current_location.location_source == "profile_edit"

        assert specialist.country_id == refs["country_id"]
        assert specialist.city_id is None
        assert specialist.latitude is None
        assert specialist.longitude is None
        assert specialist.service_radius_km == 0

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_search_by_radius_uses_current_specialist_location_coordinates(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        specialist.latitude = None
        specialist.longitude = None

        location_result = await db_session.execute(
            select(SpecialistLocation).where(
                SpecialistLocation.specialist_id == specialist.id,
                SpecialistLocation.is_current.is_(True),
            )
        )
        location = location_result.scalar_one()
        location.latitude = refs["city_latitude"]
        location.longitude = refs["city_longitude"]

        await db_session.commit()

        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_radius(
            latitude=float(refs["city_latitude"]),
            longitude=float(refs["city_longitude"]),
            radius_km=5,
            category_id=refs["category_id"],
            limit=10,
            offset=0,
        )

        result_by_id = {
            item.specialist.id: item
            for item in results
        }

        assert specialist.id in result_by_id
        assert result_by_id[specialist.id].distance_km is not None
        assert result_by_id[specialist.id].distance_km <= 5

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_search_performed_event_is_created(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        await search_service.search_by_city(
            city_id=refs["city_id"],
            category_id=refs["category_id"],
            limit=10,
            offset=0,
            requester_user_id=user_id,
            tenant_id=tenant_id,
            log_event=True,
        )

        result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == user_id,
                EventLog.event_type == "search_performed",
                EventLog.entity_type == "specialist_search",
            )
        )
        event = result.scalar_one_or_none()

        assert event is not None
        assert event.payload["city_id"] == str(refs["city_id"])
        assert event.payload["category_id"] == str(refs["category_id"])
        assert event.payload["results_count"] >= 1

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_public_specialist_card_masks_pii_and_logs_view(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        card = await search_service.get_public_card(
            specialist_id=specialist.id,
            requester_user_id=user_id,
            tenant_id=tenant_id,
            distance_km=1.2,
            log_event=True,
        )

        assert card is not None
        assert card.specialist_id == specialist.id
        assert card.display_name == specialist.display_name
        assert card.short_description == specialist.short_description
        assert card.city_id == refs["city_id"]
        assert card.city_name
        assert card.distance_km == 1.2
        assert "en" in card.languages
        assert card.category_name
        assert card.profession_name
        assert card.work_format == specialist.work_format
        assert isinstance(card.service_titles, list)
        card_data = card.__dict__

        assert "contact_text" not in card_data
        assert "metadata" not in card_data
        assert "extra_metadata" not in card_data
        assert "latitude" not in card_data
        assert "longitude" not in card_data
        assert "email" not in card_data
        assert "phone" not in card_data
        assert "username" not in card_data

        result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == user_id,
                EventLog.event_type == "specialist_viewed",
                EventLog.entity_type == "specialist",
                EventLog.entity_id == specialist.id,
            )
        )
        event = result.scalar_one_or_none()

        assert event is not None

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_search_handler_does_not_parse_uuid_from_callback_data():
    source = open("handlers/search.py", encoding="utf-8").read()

    forbidden_fragments = [
        "UUID(callback.data.split",
        "UUID(callback.data.rsplit",
        "callback_data=f\"search_category:{item.id}\"",
        "callback_data=f\"search_city:{item.id}\"",
        "callback_data=f\"search_result:{specialist.id}\"",
        "callback_data=f\"search_card:{specialist.id}\"",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source


def test_search_callback_literals_fit_telegram_limit():
    source = open("handlers/search.py", encoding="utf-8").read()

    callback_literals = [
        "M_FIND",
        "search_start",
        "search_menu",
        "search_mode_city",
        "search_mode_geo",
        "search_category:",
        "search_categories_page:",
        "search_city:",
        "search_cities_page:",
        "search_results_page:",
        "search_result:",
        "search_card:",
        "search_profession:",
        "search_professions_page:",
        "search_profession_all",
        "search_radius:",
        "search_lang:",
        "search_show_results",
        "search_price:",
        "search_premium_toggle",
        "search_work:",
        "search_rating:",
        "search_contact_pending",
        "search_favorite_pending",
        "search_report_pending",
    ]

    for callback_data in callback_literals:
        assert len(callback_data.encode("utf-8")) <= 64


def test_search_visible_texts_are_i18n_ready():
    source = open("handlers/search.py", encoding="utf-8").read()

    forbidden_fragments = [
        "Все профессии",
        "Выберите профессию",
        "Профессии для категории",
        "Профессия не найдена",
        "Rating:",
        "\"verified\"",
        "\"premium\"",
        "Выбрать город",
        "Найти рядом",
        "Назад",
        "В меню",
        "Новый поиск",
        "цена не указана",
        "Расстояние",
        "Найденные специалисты",
        "Специалисты не найдены",
        "Категории специалистов не настроены",
        "Выберите категорию",
        "Категория не найдена",
        "Как искать специалиста",
        "Города не настроены",
        "Выберите город",
        "Город не найден",
        "Отправьте вашу геолокацию",
        "Пожалуйста, отправьте геолокацию",
        "Ищу специалистов рядом",
        "Главное меню SGHR Beta",
        "Настройте фильтры",
        "Показать результаты",
        "Любой язык",
        "Любая цена",
        "До 50 EUR",
        "От 100 EUR",
        "Только premium",
        "Все тарифы",
        "Любой формат",
        "Удаленно",
        "На месте",
        "Смешанный",
        "Любой рейтинг",
        "Рейтинг от 4",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source


def test_public_card_model_does_not_expose_pii_fields():
    source = open("services/geo_search.py", encoding="utf-8").read()
    card_source = source.split("class SpecialistPublicCard:", 1)[1].split("class GeoSearchService:", 1)[0]

    forbidden_fragments = [
        "contact_text",
        "metadata",
        "extra_metadata",
        "latitude",
        "longitude",
        "email",
        "phone",
        "username",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in card_source
async def test_search_filters_price_language_premium_work_format(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        specialist.is_verified = False
        specialist.is_premium = True
        specialist.work_format = "remote"
        await db_session.commit()

        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_city(
            city_id=refs["city_id"],
            category_id=refs["category_id"],
            price_min=30,
            price_max=90,
            language_code="en",
            verified_only=True,
            premium_only=True,
            work_format="remote",
            limit=10,
            offset=0,
        )

        result_ids = {item.specialist.id for item in results}

        assert specialist.id in result_ids
        assert any(item.specialist.is_verified is False for item in results)
        assert all(item.specialist.is_premium for item in results)
        assert all(item.specialist.work_format == "remote" for item in results)

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_search_excludes_blocked_and_deleted_users(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        user = await db_session.get(User, user_id)
        user.status = "blocked"
        await db_session.commit()

        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        blocked_results = await search_service.search_by_city(
            city_id=refs["city_id"],
            category_id=refs["category_id"],
            limit=10,
            offset=0,
        )

        user.status = "deleted"
        await db_session.commit()

        deleted_results = await search_service.search_by_city(
            city_id=refs["city_id"],
            category_id=refs["category_id"],
            limit=10,
            offset=0,
        )

        assert specialist.id not in {item.specialist.id for item in blocked_results}
        assert specialist.id not in {item.specialist.id for item in deleted_results}

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_search_pagination_clamps_page_size_to_10(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_city(
            city_id=refs["city_id"],
            category_id=refs["category_id"],
            limit=50,
            offset=0,
        )

        assert len(results) <= 10

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_search_radius_clamps_to_100_km(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_radius(
            latitude=float(refs["city_latitude"]),
            longitude=float(refs["city_longitude"]),
            radius_km=500,
            category_id=refs["category_id"],
            limit=10,
            offset=0,
        )

        result = next(item for item in results if item.specialist.id == specialist.id)

        assert result.distance_km is not None
        assert result.distance_km <= 100

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_search_ranking_orders_premium_rating_and_risk_without_verified_bonus(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )
    platform_user_id_2, user_id_2, tenant_id_2, specialist_2, refs_2 = (
        await create_active_search_specialist(db_session)
    )

    try:
        specialist.display_name = "Lower ranked specialist"
        specialist.rating = 1
        specialist.is_verified = False
        specialist.is_premium = False
        specialist.work_format = "at_specialist"
        user = await db_session.get(User, user_id)
        user.profile_completion_score = 20
        user.risk_score = 80

        specialist_2.category_id = refs["category_id"]
        specialist_2.profession_id = refs["profession_id"]
        specialist_2.country_id = refs["country_id"]
        specialist_2.city_id = refs["city_id"]
        specialist_2.latitude = refs["city_latitude"]
        specialist_2.longitude = refs["city_longitude"]
        specialist_2.display_name = "Higher ranked specialist"
        specialist_2.rating = 5
        specialist_2.is_verified = True
        specialist_2.is_premium = True
        specialist_2.work_format = "at_specialist"
        user_2 = await db_session.get(User, user_id_2)
        user_2.profile_completion_score = 100
        user_2.risk_score = 0

        await db_session.commit()

        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_radius(
            latitude=float(refs["city_latitude"]),
            longitude=float(refs["city_longitude"]),
            radius_km=5,
            category_id=refs["category_id"],
            profession_id=refs["profession_id"],
            sort_by="relevance",
            limit=10,
            offset=0,
            work_format="at_specialist",
)

        result_ids = [item.specialist.id for item in results]

        assert specialist.id in result_ids
        assert specialist_2.id in result_ids
        assert result_ids.index(specialist_2.id) < result_ids.index(specialist.id)

        result_by_id = {
            item.specialist.id: item
            for item in results
        }
        assert result_by_id[specialist_2.id].ranking_score > result_by_id[specialist.id].ranking_score

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_test_user(db_session, platform_user_id_2)
        await cleanup_legal_documents(db_session, tenant_id)
        await cleanup_legal_documents(db_session, tenant_id_2)

async def test_search_ranking_uses_reviews_activity_and_stable_tiebreak(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )
    platform_user_id_2, user_id_2, tenant_id_2, specialist_2, refs_2 = (
        await create_active_search_specialist(db_session)
    )

    try:
        specialist.display_name = "Lower tie-break specialist"
        specialist.category_id = refs["category_id"]
        specialist.profession_id = refs["profession_id"]
        specialist.country_id = refs["country_id"]
        specialist.city_id = refs["city_id"]
        specialist.latitude = refs["city_latitude"]
        specialist.longitude = refs["city_longitude"]
        specialist.is_verified = True
        specialist.is_premium = False
        specialist.priority_score = 0
        specialist.rating = 4
        specialist.is_verified = False
        specialist_2.is_verified = False
        specialist.reviews_count = 1
        specialist.work_format = "at_specialist"

        specialist_2.display_name = "Higher tie-break specialist"
        specialist_2.category_id = refs["category_id"]
        specialist_2.profession_id = refs["profession_id"]
        specialist_2.country_id = refs["country_id"]
        specialist_2.city_id = refs["city_id"]
        specialist_2.latitude = refs["city_latitude"]
        specialist_2.longitude = refs["city_longitude"]
        specialist_2.is_verified = False
        specialist_2.is_premium = False
        specialist_2.priority_score = 0
        specialist_2.rating = 4
        specialist_2.reviews_count = 3
        specialist_2.work_format = "at_specialist"

        await db_session.commit()

        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_radius(
            latitude=float(refs["city_latitude"]),
            longitude=float(refs["city_longitude"]),
            radius_km=5,
            category_id=refs["category_id"],
            profession_id=refs["profession_id"],
            sort_by="distance",
            limit=10,
            offset=0,
            work_format="at_specialist",
        )

        result_ids = [item.specialist.id for item in results]

        assert specialist.id in result_ids
        assert specialist_2.id in result_ids
        assert result_ids.index(specialist_2.id) < result_ids.index(specialist.id)

        specialist_2.reviews_count = specialist.reviews_count
        specialist.updated_at = specialist_2.updated_at

        await db_session.commit()

        results = await search_service.search_by_radius(
            latitude=float(refs["city_latitude"]),
            longitude=float(refs["city_longitude"]),
            radius_km=5,
            category_id=refs["category_id"],
            profession_id=refs["profession_id"],
            sort_by="distance",
            limit=10,
            offset=0,
            work_format="at_specialist",
        )

        result_ids = [
            item.specialist.id
            for item in results
            if item.specialist.id in {specialist.id, specialist_2.id}
        ]

        assert result_ids == sorted(result_ids, key=str)

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_test_user(db_session, platform_user_id_2)
        await cleanup_legal_documents(db_session, tenant_id)
        await cleanup_legal_documents(db_session, tenant_id_2)

def test_search_handler_passes_all_filters_to_city_search():
    source = open("handlers/search.py", encoding="utf-8").read()

    city_branch = source.split("elif city_id:", 1)[1].split(
        "else:",
        1,
    )[0]

    required_fragments = [
        "search_by_city",
        "city_id=city_id",
        "category_id=category_id",
        "profession_id=profession_id",
        "price_min=price_min",
        "price_max=price_max",
        "language_code=language_code",
        "verified_only=verified_only",
        "premium_only=premium_only",
        "work_format=work_format",
        "rating_min=rating_min",
        "limit=PER_PAGE + 1",
        "offset=page * PER_PAGE",
    ]

    for fragment in required_fragments:
        assert fragment in city_branch

def test_repository_candidate_query_limit_can_exceed_telegram_page_size():
    filters = SpecialistSearchFilters(limit=200)

    assert filters.normalized_page_size == 10
    assert filters.query_limit == 200

async def test_search_filters_rating_min(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        specialist.rating = 4
        await db_session.commit()

        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        matching_results = await search_service.search_by_city(
            city_id=refs["city_id"],
            category_id=refs["category_id"],
            rating_min=3,
            limit=10,
            offset=0,
        )

        too_high_results = await search_service.search_by_city(
            city_id=refs["city_id"],
            category_id=refs["category_id"],
            rating_min=5,
            limit=10,
            offset=0,
        )

        assert specialist.id in {item.specialist.id for item in matching_results}
        assert specialist.id not in {item.specialist.id for item in too_high_results}

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_search_handler_passes_rating_filter_to_city_and_radius_search():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "rating_min = data.get(\"rating_min\")" in source

    radius_branch = source.split("if has_geo:", 1)[1].split(
        "elif city_id:",
        1,
    )[0]
    city_branch = source.split("elif city_id:", 1)[1].split(
        "else:",
        1,
    )[0]

    assert "rating_min=rating_min" in radius_branch
    assert "rating_min=rating_min" in city_branch

def test_specialist_card_has_action_callbacks_without_uuid_payloads():
    source = open("handlers/search.py", encoding="utf-8").read()

    callback_literals = [
        "search_contact_pending",
        "search_favorite_pending",
        "search_report_pending",
    ]

    for callback_data in callback_literals:
        assert f'callback_data="{callback_data}"' in source
        assert len(callback_data.encode("utf-8")) <= 64

    forbidden_fragments = [
        'callback_data=f"search_contact_pending:{',
        'callback_data=f"search_favorite_pending:{',
        'callback_data=f"search_report_pending:{',
        "UUID(callback.data.split",
        "UUID(callback.data.rsplit",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

def test_geo_distance_wrapper_keeps_haversine_default_api():
    from utils.geo import calculate_distance_km, get_geo_mode

    distance = calculate_distance_km(
        38.7223,
        -9.1393,
        41.1579,
        -8.6291,
        mode="haversine",
    )

    postgis_placeholder_distance = calculate_distance_km(
        38.7223,
        -9.1393,
        41.1579,
        -8.6291,
        mode="postgis",
    )

    assert get_geo_mode() in {"haversine", "postgis"}
    assert 270 <= distance <= 290
    assert postgis_placeholder_distance == distance

def test_search_callback_registry_exists_for_beta_05():
    source = open("ui/buttons.py", encoding="utf-8").read()

    required_names = [
        "CB_MAIN_FIND_SPECIALIST",
        "CB_SEARCH_START",
        "CB_SEARCH_MENU",
        "CB_SEARCH_CATEGORY",
        "CB_SEARCH_CATEGORY_PAGE",
        "CB_SEARCH_PROFESSION",
        "CB_SEARCH_PROFESSION_PAGE",
        "CB_SEARCH_PROFESSION_ALL",
        "CB_SEARCH_MODE_CITY",
        "CB_SEARCH_MODE_GEO",
        "CB_SEARCH_CITY",
        "CB_SEARCH_CITY_PAGE",
        "CB_SEARCH_RESULT",
        "CB_SEARCH_RESULTS_PAGE",
        "CB_SEARCH_RADIUS",
        "CB_SEARCH_LANGUAGE",
        "CB_SEARCH_PRICE",
        "CB_SEARCH_RATING",
        "CB_SEARCH_WORK",
        "CB_SEARCH_PREMIUM_TOGGLE",
        "CB_SEARCH_SHOW_RESULTS",
        "CB_SEARCH_CONTACT_PENDING",
        "CB_SEARCH_FAVORITE_PENDING",
        "CB_SEARCH_REPORT_PENDING",
        "def cb(",
    ]

    for name in required_names:
        assert name in source
def test_beta_05_tz_static_contract_is_covered():
    search_repo = open("database/repositories/search.py", encoding="utf-8").read()
    geo_service = open("services/geo_search.py", encoding="utf-8").read()
    search_handler = open("handlers/search.py", encoding="utf-8").read()
    geo_utils = open("utils/geo.py", encoding="utf-8").read()
    buttons = open("ui/buttons.py", encoding="utf-8").read()

    repo_required = [
        "class SpecialistSearchFilters",
        "category_id",
        "profession_id",
        "city_id",
        "latitude",
        "longitude",
        "radius_km",
        "price_min",
        "price_max",
        "language_code",
        "verified_only",
        "premium_only",
        "rating_min",
        "work_format",
        "normalized_radius_km",
        "normalized_page_size",
        "query_limit",
        "search_performed",
        "specialist_viewed",
        "User.status.notin_",
        "SpecialistLocation.is_current.is_(True)",
    ]

    service_required = [
        "class SpecialistSearchResult",
        "class SpecialistPublicCard",
        "ranking_score",
        "calculate_distance_km",
        "search_by_city",
        "search_by_radius",
        "get_public_card",
        "log_search_performed",
        "log_specialist_viewed",
        "distance_score * 0.30",
        "rating_score * 0.20",
        "response_score * 0.15",
        "profile_completion * 0.10",
        "premium_boost * 0.10",
        "freshness_score * 0.05",
        "- risk_penalty",
    ]

    handler_required = [
        "M_FIND",
        "SpecialistSearchFSM",
        "choosing_category",
        "choosing_profession",
        "choosing_filters",
        "viewing_results",
        "category_ids",
        "profession_ids",
        "result_specialist_ids",
        "search_contact_pending",
        "search_favorite_pending",
        "search_report_pending",
        "rating_min=rating_min",
        "premium_only=premium_only",
        "work_format=work_format",
    ]

    geo_required = [
        "DEFAULT_GEO_MODE",
        "get_geo_mode",
        "calculate_distance_km",
        "haversine_distance_km",
    ]

    buttons_required = [
        "CB_SEARCH_START",
        "CB_SEARCH_RESULT",
        "CB_SEARCH_RATING",
        "CB_SEARCH_CONTACT_PENDING",
        "def cb(",
    ]

    for fragment in repo_required:
        assert fragment in search_repo

    for fragment in service_required:
        assert fragment in geo_service

    for fragment in handler_required:
        assert fragment in search_handler

    for fragment in geo_required:
        assert fragment in geo_utils

    for fragment in buttons_required:
        assert fragment in buttons

def test_search_ux_uses_single_filter_dashboard_instead_of_wizard():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
        "format_search_filters_summary",
        "search_filters_keyboard",
        "search_filter_category",
        "search_filter_profession",
        "search_filter_location",
        "search_filter_radius",
        "search_advanced_filters",
        "search_reset_filters",
        "search_show_results",
        "search_menu",
        "category_id",
        "profession_id",
        "country_id",
        "city_id",
        "latitude",
        "longitude",
        "radius_km",
        "work_format",
        "language_code",
        "price_min",
        "price_max",
        "sort_by",
        "page",
    ]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "search_mode_keyboard",
        "search_mode_city",
        "search_mode_geo",
        "choosing_mode",
        "search_choose_city_btn",
        "search_nearby_btn",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

def test_search_ux_has_flat_filter_actions_without_nested_mode_step():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
        "format_search_filters_summary",
        "search_filters_keyboard",
        "search_filter_category",
        "search_filter_profession",
        "search_filter_location",
        "search_filter_radius",
        "search_advanced_filters",
        "search_reset_filters",
        "search_show_results",
        "search_menu",
        "await show_filters(callback, state)",
    ]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "search_mode_keyboard",
        "search_mode_city",
        "search_mode_geo",
        "choosing_mode",
        "search_choose_city_btn",
        "search_nearby_btn",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source


def test_search_location_uses_geo_provider_candidates_with_confirmation():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
    "GeoService",
    "GeoRepository",
    "search_location_city",
    "search_location_geo",
    "entering_location_query",
    "choosing_geo_place",
    "search_places",
    "nearby_places",
    "limit=4",
    "confirm_place",
    "search_geo_candidates",
    "candidate.to_state()",
    "callback_data=f\"search_geo_place:{index}\"",
    "city_id=str(place.city_id)",
    "country_id=str(place.country_id)",
    "latitude=place.latitude",
    "longitude=place.longitude",
]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "list_active_cities(limit=100)",
        "callback_data=f\"search_city:{item.id}\"",
        "F.data.startswith(\"search_city:\")",
        "F.data.startswith(\"search_cities_page:\")",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source


def test_search_radius_work_language_price_sort_are_separate_quick_filters():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
        "search_radius_keyboard",
        "search_work_format_keyboard",
        "search_language_keyboard",
        "search_price_keyboard",
        "search_sort_keyboard",
        "callback_data=\"search_radius:5\"",
        "callback_data=\"search_radius:10\"",
        "callback_data=\"search_radius:25\"",
        "callback_data=\"search_radius:50\"",
        "callback_data=\"search_radius:100\"",
        "callback_data=\"search_radius:country\"",
        "callback_data=\"search_work:any\"",
        "callback_data=\"search_work:at_client\"",
        "callback_data=\"search_work:at_specialist\"",
        "callback_data=\"search_work:remote\"",
        "callback_data=\"search_work:mixed\"",
        "callback_data=\"search_lang:any\"",
        "callback_data=\"search_lang:ru\"",
        "callback_data=\"search_lang:pt\"",
        "callback_data=\"search_lang:en\"",
        "callback_data=\"search_price:any\"",
        "callback_data=\"search_price:0_25\"",
        "callback_data=\"search_price:0_50\"",
        "callback_data=\"search_price:0_100\"",
        "callback_data=\"search_sort:distance\"",
        "callback_data=\"search_sort:relevance\"",
    ]

    for fragment in required_fragments:
        assert fragment in source


def test_search_empty_results_offer_tz10_c11_recovery_actions():
    source = open("handlers/search.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "empty_results_keyboard" in source
    assert "next_empty_radius_suggestion" in source
    assert "search_empty_increase_radius_to" in source
    assert "search_empty_increase_radius_country" in source
    assert "format_empty_results_text" in source
    assert "search_empty_summary" in source

    empty_keyboard_block = source.split(
        "def empty_results_keyboard",
        1,
    )[1].split(
        "def format_empty_results_text",
        1,
    )[0]

    assert 'callback_data="search_empty_increase_radius"' in empty_keyboard_block
    assert 'callback_data="search_empty_reset_price"' in empty_keyboard_block
    assert 'callback_data="search_location_without"' in empty_keyboard_block
    assert 'callback_data="search_reset_filters"' in empty_keyboard_block
    assert 'callback_data="search_filters"' in empty_keyboard_block

    assert "async def empty_reset_price" in source
    assert "price_min=None" in source
    assert "price_max=None" in source
    assert "await render_results(event=callback, state=state, page=0)" in source

    assert 'event_type="empty_search"' in source

    assert "search_empty_reset_price" in texts_source

def test_search_cards_are_readable_and_keep_contact_flow_separate():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
        "format_specialist_result",
        "format_public_card",
        "search_back_to_filters",
        "search_contact_pending",
        "contact_disclaimer_text",
        "contact_disclaimer_continue",
        "ContactChatService(ContactChatRepository(session)).create_contact_request",
    ]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "city_id",
        "specialist.id}",
        "latitude",
        "longitude",
    ]

    card_source = source.split("def format_public_card", 1)[1].split("async def show_filters", 1)[0]
    for fragment in forbidden_fragments:
        assert fragment not in card_source

def test_search_handler_does_not_query_professions_directly():
    handler_source = open("handlers/search.py", encoding="utf-8").read()
    repo_source = open("database/repositories/specialist.py", encoding="utf-8").read()

    assert "list_active_professions" in repo_source
    assert "select(Profession)" not in handler_source
    assert "from database.models import Profession" not in handler_source

def test_search_sort_filter_is_passed_to_search_service():
    handler_source = open("handlers/search.py", encoding="utf-8").read()
    service_source = open("services/geo_search.py", encoding="utf-8").read()
    repo_source = open("database/repositories/search.py", encoding="utf-8").read()

    assert "sort_by: str = \"distance\"" in repo_source
    assert "sort_by = data.get(\"sort_by\") or \"distance\"" in handler_source
    assert "sort_by=sort_by" in handler_source
    assert "sort_by: str = \"distance\"" in service_source
    assert "sort_by: str = \"relevance\"" in service_source
    assert "filters.sort_by == \"distance\"" in service_source
    assert "filters.sort_by == \"relevance\"" in service_source

def test_specialist_card_back_button_returns_to_current_results_page():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "def card_keyboard(language: str, results_page: int = 0)" in source
    assert 'callback_data=f"search_results_page:{results_page}"' in source
    assert "results_page = int(data.get(\"results_page\") or 0)" in source
    assert "card_keyboard(language, results_page)" in source
    assert 'callback_data="search_results_page:0"' not in source

def test_country_wide_radius_is_not_fake_100_km():
    handler_source = open("handlers/search.py", encoding="utf-8").read()
    service_source = open("services/geo_search.py", encoding="utf-8").read()
    repo_source = open("database/repositories/search.py", encoding="utf-8").read()

    assert "country_id: UUID | None = None" in repo_source
    assert "SpecialistLocation.country_id == filters.country_id" in repo_source
    assert "country_wide: bool = False" in service_source
    assert "search_within_radius" in service_source
    assert "country_id=country_id" in service_source
    assert "country_wide=country_wide" in service_source
    assert "if not country_wide:" in repo_source
    assert "distance_expr <= radius_km" in repo_source
    assert "country_wide=False" in handler_source
    assert "country_wide=True" in handler_source
    assert "await state.update_data(country_wide=True, page=0)" in handler_source
    assert "radius_km=100" not in handler_source

def test_search_telegram_location_uses_nearby_candidates_not_single_reverse_result():
    handler_source = open("handlers/search.py", encoding="utf-8").read()
    service_source = open("services/geo_service.py", encoding="utf-8").read()
    provider_source = open("services/geo_provider.py", encoding="utf-8").read()

    assert "async def search_nearby" in provider_source
    assert "viewbox" in provider_source
    assert "bounded" in provider_source
    assert "distance_km" in provider_source
    assert "async def nearby_places" in service_source
    assert "search_nearby" in service_source
    assert "nearby_places(" in handler_source
    assert "limit=4" in handler_source
    assert "candidate_state = dedupe_geo_candidate_states(" in handler_source
    assert "[candidate.to_state() for candidate in candidates]" in handler_source
    assert "search_geo_nearby_prompt" in handler_source

    receive_geo_source = handler_source.split(
        "async def receive_geo",
        1,
    )[1].split(
        "@search_router.callback_query(F.data.startswith(\"search_geo_place:\"))",
        1,
    )[0]

    assert ".reverse_place(" not in receive_geo_source
    assert "candidate_state = [candidate.to_state()]" not in receive_geo_source
def test_search_results_have_details_and_contact_actions_without_skipping_disclaimer():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "search_details_btn" in source
    assert "callback_data=f\"search_result:{index}\"" in source
    assert "callback_data=f\"search_result_contact:{index}\"" in source
    assert "async def contact_from_result" in source
    assert "await contact_start(callback, state)" in source

    contact_from_result_source = source.split(
        "async def contact_from_result",
        1,
    )[1].split(
        "@search_router.callback_query(F.data.startswith(\"search_result:\"))",
        1,
    )[0]

    assert "create_contact_request" not in contact_from_result_source
    assert "contact_start(callback, state)" in contact_from_result_source


def test_search_public_card_has_legal_warning_and_no_technical_fields():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "search_legal_warning" in source
    assert "search_price_from" in source
    assert "search_profile_status" not in source
    assert "search_status_label" not in source

    card_source = source.split("def format_public_card", 1)[1].split(
        "async def show_filters",
        1,
    )[0]

    forbidden_fragments = [
        "card.specialist_id",
        "city_id",
        "latitude",
        "longitude",
        "pending_moderation",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in card_source

def test_radius_filtering_is_done_in_repository_not_python_service_loop():
    repo_source = open("database/repositories/search.py", encoding="utf-8").read()
    service_source = open("services/geo_search.py", encoding="utf-8").read()

    assert "def _distance_km_expression" in repo_source
    assert "async def search_within_radius" in repo_source
    assert "distance_expr <= radius_km" in repo_source
    assert "func.radians" in repo_source
    assert "func.asin" in repo_source
    assert "search_within_radius" in service_source

    radius_method_source = service_source.split(
        "async def search_by_radius",
        1,
    )[1]

    assert "calculate_distance_km(" not in radius_method_source
    assert "if not country_wide and distance > filters.normalized_radius_km" not in radius_method_source
    assert "get_current_locations_by_specialist_ids" not in radius_method_source

def test_search_geo_candidates_are_deduplicated_before_display():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "def dedupe_geo_candidate_states" in source
    assert "candidate_state = dedupe_geo_candidate_states(" in source
    assert "human_key" in source
    assert "display_name" in source

def test_search_handler_does_not_query_user_accounts_directly():
    source = open("handlers/search.py", encoding="utf-8").read()
    user_repo_source = open("database/repositories/user.py", encoding="utf-8").read()

    assert "select(UserAccount)" not in source
    assert "UserAccount.user_id" not in source
    assert "get_telegram_account_by_user_id" in source

    assert "async def get_telegram_account_by_user_id" in user_repo_source
    assert "select(UserAccount)" in user_repo_source
    assert 'UserAccount.platform == "telegram"' in user_repo_source

def test_search_show_results_allows_explicit_without_location_mode():
    source = open("handlers/search.py", encoding="utf-8").read()
    service_source = open("services/geo_search.py", encoding="utf-8").read()

    assert "def search_location_keyboard" in source
    assert "async def show_filtered_results" in source
    assert "search_location_without" in source
    assert 'callback_data="search_location_without"' in source
    assert "async def choose_search_without_location" in source

    assert 'location_state="without"' in source
    assert 'data.get("location_state") == "without"' in source

    render_results_block = source.split(
        "async def render_results(",
        1,
    )[1].split(
        '@search_router.callback_query(F.data.in_({"M_FIND", "search_start"}))',
        1,
    )[0]

    assert "without_location = (" in render_results_block
    assert 'data.get("location_state") == "without"' in render_results_block
    assert 'or data.get("work_format") == "remote"' in render_results_block
    assert "if not city_id and not has_geo and not without_location:" in render_results_block
    assert "await service.search_without_location(" in render_results_block

    show_results_block = source.split(
        '@search_router.callback_query(F.data == "search_show_results")',
        1,
    )[1].split(
        '@search_router.callback_query(F.data.startswith("search_results_page:"))',
        1,
    )[0]

    assert "data.get(\"location_state\") == \"without\"" in show_results_block
    assert "if not has_location:" in show_results_block
    assert "await render_results(event=callback, state=state, page=0)" in show_results_block

    assert "async def search_without_location" in service_source
    assert "await self.repository.search_specialists(filters)" in service_source
    assert "distance_km=None" in service_source
def test_public_card_details_include_required_human_fields():
    handler_source = open("handlers/search.py", encoding="utf-8").read()
    service_source = open("services/geo_search.py", encoding="utf-8").read()
    repo_source = open("database/repositories/search.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "category_name: str | None = None" in service_source
    assert "profession_name: str | None = None" in service_source
    assert "work_format: str | None = None" in service_source
    assert "service_titles: list[str]" in service_source

    assert "get_category_name" in repo_source
    assert "get_profession_name" in repo_source
    assert "get_public_service_titles" in repo_source
    assert "SpecialistService.status == \"active\"" in repo_source

    card_source = handler_source.split("def format_public_card", 1)[1].split(
        "async def show_filters",
        1,
    )[0]

    assert "card.category_name" in card_source
    assert "card.profession_name" in card_source
    assert "work_format_label(card.work_format" in card_source
    assert "card.service_titles" in card_source
    assert "search_services_label" in card_source
    assert "search_legal_warning" in card_source

    assert "search_services_label" in texts_source

def test_search_result_cards_include_profession_city_distance_languages_without_handler_sql():
    handler_source = open("handlers/search.py", encoding="utf-8").read()
    service_source = open("services/geo_search.py", encoding="utf-8").read()

    assert "city_name: str | None = None" in service_source
    assert "profession_name: str | None = None" in service_source
    assert "languages: list[str] = field(default_factory=list)" in service_source
    assert "async def _enrich_search_results" in service_source
    assert "get_city_name" in service_source
    assert "get_profession_name" in service_source
    assert "get_language_codes_for_specialist" in service_source
    assert "interface_language: str = \"ru\"" in service_source

    assert "interface_language=language" in handler_source

    result_card_source = handler_source.split(
        "def format_specialist_result",
        1,
    )[1].split(
        "def format_public_card",
        1,
    )[0]

    assert "result.city_name" in result_card_source
    assert "result.profession_name" in result_card_source
    assert "result.languages" in result_card_source
    assert "location_parts" in result_card_source
    assert "search_filter_language_label" in result_card_source

    assert "session.execute" not in result_card_source
    assert "select(" not in result_card_source

def test_legacy_parallel_search_handlers_are_removed():
    legacy_paths = [
        "handlers/specialists/search_filters.py",
        "handlers/specialists/specialists_find_nearby.py",
        "handlers/specialists/specialists_filter_city.py",
        "handlers/specialists/specialists_filter_profession.py",
    ]

    for path in legacy_paths:
        assert not Path(path).exists()

    active_sources = [
        Path("handlers/search.py"),
        Path("services/geo_search.py"),
        Path("database/repositories/search.py"),
    ]
    combined_source = "\n".join(path.read_text(encoding="utf-8") for path in active_sources)

    assert "user_search_state" not in combined_source
def test_empty_results_increase_radius_is_dynamic_not_fixed_25():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "def next_empty_radius_suggestion" in source
    assert 'callback_data="search_empty_increase_radius"' in source
    assert 'F.data == "search_empty_increase_radius"' in source
    assert "radius_km=25, page=0" not in source
    assert "await render_results(event=callback, state=state, page=0)" in source
    assert "country_wide=True" in source
    assert "search_empty_increase_radius_to" in open("ui/texts.py", encoding="utf-8").read()
    assert "search_empty_increase_radius_country" in open("ui/texts.py", encoding="utf-8").read()

async def test_city_search_includes_whole_country_specialist(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        repository = SpecialistRepository(db_session)

        await repository.update_specialist_profile_fields(
            specialist_id=specialist.id,
            user_id=user_id,
            country_id=refs["country_id"],
            city_id=None,
            latitude=None,
            longitude=None,
            clear_city=True,
            clear_coordinates=True,
            service_radius_km=0,
        )

        search_service = GeoSearchService(
            SpecialistSearchRepository(db_session)
        )

        matching_country_results = await search_service.search_by_city(
            city_id=refs["city_id"],
            country_id=refs["country_id"],
            category_id=refs["category_id"],
            limit=10,
            offset=0,
        )

        city_only_results = await search_service.search_by_city(
            city_id=refs["city_id"],
            country_id=None,
            category_id=refs["category_id"],
            limit=10,
            offset=0,
        )

        matching_country_ids = {
            item.specialist.id
            for item in matching_country_results
        }
        city_only_ids = {
            item.specialist.id
            for item in city_only_results
        }

        assert specialist.id in matching_country_ids
        assert specialist.id not in city_only_ids

    finally:
        await cleanup_test_user(db_session, platform_user_id)
        await cleanup_legal_documents(db_session, tenant_id)
def test_search_start_logs_search_opened_event():
    source = open("handlers/search.py", encoding="utf-8").read()

    start_search_block = source.split(
        '@search_router.callback_query(F.data.in_({"M_FIND", "search_start"}))',
        1,
    )[1].split(
        '@search_router.callback_query(F.data == "search_filters")',
        1,
    )[0]

    assert 'event_type="search_opened"' in start_search_block
    assert 'entity_type="search"' in start_search_block
    assert '"source": callback.data' in start_search_block
    assert "await session.commit()" in start_search_block
def test_public_search_and_card_do_not_require_legal_gate_or_registration():
    source = open("handlers/search.py", encoding="utf-8").read()

    start_search_block = source.split(
        '@search_router.callback_query(F.data.in_({"M_FIND", "search_start"}))',
        1,
    )[1].split(
        '@search_router.callback_query(F.data == "search_filters")',
        1,
    )[0]

    assert "LegalService" not in start_search_block
    assert "LegalRepository" not in start_search_block
    assert "get_missing_specialist_consents" not in start_search_block
    assert "has_required_specialist_consents" not in start_search_block
    assert "legal_start_required" not in start_search_block
    assert "billing_start_required" not in start_search_block
    assert "await state.clear()" in start_search_block
    assert "await show_filters(callback, state)" in start_search_block

    card_block = source.split(
        '@search_router.callback_query(F.data.startswith("search_result:"))',
        1,
    )[1].split(
        '@search_router.callback_query(F.data == "search_portfolio_pending")',
        1,
    )[0]

    assert "LegalService" not in card_block
    assert "LegalRepository" not in card_block
    assert "get_missing_specialist_consents" not in card_block
    assert "has_required_specialist_consents" not in card_block
    assert "legal_start_required" not in card_block
    assert "billing_start_required" not in card_block
    assert "await store_post_auth_action" not in card_block
    assert "await callback.answer(t(\"billing_start_required\"" not in card_block
    assert "get_public_card" in card_block
    assert "format_public_card(card, language)" in card_block

def test_public_card_formatter_does_not_expose_raw_ids():
    source = open("handlers/search.py", encoding="utf-8").read()

    card_formatter = source.split(
        "def format_public_card",
        1,
    )[1].split(
        "async def show_filters",
        1,
    )[0]

    assert "card.specialist_id" not in card_formatter
    assert ".id}" not in card_formatter
    assert "UUID" not in card_formatter
    assert "search_legal_warning" in card_formatter
    assert "card.display_name" in card_formatter
    assert "card.short_description" in card_formatter

def test_auth_required_actions_are_restored_after_start():
    search_source = open("handlers/search.py", encoding="utf-8").read()
    start_source = open("handlers/start.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "async def store_post_auth_action" in search_source
    assert "async def resume_post_auth_action" in search_source

    assert 'post_auth_action=action' in search_source
    assert 'post_auth_action=None' in search_source

    assert 'action="contact"' in search_source
    assert 'action="favorite"' in search_source
    assert 'post_auth_action="report"' in search_source

    assert "await resume_post_auth_action(" in start_source

    assert "auth_required_start" in texts_source
    assert "auth_action_restored" in texts_source

def test_search_advanced_filters_keyboard_contains_full_tz10_c7_filters():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "def search_advanced_filters_keyboard" in source
    assert '@search_router.callback_query(F.data == "search_advanced_filters")' in source

    advanced_keyboard_block = source.split(
        "def search_advanced_filters_keyboard",
        1,
    )[1].split(
        "def search_location_keyboard",
        1,
    )[0]

    assert "search_filter_radius" in advanced_keyboard_block
    assert "search_filter_work_format" in advanced_keyboard_block
    assert "search_filter_language" in advanced_keyboard_block
    assert "search_filter_price" in advanced_keyboard_block
    assert "search_filter_sort" in advanced_keyboard_block
    assert "search_apply_filters" in advanced_keyboard_block
    assert "search_reset_filters" in advanced_keyboard_block
    assert "search_back_to_filters" in advanced_keyboard_block
    assert "search_menu" in advanced_keyboard_block

    assert "async def log_search_filters_changed" in source
    assert 'event_type="filters_changed"' in source
    assert 'filter_name="radius"' in source
    assert 'filter_name="work_format"' in source
    assert 'filter_name="language"' in source
    assert 'filter_name="price"' in source
    assert 'filter_name="sort"' in source
    assert 'filter_name="reset"' in source
def test_search_categories_use_tz10_page_size_eight():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "CATEGORY_PAGE_SIZE = 8" in source
    assert "page_size: int = PER_PAGE" in source

    open_category_block = source.split(
        '@search_router.callback_query(F.data == "search_filter_category")',
        1,
    )[1].split(
        '@search_router.callback_query(F.data.startswith("search_categories_page:"))',
        1,
    )[0]

    paginate_category_block = source.split(
        '@search_router.callback_query(F.data.startswith("search_categories_page:"))',
        1,
    )[1].split(
        '@search_router.callback_query(F.data.startswith("search_category:"))',
        1,
    )[0]

    assert "page_size=CATEGORY_PAGE_SIZE" in open_category_block
    assert "page_size=CATEGORY_PAGE_SIZE" in paginate_category_block

def test_search_category_selection_logs_category_selected_event():
    source = open("handlers/search.py", encoding="utf-8").read()

    choose_category_block = source.split(
        '@search_router.callback_query(F.data.startswith("search_category:"))',
        1,
    )[1].split(
        '@search_router.callback_query(F.data == "search_filter_profession")',
        1,
    )[0]

    assert 'event_type="category_selected"' in choose_category_block
    assert 'entity_type="specialist_category"' in choose_category_block
    assert '"category_name": item_name(category, language)' in choose_category_block
    assert "await session.commit()" in choose_category_block

def test_search_profession_selection_validates_category_and_logs_event():
    source = open("handlers/search.py", encoding="utf-8").read()

    choose_profession_block = source.split(
        '@search_router.callback_query(F.data.startswith("search_profession:"))',
        1,
    )[1].split(
        '@search_router.callback_query(F.data == "search_filter_location")',
        1,
    )[0]

    assert 'category_id = UUID(data["category_id"]) if data.get("category_id") else None' in choose_profession_block
    assert "profession.category_id != category_id" in choose_profession_block
    assert 'event_type="profession_selected"' in choose_profession_block
    assert 'entity_type="profession"' in choose_profession_block
    assert '"profession_name": item_name(profession, language)' in choose_profession_block
    assert '"category_id": str(category_id) if category_id else None' in choose_profession_block
    assert "await session.commit()" in choose_profession_block

def test_search_results_screen_matches_tz10_c8_requirements():
    source = open("handlers/search.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "PER_PAGE = 5" in source

    assert "def format_results_header" in source
    assert "search_results_header" in source
    assert "search_results_range" in source
    assert "profession=profession" in source
    assert "location=location" in source
    assert "radius=radius" in source
    assert "found=found" in source

    assert "async def log_results_viewed" in source
    assert 'event_type="results_viewed"' in source
    assert '"page": page' in source
    assert '"visible_count": visible_count' in source
    assert '"has_next": has_next' in source

    results_keyboard_block = source.split(
        "def results_keyboard",
        1,
    )[1].split(
        "def card_keyboard",
        1,
    )[0]

    assert 'callback_data=f"search_result:{index}"' in results_keyboard_block
    assert 'callback_data=f"search_results_page:{page - 1}"' in results_keyboard_block
    assert 'callback_data=f"search_results_page:{page + 1}"' in results_keyboard_block
    assert 'callback_data="search_filters"' in results_keyboard_block
    assert 'callback_data="search_menu"' in results_keyboard_block

    assert "Профессия: {profession}" in texts_source
    assert "Локация: {location}" in texts_source
    assert "Радиус: {radius}" in texts_source
    assert "Найдено: {found}" in texts_source
    assert "Показаны: {range}" in texts_source

def test_search_result_cards_match_tz10_c9_requirements():
    source = open("handlers/search.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    result_card_source = source.split(
        "def format_specialist_result",
        1,
    )[1].split(
        "def format_public_card",
        1,
    )[0]

    assert "specialist.display_name" in result_card_source
    assert "result.profession_name" in result_card_source
    assert "result.city_name" in result_card_source
    assert "result.distance_km" in result_card_source
    assert "search_rating" in result_card_source
    assert "search_no_reviews" in result_card_source
    assert "search_price_not_set" in result_card_source
    assert "result.languages" in result_card_source
    assert "work_format_label" in result_card_source
    assert "search_verified_label" not in result_card_source

    assert "if specialist.price_from and specialist.price_to" in result_card_source
    assert "elif specialist.price_from" in result_card_source
    assert "0 EUR" not in result_card_source
    assert "0.0" not in result_card_source

    results_keyboard_block = source.split(
        "def results_keyboard",
        1,
    )[1].split(
        "def card_keyboard",
        1,
    )[0]

    assert 'callback_data=f"search_result:{index}"' in results_keyboard_block
    assert 'callback_data=f"search_result_contact:{index}"' in results_keyboard_block
    assert 'callback_data=f"search_result_favorite:{index}"' in results_keyboard_block
    assert 'callback_data=f"search_result_report:{index}"' in results_keyboard_block

    assert "async def favorite_from_result" in source
    assert "await favorite_pending(callback, state)" in source
    assert "async def report_from_result" in source
    assert "await report_pending(callback, state)" in source

    assert 'event_type="card_viewed"' in source
    assert '"source": "search_results"' in source

    assert "search_no_reviews" in texts_source

def test_specialist_public_profile_matches_tz10_c10_requirements():
    source = open("handlers/search.py", encoding="utf-8").read()
    service_source = open("services/geo_search.py", encoding="utf-8").read()

    assert "def format_public_card" in source

    profile_formatter = source.split(
        "def format_public_card",
        1,
    )[1].split(
        "async def show_filters",
        1,
    )[0]

    assert "card.display_name" in profile_formatter
    assert "search_verified_label" not in profile_formatter
    assert "card.category_name" in profile_formatter
    assert "card.profession_name" in profile_formatter
    assert "card.city_name" in profile_formatter
    assert "card.short_description" in profile_formatter
    assert "card.service_titles" in profile_formatter
    assert "search_rating" in profile_formatter
    assert "search_legal_warning" in profile_formatter
    assert "search_price_not_set" in profile_formatter

    card_keyboard_block = source.split(
        "def card_keyboard",
        1,
    )[1].split(
        "def public_portfolio_keyboard",
        1,
    )[0]

    assert 'callback_data="search_contact_pending"' in card_keyboard_block
    assert 'callback_data="search_favorite_pending"' in card_keyboard_block
    assert 'callback_data="search_reviews_pending"' in card_keyboard_block
    assert 'callback_data="search_portfolio_pending"' in card_keyboard_block
    assert 'callback_data="search_report_pending"' in card_keyboard_block
    assert 'callback_data=f"search_results_page:{results_page}"' in card_keyboard_block

    assert "async def show_selected_specialist_reviews" in source
    assert "async def show_selected_specialist_portfolio" in source
    assert "render_public_portfolio" in source
    assert "public_portfolio_keyboard" in source

    show_card_block = source.split(
        "async def show_specialist_card",
        1,
    )[1].split(
        '@search_router.callback_query(F.data == "search_portfolio_pending")',
        1,
    )[0]

    assert "render_public_portfolio(" not in show_card_block
    assert 'event_type="profile_viewed"' in show_card_block

    assert "reviews_count: int = 0" in service_source
def test_favorites_c16_list_has_pagination_and_remove_flow():
    repo_source = open("database/repositories/favorites.py", encoding="utf-8").read()
    billing_source = open("handlers/billing.py", encoding="utf-8").read()

    assert "offset: int = 0" in repo_source
    assert ".offset(offset)" in repo_source
    assert "FAVORITES_PAGE_SIZE = 10" in billing_source
    assert 'F.data.startswith("CAB_FAVORITES:")' in billing_source
    assert "cabinet_favorites_page" in billing_source
    assert 'callback_data=f"CAB_FAVORITES:{page + 1}"' in billing_source
    assert 'callback_data=f"CAB_FAVORITES:{page - 1}"' in billing_source
    assert "CAB_FAV_REMOVE" in billing_source
    assert "favorites_opened" in billing_source
    assert "favorite_viewed" in billing_source
    assert "favorite_removed" in billing_source
    assert "EventRepository(session).create_event" in billing_source

def test_remote_work_format_search_does_not_require_location():
    source = open("handlers/search.py", encoding="utf-8").read()

    choose_work_block = source.split(
        'async def choose_work_format_filter',
        1,
    )[1].split(
        '@search_router.callback_query(F.data == "search_filter_language")',
        1,
    )[0]

    assert 'if work_format == "remote":' in choose_work_block
    assert 'location_state="without"' in choose_work_block
    assert 'city_id=None' in choose_work_block
    assert 'country_id=None' in choose_work_block
    assert 'latitude=None' in choose_work_block
    assert 'longitude=None' in choose_work_block

    show_results_block = source.split(
        '@search_router.callback_query(F.data == "search_show_results")',
        1,
    )[1].split(
        '@search_router.callback_query(F.data.startswith("search_results_page:"))',
        1,
    )[0]

    assert 'or data.get("work_format") == "remote"' in show_results_block

    render_results_block = source.split(
        "async def render_results(",
        1,
    )[1].split(
        '@search_router.callback_query(F.data.in_({"M_FIND", "search_start"}))',
        1,
    )[0]

    assert 'or data.get("work_format") == "remote"' in render_results_block
    assert "await service.search_without_location(" in render_results_block

def test_remote_work_format_search_ignores_geo_and_shows_remote_label():
    source = open("handlers/search.py", encoding="utf-8").read()

    render_results_block = source.split(
        "async def render_results(",
        1,
    )[1].split(
        '@search_router.callback_query(F.data.in_({"M_FIND", "search_start"}))',
        1,
    )[0]

    assert 'remote_only = work_format == "remote"' in render_results_block
    assert "if remote_only:" in render_results_block

    remote_branch = render_results_block.split(
        "if remote_only:",
        1,
    )[1].split(
        "elif has_geo:",
        1,
    )[0]

    assert "await service.search_without_location(" in remote_branch
    assert "work_format=work_format" in remote_branch

    result_card_block = source.split(
        "def format_specialist_result",
        1,
    )[1].split(
        "def format_public_card",
        1,
    )[0]

    assert 'is_remote = getattr(specialist, "work_format", None) == "remote"' in result_card_block
    assert 'work_format_label("remote", language)' in result_card_block
    assert "distance = None if is_remote else" in result_card_block

    public_card_block = source.split(
        "def format_public_card",
        1,
    )[1].split(
        "async def show_filters",
        1,
    )[0]

    assert 'is_remote = card.work_format == "remote"' in public_card_block
    assert 'work_format_label("remote", language)' in public_card_block
    assert 'distance = "" if is_remote else' in public_card_block

def test_acceptance_no_zero_rating_when_reviews_are_missing():
    search_source = open("handlers/search.py", encoding="utf-8").read()
    billing_source = open("handlers/billing.py", encoding="utf-8").read()

    public_reviews_block = search_source.split(
        "def format_public_reviews",
        1,
    )[1].split(
        "def public_reviews_keyboard",
        1,
    )[0]

    specialist_reviews_block = billing_source.split(
        "def format_specialist_reviews_cabinet",
        1,
    )[1].split(
        "def specialist_reviews_keyboard",
        1,
    )[0]

    specialist_profile_block = billing_source.split(
        "def format_specialist_profile_text",
        1,
    )[1].split(
        "def specialist_profile_status_block",
        1,
    )[0]

    favorite_card_block = billing_source.split(
        "def format_favorite_card",
        1,
    )[1].split(
        "def specialist_profile_keyboard",
        1,
    )[0]

    for block in [
        public_reviews_block,
        specialist_reviews_block,
        specialist_profile_block,
        favorite_card_block,
    ]:
        assert '"0.0"' not in block
        assert "0.00" not in block
        assert "search_no_reviews" in block

def test_search_filters_default_page_size_is_beta_card_page_size():
    filters = SpecialistSearchFilters()

    assert filters.page_size == 5
    assert filters.normalized_page_size == 5
    assert filters.query_limit == 5

def test_acceptance_ranking_uses_verified_before_tie_breaks():
    repository_source = open("database/repositories/search.py", encoding="utf-8").read()
    service_source = open("services/geo_search.py", encoding="utf-8").read()

    assert (
        "Specialist.rating.desc(),\n"
        "                Specialist.is_verified.desc(),\n"
        "                Specialist.reviews_count.desc(),\n"
        "                Specialist.updated_at.desc(),\n"
        "                Specialist.id.asc(),"
    ) in repository_source

    assert (
        "-float(item.specialist.rating or 0),\n"
        "                    -int(bool(item.specialist.is_verified)),\n"
        "                    -int(item.specialist.reviews_count or 0),\n"
        "                    -self._activity_timestamp(item.specialist),\n"
        "                    str(item.specialist.id),"
    ) in service_source