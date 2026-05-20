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
)
from database.repositories.search import SpecialistSearchRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from services.geo_search import GeoSearchService
from services.specialist import SpecialistRegistrationData
from services.specialist import SpecialistService as SpecialistRegistrationService
from utils.geo import haversine_distance_km, is_within_radius_km


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

    return category, profession, country, city


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

    return platform_user_id, user


async def create_active_search_specialist(session):
    platform_user_id, user = await create_test_user(session)
    category, profession, country, city = await get_reference_data(session)

    service = SpecialistRegistrationService(SpecialistRepository(session))
    specialist = await service.create_pending_profile(
        SpecialistRegistrationData(
            tenant_id=user.tenant_id,
            user_id=user.id,
            category_id=category.id,
            profession_id=profession.id,
            country_id=country.id,
            city_id=city.id,
            display_name="Searchable Beta Specialist",
            short_description="Experienced searchable beta specialist for geo tests.",
            full_description="Detailed searchable beta specialist profile.",
            price_from=40,
            price_to=80,
            currency="EUR",
            price_unit="service",
            latitude=city.latitude,
            longitude=city.longitude,
            service_radius_km=25,
            languages=["ru", "en"],
            service_title="Searchable beta service",
            service_description="Service created by beta 0.5 search test.",
            contact_text="Contact inside SGHR beta chat",
        )
    )

    specialist.status = "active"
    await session.commit()

    return platform_user_id, user, specialist, category, profession, country, city


async def test_search_specialists_by_city_and_category(db_session):
    platform_user_id, user, specialist, category, profession, country, city = (
        await create_active_search_specialist(db_session)
    )

    try:
        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_city(
            city_id=city.id,
            category_id=category.id,
            limit=10,
            offset=0,
        )

        result_ids = {item.specialist.id for item in results}

        assert specialist.id in result_ids
        assert all(item.specialist.status == "active" for item in results)
        assert all(item.specialist.city_id == city.id for item in results)
        assert all(item.specialist.category_id == category.id for item in results)

    finally:
        await cleanup_test_user(db_session, platform_user_id)


async def test_search_specialists_by_radius(db_session):
    platform_user_id, user, specialist, category, profession, country, city = (
        await create_active_search_specialist(db_session)
    )

    try:
        search_service = GeoSearchService(SpecialistSearchRepository(db_session))

        results = await search_service.search_by_radius(
            latitude=float(city.latitude),
            longitude=float(city.longitude),
            radius_km=5,
            category_id=category.id,
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