from sqlalchemy import delete, select

from database.models import City, Country
from database.repositories.geo_repository import GeoRepository
from services.geo_provider import GeoPlaceCandidate
from services.geo_service import GeoService


FAKE_CITY_NAME = "SGHR Fake Geo City"
FAKE_COUNTRY_NAME = "SGHR Fake Geo Country"
FAKE_COUNTRY_CODE = "ZZ"


class FakeGeoProvider:
    async def search(self, *, query: str, language: str = "ru", limit: int = 5):
        return [
            GeoPlaceCandidate(
                name=FAKE_CITY_NAME,
                country_name=FAKE_COUNTRY_NAME,
                country_code=FAKE_COUNTRY_CODE,
                latitude=41.1579,
                longitude=-8.6291,
                display_name=f"{FAKE_CITY_NAME}, {FAKE_COUNTRY_NAME}",
                provider="fake",
                place_id="fake-sghr-place",
                osm_type="relation",
                osm_id="fake-sghr-osm",
                place_type="city",
            )
        ]

    async def reverse(self, *, latitude: float, longitude: float, language: str = "ru"):
        return GeoPlaceCandidate(
            name=FAKE_CITY_NAME,
            country_name=FAKE_COUNTRY_NAME,
            country_code=FAKE_COUNTRY_CODE,
            latitude=latitude,
            longitude=longitude,
            display_name=f"{FAKE_CITY_NAME}, {FAKE_COUNTRY_NAME}",
            provider="fake",
            place_id="fake-sghr-reverse-place",
            osm_type="relation",
            osm_id="fake-sghr-reverse-osm",
            place_type="city",
        )


async def cleanup_fake_geo(session):
    await session.rollback()

    city_result = await session.execute(
        select(City).where(City.name == FAKE_CITY_NAME)
    )
    cities = city_result.scalars().all()

    for city in cities:
        await session.execute(delete(City).where(City.id == city.id))

    country_result = await session.execute(
        select(Country).where(Country.code == FAKE_COUNTRY_CODE)
    )
    countries = country_result.scalars().all()

    for country in countries:
        city_result = await session.execute(
            select(City).where(City.country_id == country.id)
        )
        if not city_result.scalar_one_or_none():
            await session.execute(delete(Country).where(Country.id == country.id))

    await session.commit()


async def test_geo_service_search_returns_provider_candidates(db_session):
    service = GeoService(
        GeoRepository(db_session),
        provider=FakeGeoProvider(),
    )

    candidates = await service.search_places(
        query=FAKE_CITY_NAME,
        language="en",
        limit=5,
    )

    assert len(candidates) == 1
    assert candidates[0].name == FAKE_CITY_NAME
    assert candidates[0].country_code == FAKE_COUNTRY_CODE
    assert candidates[0].display_name == f"{FAKE_CITY_NAME}, {FAKE_COUNTRY_NAME}"


async def test_geo_service_reverse_returns_provider_candidate(db_session):
    service = GeoService(
        GeoRepository(db_session),
        provider=FakeGeoProvider(),
    )

    candidate = await service.reverse_place(
        latitude=41.1579,
        longitude=-8.6291,
        language="en",
    )

    assert candidate is not None
    assert candidate.name == FAKE_CITY_NAME
    assert candidate.country_code == FAKE_COUNTRY_CODE
    assert candidate.latitude == 41.1579
    assert candidate.longitude == -8.6291


async def test_geo_service_confirm_place_creates_country_city_and_is_idempotent(db_session):
    await cleanup_fake_geo(db_session)

    service = GeoService(
        GeoRepository(db_session),
        provider=FakeGeoProvider(),
    )

    candidates = await service.search_places(query=FAKE_CITY_NAME, language="en")
    candidate = candidates[0]

    try:
        first = await service.confirm_place(candidate)
        second = await service.confirm_place(candidate.to_state())

        assert first.country_id == second.country_id
        assert first.city_id == second.city_id
        assert first.city_name == FAKE_CITY_NAME
        assert first.country_code == FAKE_COUNTRY_CODE
        assert first.latitude == 41.1579
        assert first.longitude == -8.6291

        city = await db_session.get(City, first.city_id)
        country = await db_session.get(Country, first.country_id)

        assert city is not None
        assert country is not None
        assert city.extra_metadata["provider"] == "fake"
        assert city.extra_metadata["osm_id"] == "fake-sghr-osm"
        assert country.extra_metadata["provider"] == "fake"

    finally:
        await cleanup_fake_geo(db_session)


def test_geo_provider_contract_exists():
    source = open("services/geo_provider.py", encoding="utf-8").read()
    service_source = open("services/geo_service.py", encoding="utf-8").read()
    repo_source = open("database/repositories/geo_repository.py", encoding="utf-8").read()

    provider_required = [
        "class NominatimGeoProvider",
        "async def search",
        "async def reverse",
        "User-Agent",
        "city",
        "town",
        "village",
        "hamlet",
        "suburb",
        "county",
        "state_district",
    ]

    service_required = [
        "class GeoService",
        "search_places",
        "reverse_place",
        "confirm_place",
        "GeoPlaceCandidate.from_state",
    ]

    repo_required = [
        "class GeoRepository",
        "ensure_country",
        "ensure_city",
        "extra_metadata",
        "provider",
        "osm_id",
        "place_id",
    ]

    for fragment in provider_required:
        assert fragment in source

    for fragment in service_required:
        assert fragment in service_source

    for fragment in repo_required:
        assert fragment in repo_source

async def test_geo_service_nearby_places_uses_provider_nearby_candidates(db_session):
    class NearbyFakeProvider(FakeGeoProvider):
        async def search_nearby(
            self,
            *,
            latitude: float,
            longitude: float,
            language: str = "ru",
            limit: int = 4,
            radius_km: float = 25,
        ):
            return [
    GeoPlaceCandidate(
        name=FAKE_CITY_NAME,
        country_name=FAKE_COUNTRY_NAME,
        country_code=FAKE_COUNTRY_CODE,
        latitude=41.1579,
        longitude=-8.6291,
        display_name=f"{FAKE_CITY_NAME}, {FAKE_COUNTRY_NAME}",
        provider="fake",
        place_id="fake-place",
        osm_type="relation",
        osm_id="fake-osm",
        place_type="city",
    ),
    GeoPlaceCandidate(
        name="SGHR Nearby Geo City",
        country_name=FAKE_COUNTRY_NAME,
        country_code=FAKE_COUNTRY_CODE,
        latitude=41.2,
        longitude=-8.7,
        display_name="SGHR Nearby Geo City, SGHR Fake Geo Country",
        provider="fake",
        place_id="fake-nearby-place",
        osm_type="relation",
        osm_id="fake-nearby-osm",
        place_type="town",
    ),
]

    service = GeoService(
        GeoRepository(db_session),
        provider=NearbyFakeProvider(),
    )

    candidates = await service.nearby_places(
        latitude=41.1579,
        longitude=-8.6291,
        language="en",
        limit=4,
    )

    assert len(candidates) == 2
    assert candidates[0].name == FAKE_CITY_NAME
    assert candidates[1].name == "SGHR Nearby Geo City"