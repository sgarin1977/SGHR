from dataclasses import dataclass
from uuid import UUID

from database.repositories.geo_repository import GeoRepository
from database.repositories.event import EventRepository
from database.repositories.rate_limit import RateLimitRepository
from services.rate_limit import RateLimitService
from services.geo_provider import (
    GeoPlaceCandidate,
    GeoProviderError,
    NominatimGeoProvider,
)


class GeoServiceError(Exception):
    pass


@dataclass(frozen=True)
class SavedGeoPlace:
    country_id: UUID
    city_id: UUID
    country_name: str
    country_code: str
    city_name: str
    latitude: float
    longitude: float
    display_name: str

    def to_state(self) -> dict:
        return {
            "country_id": str(self.country_id),
            "city_id": str(self.city_id),
            "country_name": self.country_name,
            "country_code": self.country_code,
            "city_name": self.city_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "display_name": self.display_name,
        }


class GeoService:
    def __init__(
        self,
        repository: GeoRepository,
        provider: NominatimGeoProvider | None = None,
    ):
        self.repository = repository
        self.events = EventRepository(repository.session)
        self.rate_limits = RateLimitService(
            RateLimitRepository(repository.session)
        )
        self.provider = provider or NominatimGeoProvider()

    async def search_places(
        self,
        *,
        query: str,
        language: str = "ru",
        limit: int = 5,
    ) -> list[GeoPlaceCandidate]:
        normalized_query = (query or "").strip()
        if len(normalized_query) < 2:
            return []

        try:
            return await self.provider.search(
                query=normalized_query,
                language=self._normalize_language(language),
                limit=limit,
            )
        except GeoProviderError as exc:
            raise GeoServiceError(str(exc)) from exc

    async def reverse_place(
        self,
        *,
        latitude: float,
        longitude: float,
        language: str = "ru",
    ) -> GeoPlaceCandidate | None:
        try:
            return await self.provider.reverse(
                latitude=float(latitude),
                longitude=float(longitude),
                language=self._normalize_language(language),
            )
        except GeoProviderError as exc:
            raise GeoServiceError(str(exc)) from exc

    async def nearby_places(
        self,
        *,
        latitude: float,
        longitude: float,
        language: str = "ru",
        limit: int = 4,
    ) -> list[GeoPlaceCandidate]:
        normalized_language = self._normalize_language(language)

        if hasattr(self.provider, "search_nearby"):
            candidates = await self.provider.search_nearby(
                latitude=latitude,
                longitude=longitude,
                language=normalized_language,
                limit=limit,
            )
            if candidates:
                return candidates[:limit]

        primary = await self.reverse_place(
            latitude=latitude,
            longitude=longitude,
            language=normalized_language,
        )
        return [primary] if primary else []

    async def confirm_place(
        self,
        candidate: GeoPlaceCandidate | dict,
        *,
        commit: bool = True,
    ) -> SavedGeoPlace:
        place = GeoPlaceCandidate.from_state(candidate)

        if not place.name:
            raise GeoServiceError("Place name is required.")

        if not place.country_name or len(place.country_code) != 2:
            raise GeoServiceError("Country data is required.")

        country = await self.repository.ensure_country(place)
        city = await self.repository.ensure_city(
            country=country,
            candidate=place,
        )

        if commit:
            await self.repository.session.commit()

        return SavedGeoPlace(
            country_id=country.id,
            city_id=city.id,
            country_name=country.name,
            country_code=country.code,
            city_name=city.name,
            latitude=float(city.latitude) if city.latitude is not None else place.latitude,
            longitude=float(city.longitude) if city.longitude is not None else place.longitude,
            display_name=(city.extra_metadata or {}).get("display_name") or place.display_name,
        )

    async def confirm_search_place(
        self,
        candidate: GeoPlaceCandidate | dict,
        *,
        tenant_id: UUID | None,
        user_id: UUID | None,
        source: str = "search_filter",
    ) -> SavedGeoPlace:
        normalized_source = (
            source or "search_filter"
        ).strip()[:100]

        try:
            if tenant_id and user_id:
                await self.rate_limits.ensure_geo_change_allowed(
                    tenant_id=tenant_id,
                    user_id=user_id,
                )

            place = await self.confirm_place(
                candidate,
                commit=False,
            )

            if tenant_id and user_id:
                await self.events.create_event(
                    event_type="geo_change",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    entity_type="city",
                    entity_id=place.city_id,
                    payload={
                        "source": normalized_source,
                        "country_id": str(
                            place.country_id
                        ),
                    },
                    platform="telegram",
                )

                await self.events.create_event(
                    event_type="location_selected",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    entity_type="city",
                    entity_id=place.city_id,
                    payload={
                        "source": normalized_source,
                        "country_id": str(
                            place.country_id
                        ),
                        "city_name": place.city_name,
                        "location_state": "selected",
                    },
                    platform="telegram",
                )

            await self.repository.session.commit()
            return place

        except Exception:
            await self.repository.session.rollback()
            raise

    def _normalize_language(self, language: str | None) -> str:
        return language if language in {"ru", "en", "pt"} else "ru"