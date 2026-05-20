import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parent.parent))

from database.models import (
    Specialist,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistService,
)
from database.session import async_session


PROFILE_ID = "845bc218-5467-465c-b5cf-5a5a19952fea"


async def main():
    specialist_id = uuid.UUID(PROFILE_ID)

    async with async_session() as session:
        specialist = await session.get(Specialist, specialist_id)

        if not specialist:
            raise SystemExit(f"FAIL: specialist profile not found: {specialist_id}")

        print(f"specialist_id={specialist.id}")
        print(f"user_id={specialist.user_id}")
        print(f"tenant_id={specialist.tenant_id}")
        print(f"status={specialist.status}")
        print(f"display_name={specialist.display_name}")
        print(f"city_id={specialist.city_id}")
        print(f"category_id={specialist.category_id}")
        print(f"profession_id={specialist.profession_id}")
        print(f"contact_text={specialist.extra_metadata.get('contact_text') if specialist.extra_metadata else None}")

        if specialist.status != "pending_moderation":
            raise SystemExit(f"FAIL: expected pending_moderation, got {specialist.status}")

        if not specialist.display_name:
            raise SystemExit("FAIL: display_name is empty")

        location_result = await session.execute(
            select(SpecialistLocation).where(
                SpecialistLocation.specialist_id == specialist_id,
                SpecialistLocation.is_current.is_(True),
            )
        )
        location = location_result.scalar_one_or_none()

        language_result = await session.execute(
            select(SpecialistLanguage).where(
                SpecialistLanguage.specialist_id == specialist_id
            )
        )
        languages = language_result.scalars().all()

        service_result = await session.execute(
            select(SpecialistService).where(
                SpecialistService.specialist_id == specialist_id
            )
        )
        service = service_result.scalar_one_or_none()

        print(f"has_current_location={location is not None}")
        print(f"languages={[item.language_code for item in languages]}")
        print(f"has_service={service is not None}")

        if location is None:
            raise SystemExit("FAIL: current specialist location not found")

        if not languages:
            raise SystemExit("FAIL: specialist languages not found")

        if service is None:
            raise SystemExit("FAIL: specialist service not found")

        print("OK: beta 0.4 specialist profile smoke passed")


if __name__ == "__main__":
    asyncio.run(main())