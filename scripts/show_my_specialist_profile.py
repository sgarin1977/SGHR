import asyncio
from sqlalchemy import select
from database.models import (
    User,
    UserAccount,
    Specialist,
    SpecialistLocation,
    SpecialistLanguage,
    SpecialistService,
    EventLog,
)
from database.session import get_session


TELEGRAM_ID = "755072549"


async def main():
    async with get_session() as session:
        account_result = await session.execute(
            select(UserAccount).where(
                UserAccount.platform == "telegram",
                UserAccount.platform_user_id == TELEGRAM_ID,
            )
        )
        account = account_result.scalar_one_or_none()

        if not account:
            print("UserAccount не знайдено")
            return

        user = await session.get(User, account.user_id)
        print("\nUSER")
        print("id:", user.id)
        print("tenant_id:", user.tenant_id)
        print("language:", user.language_code)
        print("active_role:", user.active_role)
        print("status:", user.status)

        specialist_result = await session.execute(
            select(Specialist).where(Specialist.user_id == user.id)
        )
        specialist = specialist_result.scalar_one_or_none()

        if not specialist:
            print("\nSpecialist profile не знайдено")
            return

        print("\nSPECIALIST")
        print("id:", specialist.id)
        print("status:", specialist.status)
        print("display_name:", specialist.display_name)
        print("short_description:", specialist.short_description)
        print("price_from:", specialist.price_from)
        print("price_to:", specialist.price_to)
        print("currency:", specialist.currency)
        print("price_unit:", specialist.price_unit)
        print("category_id:", specialist.category_id)
        print("profession_id:", specialist.profession_id)
        print("country_id:", specialist.country_id)
        print("city_id:", specialist.city_id)
        print("metadata:", specialist.extra_metadata)

        location_result = await session.execute(
            select(SpecialistLocation).where(
                SpecialistLocation.specialist_id == specialist.id
            )
        )
        locations = location_result.scalars().all()

        print("\nLOCATIONS")
        for item in locations:
            print(
                item.id,
                "city_id:", item.city_id,
                "lat:", item.latitude,
                "lon:", item.longitude,
                "current:", item.is_current,
                "visibility:", item.visibility_level,
            )

        language_result = await session.execute(
            select(SpecialistLanguage).where(
                SpecialistLanguage.specialist_id == specialist.id
            )
        )
        languages = language_result.scalars().all()

        print("\nLANGUAGES")
        for item in languages:
            print(item.__dict__)

        service_result = await session.execute(
            select(SpecialistService).where(
                SpecialistService.specialist_id == specialist.id
            )
        )
        services = service_result.scalars().all()

        print("\nSERVICES")
        for item in services:
            print("title:", item.title)
            print("description:", item.description)
            print("price:", item.price_from, item.price_to, item.currency)
            print("status:", item.status)

        event_result = await session.execute(
            select(EventLog)
            .where(EventLog.user_id == user.id)
            .order_by(EventLog.created_at.desc())
            .limit(20)
        )
        events = event_result.scalars().all()

        print("\nEVENTS")
        for item in events:
            print(item.created_at, item.event_type, item.entity_type, item.entity_id)


if __name__ == "__main__":
    asyncio.run(main())