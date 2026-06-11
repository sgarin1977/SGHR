from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from database.models import Specialist
from database.repositories.search import (
    SpecialistSearchFilters,
    SpecialistSearchRepository,
)
from utils.geo import calculate_distance_km


@dataclass
class SpecialistSearchResult:
    specialist: Specialist
    distance_km: float | None = None
    ranking_score: float = 0.0
    city_name: str | None = None
    profession_name: str | None = None
    languages: list[str] = field(default_factory=list)


@dataclass
class SpecialistPublicCard:
    specialist_id: UUID
    display_name: str
    short_description: str
    city_id: UUID | None
    price_from: float | None
    price_to: float | None
    currency: str
    price_unit: str | None
    city_name: str | None = None
    category_name: str | None = None
    profession_name: str | None = None
    work_format: str | None = None
    service_titles: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    rating: float = 0.0
    reviews_count: int = 0
    is_verified: bool = False
    is_premium: bool = False
    distance_km: float | None = None


class GeoSearchService:
    def __init__(self, repository: SpecialistSearchRepository):
        self.repository = repository

    async def _enrich_search_results(
        self,
        results: list[SpecialistSearchResult],
        language: str = "ru",
    ) -> list[SpecialistSearchResult]:
        for result in results:
            result.city_name = await self.repository.get_city_name(
                result.specialist.city_id,
                language,
            )
            result.profession_name = await self.repository.get_profession_name(
                result.specialist.profession_id,
                language,
            )
            result.languages = await self.repository.get_language_codes_for_specialist(
                result.specialist.id,
            )

        return results

    def _calculate_ranking_score(
        self,
        *,
        specialist: Specialist,
        distance_km: float | None,
        radius_km: float,
        profile_completion_score: int,
        risk_score: int,
    ) -> float:
        if distance_km is None:
            distance_score = 0.5
        elif radius_km <= 0:
            distance_score = 1.0
        else:
            distance_score = max(0.0, 1.0 - (distance_km / radius_km))

        rating_score = min(float(specialist.rating or 0) / 5.0, 1.0)

        response_minutes = specialist.response_time_minutes
        if response_minutes is None:
            response_score = 0.5
        elif response_minutes <= 60:
            response_score = 1.0
        elif response_minutes <= 24 * 60:
            response_score = 0.7
        else:
            response_score = 0.3

        profile_completion = min(float(profile_completion_score or 0) / 100.0, 1.0)
        verified_bonus = 1.0 if specialist.is_verified else 0.0
        premium_boost = 1.0 if specialist.is_premium else 0.0

        created_at = specialist.created_at
        if created_at is None:
            freshness_score = 0.5
        else:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_days = max(0, (datetime.now(timezone.utc) - created_at).days)
            freshness_score = max(0.0, 1.0 - (age_days / 365.0))

        risk_penalty = min(float(risk_score or 0) / 100.0, 1.0)

        return (
            distance_score * 0.30
            + rating_score * 0.20
            + response_score * 0.15
            + profile_completion * 0.10
            + verified_bonus * 0.10
            + premium_boost * 0.10
            + freshness_score * 0.05
            - risk_penalty
        )

    async def get_public_card(
        self,
        *,
        specialist_id: UUID,
        requester_user_id: UUID | None = None,
        tenant_id: UUID | None = None,
        distance_km: float | None = None,
        log_event: bool = False,
        language: str = "ru",
    ) -> SpecialistPublicCard | None:
        specialist = await self.repository.get_active_specialist_for_card(specialist_id)
        if not specialist:
            return None

        languages = await self.repository.get_language_codes_for_specialist(specialist.id)
        city_name = await self.repository.get_city_name(specialist.city_id, language)

        category_name = await self.repository.get_category_name(
            specialist.category_id,
            language,
        )
        profession_name = await self.repository.get_profession_name(
            specialist.profession_id,
            language,
        )
        service_titles = await self.repository.get_public_service_titles(
            specialist.id,
            limit=5,
        )

        if log_event:
            await self.repository.log_specialist_viewed(
                tenant_id=tenant_id,
                user_id=requester_user_id,
                specialist_id=specialist.id,
            )

        return SpecialistPublicCard(
            specialist_id=specialist.id,
            display_name=specialist.display_name,
            short_description=specialist.short_description,
            city_id=specialist.city_id,
            price_from=float(specialist.price_from) if specialist.price_from is not None else None,
            price_to=float(specialist.price_to) if specialist.price_to is not None else None,
            currency=specialist.currency,
            price_unit=specialist.price_unit,
            languages=languages,
            rating=float(specialist.rating or 0),
            reviews_count=specialist.reviews_count or 0,
            category_name=category_name,
            profession_name=profession_name,
            work_format=specialist.work_format,
            service_titles=service_titles,
            is_verified=bool(specialist.is_verified),
            is_premium=bool(specialist.is_premium),
            distance_km=distance_km,
            city_name=city_name,
        )

    async def search_by_city(
        self,
        *,
        city_id: UUID,
        country_id: UUID | None = None,
        sort_by: str = "relevance",
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 10,
        offset: int = 0,
        requester_user_id: UUID | None = None,
        tenant_id: UUID | None = None,
        log_event: bool = False,
        interface_language: str = "ru",
    ) -> list[SpecialistSearchResult]:
        filters = SpecialistSearchFilters(
            city_id=city_id,
            category_id=category_id,
            country_id=country_id,
            profession_id=profession_id,
            price_min=price_min,
            price_max=price_max,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            rating_min=rating_min,
            work_format=work_format,
            status="active",
            limit=limit,
            offset=offset,
            sort_by=sort_by,
        )
        candidate_filters = SpecialistSearchFilters(
            city_id=city_id,
            category_id=category_id,
            country_id=country_id,
            profession_id=profession_id,
            price_min=price_min,
            price_max=price_max,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            work_format=work_format,
            rating_min=rating_min,
            status="active",
            limit=200,
            offset=0,
            sort_by=sort_by,
        )
        specialists = await self.repository.search_specialists(candidate_filters)
        user_metrics = await self.repository.get_user_metrics_by_specialist_ids(
            [specialist.id for specialist in specialists]
        )

        results = []
        for specialist in specialists:
            metrics = user_metrics.get(specialist.id, {})
            results.append(
                SpecialistSearchResult(
                    specialist=specialist,
                    distance_km=None,
                    ranking_score=self._calculate_ranking_score(
                        specialist=specialist,
                        distance_km=None,
                        radius_km=filters.normalized_radius_km,
                        profile_completion_score=metrics.get("profile_completion_score", 0),
                        risk_score=metrics.get("risk_score", 0),
                    ),
                )
            )

        if filters.sort_by == "relevance":
            results.sort(
                key=lambda item: (
                    item.ranking_score,
                    float(item.specialist.rating or 0),
                ),
                reverse=True,
            )
        else:
            results.sort(
                key=lambda item: (
                    float(item.specialist.rating or 0),
                    item.ranking_score,
                ),
                reverse=True,
            )

        paginated_results = results[
            filters.normalized_offset : filters.normalized_offset + filters.normalized_page_size
        ]

        paginated_results = await self._enrich_search_results(
            paginated_results,
            interface_language,
        )

        if log_event:
            await self.repository.log_search_performed(
                tenant_id=tenant_id,
                user_id=requester_user_id,
                filters=filters,
                results_count=len(paginated_results),
            )

        return paginated_results

    async def search_without_location(
        self,
        *,
        sort_by: str = "relevance",
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 10,
        offset: int = 0,
        requester_user_id: UUID | None = None,
        tenant_id: UUID | None = None,
        log_event: bool = False,
        interface_language: str = "ru",
    ) -> list[SpecialistSearchResult]:
        filters = SpecialistSearchFilters(
            category_id=category_id,
            profession_id=profession_id,
            price_min=price_min,
            price_max=price_max,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            rating_min=rating_min,
            work_format=work_format,
            status="active",
            limit=limit,
            offset=offset,
            sort_by="relevance" if sort_by == "distance" else sort_by,
        )

        specialists = await self.repository.search_specialists(filters)
        user_metrics = await self.repository.get_user_metrics_by_specialist_ids(
            [specialist.id for specialist in specialists]
        )

        results = []
        for specialist in specialists:
            metrics = user_metrics.get(specialist.id, {})
            results.append(
                SpecialistSearchResult(
                    specialist=specialist,
                    distance_km=None,
                    ranking_score=self._calculate_ranking_score(
                        specialist=specialist,
                        distance_km=None,
                        radius_km=filters.normalized_radius_km,
                        profile_completion_score=metrics.get("profile_completion_score", 0),
                        risk_score=metrics.get("risk_score", 0),
                    ),
                )
            )

        results = await self._enrich_search_results(
            results,
            interface_language,
        )

        if log_event:
            await self.repository.log_search_performed(
                tenant_id=tenant_id,
                user_id=requester_user_id,
                filters=filters,
                results_count=len(results),
            )

        return results

    async def search_by_radius(
        self,
        *,
        latitude: float,
        sort_by: str = "distance",
        longitude: float,
        radius_km: float = 25,
        country_id: UUID | None = None,
        country_wide: bool = False,
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 10,
        offset: int = 0,
        requester_user_id: UUID | None = None,
        tenant_id: UUID | None = None,
        log_event: bool = False,
        interface_language: str = "ru",
    ) -> list[SpecialistSearchResult]:
        filters = SpecialistSearchFilters(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            country_id=country_id,
            category_id=category_id,
            profession_id=profession_id,
            price_min=price_min,
            price_max=price_max,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            work_format=work_format,
            rating_min=rating_min,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
        )

        candidates = await self.repository.search_within_radius(
            latitude=latitude,
            longitude=longitude,
            radius_km=filters.normalized_radius_km,
            country_wide=country_wide,
            country_id=country_id,
            category_id=category_id,
            profession_id=profession_id,
            price_min=price_min,
            price_max=price_max,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            rating_min=rating_min,
            work_format=work_format,
            limit=200,
        )

        specialist_ids = [specialist.id for specialist, _distance in candidates]
        user_metrics = await self.repository.get_user_metrics_by_specialist_ids(
            specialist_ids
        )

        results: list[SpecialistSearchResult] = []

        for specialist, distance in candidates:
            metrics = user_metrics.get(specialist.id, {})
            results.append(
                SpecialistSearchResult(
                    specialist=specialist,
                    distance_km=distance,
                    ranking_score=self._calculate_ranking_score(
                        specialist=specialist,
                        distance_km=distance,
                        radius_km=filters.normalized_radius_km,
                        profile_completion_score=metrics.get("profile_completion_score", 0),
                        risk_score=metrics.get("risk_score", 0),
                    ),
                )
            )

        if filters.sort_by == "distance":
            results.sort(
                key=lambda item: (
                    item.distance_km if item.distance_km is not None else 999999,
                    -(item.specialist.created_at or datetime.min).timestamp(),
                    -item.ranking_score,
                    -float(item.specialist.rating or 0),
                    str(item.specialist.id),
                )
            )
        else:
            results.sort(
                key=lambda item: (
                    item.ranking_score,
                    -(item.distance_km or 999999),
                    float(item.specialist.rating or 0),
                ),
                reverse=True,
            )
        paginated_results = results[
            filters.normalized_offset : filters.normalized_offset + filters.normalized_page_size
        ]

        paginated_results = await self._enrich_search_results(
            paginated_results,
            interface_language,
        )

        if log_event:
            await self.repository.log_search_performed(
                tenant_id=tenant_id,
                user_id=requester_user_id,
                filters=filters,
                results_count=len(paginated_results),
            )

        return paginated_results