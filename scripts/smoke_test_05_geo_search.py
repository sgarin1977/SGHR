import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from database.models import Specialist
from database.repositories.search import SpecialistSearchRepository
from database.session import async_session
from services.geo_search import GeoSearchService


def resolve_specialist_id() -> uuid.UUID:
    raw_value = None

    if len(sys.argv) > 1:
        raw_value = sys.argv[1]

    if not raw_value:
        raw_value = os.getenv("SPECIALIST_ID")

    if not raw_value:
        raise SystemExit(
            "FAIL: provide specialist id via SPECIALIST_ID env or first argument"
        )

    try:
        return uuid.UUID(raw_value)
    except ValueError as exc:
        raise SystemExit(f"FAIL: invalid specialist id: {raw_value}") from exc


async def main():
    specialist_id = resolve_specialist_id()

    async with async_session() as session:
        specialist = await session.get(Specialist, specialist_id)

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

        filtered_results = await search_service.search_by_city(
            city_id=specialist.city_id,
            category_id=specialist.category_id,
            price_min=None,
            price_max=float(specialist.price_from) if specialist.price_from is not None else None,
            language_code="en",
            work_format=specialist.work_format,
            limit=10,
            offset=0,
        )

        filtered_result_ids = {item.specialist.id for item in filtered_results}

        print(f"filtered_results_count={len(filtered_results)}")

        if specialist.id not in filtered_result_ids:
            raise SystemExit("FAIL: specialist was not found by language/price/work_format filters")

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

        card = await search_service.get_public_card(
            specialist_id=specialist.id,
            distance_km=distance,
            log_event=False,
        )

        if not card:
            raise SystemExit("FAIL: public specialist card was not returned")

        card_data = card.__dict__
        forbidden_public_fields = {
            "contact_text",
            "metadata",
            "extra_metadata",
            "latitude",
            "longitude",
            "email",
            "phone",
            "username",
        }

        leaked_fields = forbidden_public_fields.intersection(card_data)
        if leaked_fields:
            raise SystemExit(f"FAIL: public card leaks fields: {sorted(leaked_fields)}")

        print("OK: beta 0.5 geo search smoke passed")


if __name__ == "__main__":
    asyncio.run(main())