import asyncio
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from database.repositories.search import SpecialistSearchRepository
from database.session import async_session
from services.geo_search import GeoSearchService


SPECIALIST_ID = "845bc218-5467-465c-b5cf-5a5a19952fea"


async def main():
    specialist_id = uuid.UUID(SPECIALIST_ID)

    async with async_session() as session:
        specialist = await session.get(
            __import__("database.models", fromlist=["Specialist"]).Specialist,
            specialist_id,
        )

        if not specialist:
            raise SystemExit(f"FAIL: specialist not found: {specialist_id}")

        print(f"specialist_id={specialist.id}")
        print(f"status={specialist.status}")
        print(f"display_name={specialist.display_name}")
        print(f"city_id={specialist.city_id}")
        print(f"category_id={specialist.category_id}")
        print(f"latitude={specialist.latitude}")
        print(f"longitude={specialist.longitude}")

        if specialist.status != "active":
            raise SystemExit(
                f"FAIL: specialist must be active for search smoke, got {specialist.status}"
            )

        if not specialist.city_id:
            raise SystemExit("FAIL: specialist city_id is empty")

        if not specialist.category_id:
            raise SystemExit("FAIL: specialist category_id is empty")

        search_service = GeoSearchService(SpecialistSearchRepository(session))

        city_results = await search_service.search_by_city(
            city_id=specialist.city_id,
            category_id=specialist.category_id,
            limit=10,
            offset=0,
        )

        city_result_ids = {item.specialist.id for item in city_results}

        print(f"city_results_count={len(city_results)}")

        if specialist.id not in city_result_ids:
            raise SystemExit("FAIL: specialist was not found by city/category search")

        if specialist.latitude is None or specialist.longitude is None:
            raise SystemExit("FAIL: specialist has no coordinates for radius search")

        radius_results = await search_service.search_by_radius(
            latitude=float(specialist.latitude),
            longitude=float(specialist.longitude),
            radius_km=5,
            category_id=specialist.category_id,
            limit=10,
            offset=0,
        )

        radius_result_by_id = {
            item.specialist.id: item
            for item in radius_results
        }

        print(f"radius_results_count={len(radius_results)}")

        if specialist.id not in radius_result_by_id:
            raise SystemExit("FAIL: specialist was not found by radius/category search")

        distance = radius_result_by_id[specialist.id].distance_km
        print(f"distance_km={distance}")

        if distance is None or distance > 5:
            raise SystemExit(f"FAIL: expected distance <= 5 km, got {distance}")

        print("OK: beta 0.5 geo search smoke passed")


if __name__ == "__main__":
    asyncio.run(main())