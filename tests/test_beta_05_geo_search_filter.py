import uuid

from sqlalchemy import delete, select

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
        "search_verified_toggle",
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
        "Только проверенные",
        "Все специалисты",
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
async def test_search_filters_price_language_verified_premium_work_format(db_session):
    platform_user_id, user_id, tenant_id, specialist, refs = (
        await create_active_search_specialist(db_session)
    )

    try:
        specialist.is_verified = True
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
        assert all(item.specialist.is_verified for item in results)
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

async def test_search_ranking_orders_verified_premium_rating_and_risk(db_session):
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
            limit=10,
            offset=0,
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

def test_search_handler_passes_all_filters_to_city_search():
    source = open("handlers/search.py", encoding="utf-8").read()

    city_branch = source.split('if mode == "city":', 1)[1].split(
        'elif mode == "geo":',
        1,
    )[0]

    required_fragments = [
        "price_min=price_min",
        "price_max=price_max",
        "language_code=language_code",
        "verified_only=verified_only",
        "premium_only=premium_only",
        "work_format=work_format",
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
    assert "callback_data=\"search_rating:any\"" in source
    assert "callback_data=\"search_rating:4\"" in source

    city_branch = source.split('if mode == "city":', 1)[1].split(
        'elif mode == "geo":',
        1,
    )[0]
    geo_branch = source.split('elif mode == "geo":', 1)[1].split(
        "else:",
        1,
    )[0]

    assert "rating_min=rating_min" in city_branch
    assert "rating_min=rating_min" in geo_branch

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
        "CB_SEARCH_VERIFIED_TOGGLE",
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
        "verified_bonus * 0.10",
        "premium_boost * 0.10",
        "freshness_score * 0.05",
        "- risk_penalty",
    ]

    handler_required = [
        "M_FIND",
        "SpecialistSearchFSM",
        "choosing_category",
        "choosing_profession",
        "choosing_mode",
        "choosing_filters",
        "viewing_results",
        "category_ids",
        "profession_ids",
        "city_ids",
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