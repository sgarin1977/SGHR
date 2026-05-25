import asyncio
import os
import sys
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parent.parent))

from database.repositories.geo_repository import GeoRepository
from database.session import async_session
from services.geo_provider import NominatimGeoProvider
from services.geo_service import GeoService


QUERY = os.getenv("GEO_SMOKE_QUERY", "Porto, Portugal")
LATITUDE = float(os.getenv("GEO_SMOKE_LATITUDE", "41.1579"))
LONGITUDE = float(os.getenv("GEO_SMOKE_LONGITUDE", "-8.6291"))

async def main():
    async with async_session() as session:
       

        service = GeoService(
            GeoRepository(session),
            provider=NominatimGeoProvider(),
        )

        candidates = await service.search_places(
            query=QUERY,
            language="en",
            limit=5,
        )

        print(f"search_query={QUERY}")
        print(f"search_results_count={len(candidates)}")

        if not candidates:
            raise SystemExit("FAIL: Nominatim search returned no candidates")

        selected = candidates[0]
        print(f"selected_display_name={selected.display_name}")
        print(f"selected_country_code={selected.country_code}")
        print(f"selected_latitude={selected.latitude}")
        print(f"selected_longitude={selected.longitude}")

        saved = await service.confirm_place(selected)
        

        print(f"country_id={saved.country_id}")
        print(f"city_id={saved.city_id}")
        print(f"latitude={saved.latitude}")
        print(f"longitude={saved.longitude}")

        second = await service.confirm_place(selected.to_state())

        if second.country_id != saved.country_id:
            raise SystemExit("FAIL: repeated confirm created different country")

        if second.city_id != saved.city_id:
            raise SystemExit("FAIL: repeated confirm created different city")

        reverse_candidate = await service.reverse_place(
            latitude=LATITUDE,
            longitude=LONGITUDE,
            language="en",
        )

        print(f"reverse_latitude={LATITUDE}")
        print(f"reverse_longitude={LONGITUDE}")
        print(f"reverse_display_name={reverse_candidate.display_name if reverse_candidate else None}")

        if not reverse_candidate:
            raise SystemExit("FAIL: Nominatim reverse returned no candidate")

        reverse_saved = await service.confirm_place(reverse_candidate)
        

        if not reverse_saved.city_id:
            raise SystemExit("FAIL: reverse confirm did not return city_id")

        print("OK: geo provider smoke passed")


if __name__ == "__main__":
    asyncio.run(main())