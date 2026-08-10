from uuid import UUID

from sqlalchemy import and_, func, or_, select
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
        normalized_name = (
            name or ""
        ).strip().lower()

        if not normalized_name:
            return None

        localized_fields = (
            City.name,
            City.name_ru,
            City.name_en,
            City.name_pt,
            City.name_es,
            City.name_uk,
            City.name_pl,
            City.name_de,
            City.name_nl,
        )

        result = await self.session.execute(
            select(City).where(
                City.country_id == country_id,
                or_(
                    *(
                        func.lower(
                            func.trim(field)
                        )
                        == normalized_name
                        for field in localized_fields
                    )
                ),
            )
        )
        return result.scalar_one_or_none()

    async def find_city_by_provider_metadata(
        self,
        *,
        provider: str,
        osm_type: str | None,
        osm_id: str | None,
        place_id: str | None,
    ) -> City | None:
        identifier_conditions = []

        if osm_id:
            osm_conditions = [
                City.extra_metadata[
                    "osm_id"
                ].astext
                == str(osm_id)
            ]

            if osm_type:
                osm_conditions.append(
                    City.extra_metadata[
                        "osm_type"
                    ].astext
                    == str(osm_type)
                )

            identifier_conditions.append(
                and_(*osm_conditions)
            )

        if place_id:
            identifier_conditions.append(
                City.extra_metadata[
                    "place_id"
                ].astext
                == str(place_id)
            )

        if not identifier_conditions:
            return None

        result = await self.session.execute(
            select(City)
            .where(
                City.extra_metadata[
                    "provider"
                ].astext
                == provider,
                or_(*identifier_conditions),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _normalize_language(
        language: str | None,
    ) -> str:
        normalized = (
            language or ""
        ).strip().lower()

        if normalized in {
            "ru",
            "en",
            "pt",
            "es",
            "uk",
            "pl",
            "de",
            "nl",
        }:
            return normalized

        return "ru"

    @classmethod
    def _apply_localized_name(
        cls,
        entity: City | Country,
        *,
        language: str | None,
        name: str,
        display_name: str | None = None,
    ) -> None:
        normalized_language = (
            cls._normalize_language(language)
        )
        cleaned_name = (name or "").strip()

        if not cleaned_name:
            return

        setattr(
            entity,
            f"name_{normalized_language}",
            cleaned_name,
        )

        metadata = dict(
            entity.extra_metadata or {}
        )

        stored_names = metadata.get(
            "localized_names"
        )
        localized_names = (
            dict(stored_names)
            if isinstance(stored_names, dict)
            else {}
        )
        localized_names[
            normalized_language
        ] = cleaned_name
        metadata["localized_names"] = (
            localized_names
        )

        cleaned_display_name = (
            display_name or ""
        ).strip()

        if cleaned_display_name:
            stored_display_names = metadata.get(
                "localized_display_names"
            )
            localized_display_names = (
                dict(stored_display_names)
                if isinstance(
                    stored_display_names,
                    dict,
                )
                else {}
            )
            localized_display_names[
                normalized_language
            ] = cleaned_display_name
            metadata[
                "localized_display_names"
            ] = localized_display_names

        entity.extra_metadata = metadata

    async def ensure_country(
        self,
        candidate: GeoPlaceCandidate,
        *,
        language: str = "ru",
    ) -> Country:
        country = await self.get_country_by_code(
            candidate.country_code
        )

        if country:
            if not country.is_active:
                country.is_active = True

            metadata = dict(
                country.extra_metadata or {}
            )
            metadata.setdefault(
                "provider",
                candidate.provider,
            )
            metadata.setdefault(
                "source",
                "geo_provider",
            )
            metadata.setdefault(
                "display_name",
                candidate.country_name,
            )
            country.extra_metadata = metadata

            self._apply_localized_name(
                country,
                language=language,
                name=candidate.country_name,
                display_name=(
                    candidate.country_name
                ),
            )
            return country

        country = Country(
            code=(
                candidate.country_code
                .upper()[:2]
            ),
            name=candidate.country_name,
            is_active=True,
            extra_metadata={
                "provider": candidate.provider,
                "source": "geo_provider",
                "display_name": (
                    candidate.country_name
                ),
            },
        )

        self._apply_localized_name(
            country,
            language=language,
            name=candidate.country_name,
            display_name=candidate.country_name,
        )

        self.session.add(country)
        await self.session.flush()
        return country

    async def ensure_city(
        self,
        *,
        country: Country,
        candidate: GeoPlaceCandidate,
        language: str = "ru",
    ) -> City:
        by_provider = (
            await self.find_city_by_provider_metadata(
                provider=candidate.provider,
                osm_type=candidate.osm_type,
                osm_id=candidate.osm_id,
                place_id=candidate.place_id,
            )
        )

        if by_provider:
            if not by_provider.is_active:
                by_provider.is_active = True

            if by_provider.latitude is None:
                by_provider.latitude = (
                    candidate.latitude
                )

            if by_provider.longitude is None:
                by_provider.longitude = (
                    candidate.longitude
                )

            self._apply_localized_name(
                by_provider,
                language=language,
                name=candidate.name,
                display_name=(
                    candidate.display_name
                ),
            )
            return by_provider

        by_name = (
            await self.get_city_by_country_and_name(
                country_id=country.id,
                name=candidate.name,
            )
        )

        if by_name:
            metadata = dict(
                by_name.extra_metadata or {}
            )
            metadata.setdefault(
                "provider",
                candidate.provider,
            )
            metadata.setdefault(
                "source",
                "geo_provider",
            )
            metadata.setdefault(
                "place_id",
                candidate.place_id,
            )
            metadata.setdefault(
                "osm_type",
                candidate.osm_type,
            )
            metadata.setdefault(
                "osm_id",
                candidate.osm_id,
            )
            metadata.setdefault(
                "place_type",
                candidate.place_type,
            )
            metadata.setdefault(
                "display_name",
                candidate.display_name,
            )
            by_name.extra_metadata = metadata

            if by_name.latitude is None:
                by_name.latitude = (
                    candidate.latitude
                )

            if by_name.longitude is None:
                by_name.longitude = (
                    candidate.longitude
                )

            if not by_name.is_active:
                by_name.is_active = True

            self._apply_localized_name(
                by_name,
                language=language,
                name=candidate.name,
                display_name=(
                    candidate.display_name
                ),
            )
            return by_name

        city = City(
            country_id=country.id,
            name=candidate.name,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            is_active=True,
            extra_metadata={
                "provider": candidate.provider,
                "source": "geo_provider",
                "place_id": candidate.place_id,
                "osm_type": candidate.osm_type,
                "osm_id": candidate.osm_id,
                "place_type": (
                    candidate.place_type
                ),
                "display_name": (
                    candidate.display_name
                ),
            },
        )

        self._apply_localized_name(
            city,
            language=language,
            name=candidate.name,
            display_name=candidate.display_name,
        )

        self.session.add(city)
        await self.session.flush()
        return city
