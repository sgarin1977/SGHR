import uuid

import pytest
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
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from services.specialist import (
    SpecialistRegistrationData,
    SpecialistRegistrationError,
    SpecialistService as SpecialistRegistrationService,
)


BETA_CONTACT_NOTE = "Contact inside SGHR beta chat"


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
        select(City).where(City.is_active.is_(True)).limit(1)
    )
    city = city_result.scalar_one_or_none()
    assert city is not None, "No active city found. Seed beta cities first."

    country = await session.get(Country, city.country_id)
    assert country is not None

    return category, profession, country, city


async def create_test_user(session):
    platform_user_id = f"test-specialist-{uuid.uuid4()}"

    user_repo = UserRepository(session)
    user_id = await user_repo.create_telegram_user_core(
        platform_user_id=platform_user_id,
        username="test_specialist",
        first_name="Test",
        last_name="Specialist",
        language_code="ru",
        role="client",
    )

    user = await session.get(User, user_id)
    assert user is not None

    return platform_user_id, user


async def test_create_specialist_profile_pending_moderation(db_session):
    platform_user_id, user = await create_test_user(db_session)
    category, profession, country, city = await get_reference_data(db_session)

    try:
        service = SpecialistRegistrationService(SpecialistRepository(db_session))

        specialist = await service.create_pending_profile(
            SpecialistRegistrationData(
                tenant_id=user.tenant_id,
                user_id=user.id,
                category_id=category.id,
                profession_id=profession.id,
                country_id=country.id,
                city_id=city.id,
                display_name="Test Specialist",
                short_description="Experienced specialist for beta testing.",
                full_description="Detailed beta test specialist profile.",
                price_from=50,
                price_to=100,
                currency="EUR",
                price_unit="hour",
                latitude=city.latitude,
                longitude=city.longitude,
                service_radius_km=25,
                languages=["ru", "en"],
                service_title="Beta service",
                service_description="Service created by beta 0.4 test.",
                contact_text=BETA_CONTACT_NOTE,
            )
        )

        assert specialist.id is not None
        assert specialist.tenant_id == user.tenant_id
        assert specialist.user_id == user.id
        assert specialist.category_id == category.id
        assert specialist.profession_id == profession.id
        assert specialist.country_id == country.id
        assert specialist.city_id == city.id
        assert specialist.status == "pending_moderation"
        assert specialist.is_verified is False
        assert specialist.is_premium is False
        assert specialist.extra_metadata["contact_text"] == BETA_CONTACT_NOTE

        location_result = await db_session.execute(
            select(SpecialistLocation).where(SpecialistLocation.specialist_id == specialist.id)
        )
        location = location_result.scalar_one_or_none()
        assert location is not None
        assert location.city_id == city.id
        assert location.is_current is True

        languages_result = await db_session.execute(
            select(SpecialistLanguage).where(SpecialistLanguage.specialist_id == specialist.id)
        )
        languages = languages_result.scalars().all()
        assert {item.language_code for item in languages} == {"ru", "en"}

        service_result = await db_session.execute(
            select(SpecialistService).where(SpecialistService.specialist_id == specialist.id)
        )
        specialist_service = service_result.scalar_one_or_none()
        assert specialist_service is not None
        assert specialist_service.title == "Beta service"
        assert specialist_service.status == "active"

    finally:
        await cleanup_test_user(db_session, platform_user_id)


async def test_create_specialist_profile_rejects_duplicate(db_session):
    platform_user_id, user = await create_test_user(db_session)
    category, profession, country, city = await get_reference_data(db_session)

    data = SpecialistRegistrationData(
        tenant_id=user.tenant_id,
        user_id=user.id,
        category_id=category.id,
        profession_id=profession.id,
        country_id=country.id,
        city_id=city.id,
        display_name="Duplicate Specialist",
        short_description="Experienced specialist for duplicate beta test.",
        currency="EUR",
        languages=["ru"],
        service_title="Duplicate service",
        contact_text=BETA_CONTACT_NOTE,
    )

    try:
        service = SpecialistRegistrationService(SpecialistRepository(db_session))

        first = await service.create_pending_profile(data)
        assert first.status == "pending_moderation"
        assert first.extra_metadata["contact_text"] == BETA_CONTACT_NOTE

        with pytest.raises(SpecialistRegistrationError):
            await service.create_pending_profile(data)

    finally:
        await cleanup_test_user(db_session, platform_user_id)