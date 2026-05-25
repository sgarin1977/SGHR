from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import City, Country
from services.geo_provider import GeoPlaceCandidate


class GeoRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_country_by_code(self, code: str) -> Country | None:
        normalized_code = (code or "").strip().upper()[:2]
        if not normalized_code:
            return None

        result = await self.session.execute(
            select(Country).where(func.upper(Country.code) == normalized_code)
        )
        return result.scalar_one_or_none()

    async def get_city_by_country_name_and_coordinates(
        self,
        *,
        country_id: UUID,
        name: str,
        latitude: float,
        longitude: float,
    ) -> City | None:
        normalized_name = (name or "").strip()
        if not normalized_name:
            return None

        result = await self.session.execute(
            select(City).where(
                City.country_id == country_id,
                func.lower(City.name) == normalized_name.lower(),
                City.latitude == latitude,
                City.longitude == longitude,
            )
        )
        return result.scalar_one_or_none()

    async def get_city_by_country_and_name(
        self,
        *,
        country_id: UUID,
        name: str,
    ) -> City | None:
        normalized_name = (name or "").strip()
        if not normalized_name:
            return None

        result = await self.session.execute(
            select(City).where(
                City.country_id == country_id,
                func.lower(City.name) == normalized_name.lower(),
            )
        )
        return result.scalar_one_or_none()

    async def find_city_by_provider_metadata(
        self,
        *,
        provider: str,
        osm_id: str | None,
        place_id: str | None,
    ) -> City | None:
        if not osm_id and not place_id:
            return None

        conditions = [City.extra_metadata["provider"].astext == provider]

        if osm_id:
            conditions.append(City.extra_metadata["osm_id"].astext == str(osm_id))

        if place_id:
            conditions.append(City.extra_metadata["place_id"].astext == str(place_id))

        result = await self.session.execute(
            select(City).where(*conditions).limit(1)
        )
        return result.scalar_one_or_none()

    async def ensure_country(self, candidate: GeoPlaceCandidate) -> Country:
        country = await self.get_country_by_code(candidate.country_code)
        if country:
            if not country.is_active:
                country.is_active = True

            metadata = dict(country.extra_metadata or {})
            metadata.setdefault("provider", candidate.provider)
            metadata.setdefault("source", "geo_provider")
            metadata.setdefault("display_name", candidate.country_name)
            country.extra_metadata = metadata
            return country

        country = Country(
            code=candidate.country_code.upper()[:2],
            name=candidate.country_name,
            name_ru=candidate.country_name,
            name_en=candidate.country_name,
            name_pt=candidate.country_name,
            is_active=True,
            extra_metadata={
                "provider": candidate.provider,
                "source": "geo_provider",
                "display_name": candidate.country_name,
            },
        )
        self.session.add(country)
        await self.session.flush()
        return country

    async def ensure_city(
        self,
        *,
        country: Country,
        candidate: GeoPlaceCandidate,
    ) -> City:
        by_provider = await self.find_city_by_provider_metadata(
            provider=candidate.provider,
            osm_id=candidate.osm_id,
            place_id=candidate.place_id,
        )
        if by_provider:
            if not by_provider.is_active:
                by_provider.is_active = True
            return by_provider

        by_name = await self.get_city_by_country_and_name(
            country_id=country.id,
            name=candidate.name,
        )
        if by_name:
            metadata = dict(by_name.extra_metadata or {})
            metadata.setdefault("provider", candidate.provider)
            metadata.setdefault("source", "geo_provider")
            metadata.setdefault("place_id", candidate.place_id)
            metadata.setdefault("osm_type", candidate.osm_type)
            metadata.setdefault("osm_id", candidate.osm_id)
            metadata.setdefault("place_type", candidate.place_type)
            metadata.setdefault("display_name", candidate.display_name)
            by_name.extra_metadata = metadata

            if by_name.latitude is None:
                by_name.latitude = candidate.latitude

            if by_name.longitude is None:
                by_name.longitude = candidate.longitude

            if not by_name.is_active:
                by_name.is_active = True

            return by_name

        city = City(
            country_id=country.id,
            name=candidate.name,
            name_ru=candidate.name,
            name_en=candidate.name,
            name_pt=candidate.name,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            is_active=True,
            extra_metadata={
                "provider": candidate.provider,
                "source": "geo_provider",
                "place_id": candidate.place_id,
                "osm_type": candidate.osm_type,
                "osm_id": candidate.osm_id,
                "place_type": candidate.place_type,
                "display_name": candidate.display_name,
            },
        )
        self.session.add(city)
        await self.session.flush()
        return city