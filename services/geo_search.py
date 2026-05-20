from dataclasses import dataclass
from uuid import UUID

from database.models import Specialist
from database.repositories.search import (
    SpecialistSearchFilters,
    SpecialistSearchRepository,
)
from utils.geo import haversine_distance_km


@dataclass
class SpecialistSearchResult:
    specialist: Specialist
    distance_km: float | None = None


class GeoSearchService:
    def __init__(self, repository: SpecialistSearchRepository):
        self.repository = repository

    async def search_by_city(
        self,
        *,
        city_id: UUID,
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SpecialistSearchResult]:
        specialists = await self.repository.search_specialists(
            SpecialistSearchFilters(
                city_id=city_id,
                category_id=category_id,
                profession_id=profession_id,
                status="active",
                limit=limit,
                offset=offset,
            )
        )

        return [
            SpecialistSearchResult(specialist=specialist)
            for specialist in specialists
        ]

    async def search_by_radius(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float,
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SpecialistSearchResult]:
        candidates = await self.repository.list_active_with_coordinates(
            category_id=category_id,
            profession_id=profession_id,
            limit=200,
        )

        results: list[SpecialistSearchResult] = []

        for specialist in candidates:
            if specialist.latitude is None or specialist.longitude is None:
                continue

            distance = haversine_distance_km(
                latitude,
                longitude,
                float(specialist.latitude),
                float(specialist.longitude),
            )

            if distance <= radius_km:
                results.append(
                    SpecialistSearchResult(
                        specialist=specialist,
                        distance_km=distance,
                    )
                )

        results.sort(key=lambda item: item.distance_km if item.distance_km is not None else 999999)

        return results[offset : offset + limit]