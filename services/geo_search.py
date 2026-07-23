from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from database.models import (
    ProfessionalCabinet,
    Specialist,
)
from database.repositories.event import EventRepository
from database.repositories.search import (
    SpecialistSearchFilters,
    SpecialistSearchRepository,
)
from utils.geo import calculate_distance_km
from database.repositories.reviews import (
    ReviewRepository,
)

@dataclass
class SpecialistSearchResult:
    specialist: Specialist
    professional_cabinet: (
        ProfessionalCabinet | None
    ) = None
    distance_km: float | None = None
    ranking_score: float = 0.0
    rating: float = 0.0
    reviews_count: int = 0
    is_premium: bool = False
    promotion_priority: float = 0.0
    city_name: str | None = None
    category_name: str | None = None
    profession_name: str | None = None
    languages: list[str] = field(
        default_factory=list
    )


@dataclass
class SpecialistPublicCard:
    specialist_id: UUID
    professional_cabinet_id: UUID
    display_name: str
    short_description: str
    city_id: UUID | None
    experience_years: int | None = None
    city_name: str | None = None
    country_name: str | None = None
    category_name: str | None = None
    profession_name: str | None = None
    work_format: str | None = None
    service_titles: list[str] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    rating: float = 0.0
    reviews_count: int = 0
    is_verified: bool = False
    moderation_status: str = (
        "pending_moderation"
    )
    is_available: bool = False
    availability_status: str = (
        "temporarily_unavailable"
    )
    is_premium: bool = False
    distance_km: float | None = None

@dataclass(frozen=True)
class SearchResultsViewedEvent:
    platform_user_id: str | None
    page: int
    visible_count: int
    has_next: bool
    category_id: str | None
    profession_id: str | None
    city_id: str | None
    location_state: str | None
    radius_km: int | float | None
    country_wide: bool
    sort_by: str | None
    category_name: str | None
    profession_name: str | None
    city_name: str | None
    search_text_query: str | None

@dataclass(frozen=True)
class EmptySearchEvent:
    page: int
    category_id: str | None
    profession_id: str | None
    city_id: str | None
    location_state: str | None
    radius_km: int | float | None
    country_wide: bool
    language_code: str | None
    work_format: str | None

@dataclass(frozen=True)
class PublicCardViewEvent:
    source: str
    results_page: int
    result_index: int
    distance_km: float | None

SEARCH_FILTER_EVENT_NAMES = frozenset(
    {
        "radius",
        "work_format",
        "language",
        "availability",
        "verified_profile",
        "rating",
        "sort",
        "reset",
    }
)

class GeoSearchService:
    def __init__(self, repository: SpecialistSearchRepository):
        self.repository = repository
        self.events = EventRepository(repository.session)

    async def list_recent_search_history(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        limit: int = 5,
    ) -> list[dict]:
        events = await (
            self.repository
            .list_recent_search_events(
                tenant_id=tenant_id,
                user_id=user_id,
                limit=limit,
            )
        )

        return [
            dict(event.payload or {})
            for event in events
        ]

    async def record_search_opened(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        source: str | None,
    ) -> None:
        normalized_source = (
            (source or "unknown").strip()[:100]
        )

        try:
            await self.events.create_event(
                event_type="search_opened",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="search",
                entity_id=None,
                payload={
                    "source": normalized_source,
                },
                platform="telegram",
            )
            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

    async def record_location_opened(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        source: str | None,
    ) -> None:
        normalized_source = (
            (source or "search_filter").strip()[:100]
        )

        try:
            await self.events.create_event(
                event_type="location_opened",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="search",
                entity_id=None,
                payload={
                    "source": normalized_source,
                },
                platform="telegram",
            )
            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

    async def record_filter_changed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        filter_name: str,
        value: str | int | float | bool | None,
    ) -> None:
        normalized_filter_name = (
            filter_name or ""
        ).strip().lower()

        if (
            normalized_filter_name
            not in SEARCH_FILTER_EVENT_NAMES
        ):
            raise ValueError(
                "Unsupported search filter event."
            )

        try:
            await self.events.create_event(
                event_type="filters_changed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="search",
                entity_id=None,
                payload={
                    "filter": normalized_filter_name,
                    "value": value,
                },
                platform="telegram",
            )
            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

    async def record_results_viewed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        event: SearchResultsViewedEvent,
    ) -> None:
        try:
            await self.events.create_event(
                event_type="results_viewed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="search",
                entity_id=None,
                payload={
                    "telegram_id": event.platform_user_id,
                    "page": max(int(event.page), 0),
                    "visible_count": max(
                        int(event.visible_count),
                        0,
                    ),
                    "has_next": bool(event.has_next),
                    "category_id": event.category_id,
                    "profession_id": event.profession_id,
                    "city_id": event.city_id,
                    "location_state": event.location_state,
                    "radius_km": event.radius_km,
                    "country_wide": bool(
                        event.country_wide
                    ),
                    "sort_by": event.sort_by,
                    "category_name": event.category_name,
                    "profession_name": event.profession_name,
                    "city_name": event.city_name,
                    "search_text_query": (
                        event.search_text_query
                    ),
                },
                platform="telegram",
            )
            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

    async def record_empty_search(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        event: EmptySearchEvent,
    ) -> None:
        try:
            await self.events.create_event(
                event_type="empty_search",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="search",
                entity_id=None,
                payload={
                    "page": max(int(event.page), 0),
                    "category_id": event.category_id,
                    "profession_id": event.profession_id,
                    "city_id": event.city_id,
                    "location_state": event.location_state,
                    "radius_km": event.radius_km,
                    "country_wide": bool(
                        event.country_wide
                    ),
                    "language_code": event.language_code,
                    "work_format": event.work_format,
                },
                platform="telegram",
            )
            await self.repository.session.commit()
        except Exception:
            await self.repository.session.rollback()
            raise

    async def get_public_card_for_viewer(
        self,
        *,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
        viewer_user_id: UUID | None,
        tenant_id: UUID,
        event: PublicCardViewEvent,
        language: str = "ru",
    ) -> SpecialistPublicCard | None:
        try:
            card = await self.get_public_card(
                specialist_id=specialist_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                requester_user_id=viewer_user_id,
                tenant_id=tenant_id,
                distance_km=event.distance_km,
                log_event=False,
                language=language,
            )

            if not card:
                return None

            if viewer_user_id and tenant_id:
                normalized_source = (
                    event.source or "search_results"
                ).strip()[:100]

                await self.events.create_event(
                    event_type="specialist_viewed",
                    tenant_id=tenant_id,
                    user_id=viewer_user_id,
                    entity_type="specialist",
                    entity_id=specialist_id,
                    payload={},
                    platform="telegram",
                )

                await self.events.create_event(
                    event_type="card_viewed",
                    tenant_id=tenant_id,
                    user_id=viewer_user_id,
                    entity_type="specialist",
                    entity_id=specialist_id,
                    payload={
                        "source": normalized_source,
                        "results_page": max(
                            int(event.results_page),
                            0,
                        ),
                        "result_index": max(
                            int(event.result_index),
                            0,
                        ),
                        "distance_km": event.distance_km,
                    },
                    platform="telegram",
                )

                await self.events.create_event(
                    event_type="profile_viewed",
                    tenant_id=tenant_id,
                    user_id=viewer_user_id,
                    entity_type="specialist",
                    entity_id=specialist_id,
                    payload={
                        "source": normalized_source,
                        "results_page": max(
                            int(event.results_page),
                            0,
                        ),
                        "result_index": max(
                            int(event.result_index),
                            0,
                        ),
                    },
                    platform="telegram",
                )

                await self.repository.session.commit()

            return card

        except Exception:
            await self.repository.session.rollback()
            raise

    def _activity_timestamp(self, specialist: Specialist) -> float:
        activity_at = specialist.updated_at or specialist.created_at
        if activity_at is None:
            return 0.0
        if activity_at.tzinfo is None:
            activity_at = activity_at.replace(tzinfo=timezone.utc)
        return activity_at.timestamp()

    async def _apply_cabinet_reputations(
        self,
        results: list[SpecialistSearchResult],
    ) -> list[SpecialistSearchResult]:
        cabinet_ids = [
            result.professional_cabinet.id
            for result in results
            if result.professional_cabinet
        ]

        reputations = await ReviewRepository(
            self.repository.session
        ).list_professional_cabinet_reputations(
            professional_cabinet_ids=(
                cabinet_ids
            ),
        )

        for result in results:
            cabinet = result.professional_cabinet

            if not cabinet:
                continue

            reputation = reputations.get(
                cabinet.id
            )

            if not reputation:
                continue

            result.rating = float(
                reputation.score or 0
            )
            result.reviews_count = int(
                reputation.review_count or 0
            )

        return results

    async def _apply_cabinet_promotions(
        self,
        *,
        results: list[SpecialistSearchResult],
        tenant_id: UUID,
    ) -> list[SpecialistSearchResult]:
        cabinet_ids = [
            result.professional_cabinet.id
            for result in results
            if result.professional_cabinet
        ]

        promotion_types_by_cabinet = await (
            self.repository
            .list_active_professional_cabinet_promotion_types(
                tenant_id=tenant_id,
                professional_cabinet_ids=(
                    cabinet_ids
                ),
            )
        )

        priority_by_type = {
            "top_category": 100.0,
            "premium": 50.0,
            "featured_service": 25.0,
            "boost": 15.0,
        }

        for result in results:
            cabinet = result.professional_cabinet
            if not cabinet:
                continue

            promotion_types = (
                promotion_types_by_cabinet.get(
                    cabinet.id,
                    set(),
                )
            )

            result.is_premium = (
                "premium" in promotion_types
            )
            result.promotion_priority = max(
                (
                    priority_by_type.get(
                        promotion_type,
                        0.0,
                    )
                    for promotion_type
                    in promotion_types
                ),
                default=0.0,
            )

        return results

    async def _enrich_search_results(
        self,
        results: list[SpecialistSearchResult],
        language: str = "ru",
    ) -> list[SpecialistSearchResult]:
        for result in results:
            cabinet = result.professional_cabinet

            if not cabinet:
                continue

            result.city_name = (
                await self.repository.get_city_name(
                    cabinet.city_id,
                    language,
                )
            )
            result.category_name = (
                await self.repository.get_category_name(
                    cabinet.category_id,
                    language,
                )
            )
            result.profession_name = (
                await self.repository.get_profession_name(
                    cabinet.profession_id,
                    language,
                )
            )
            result.languages = await (
                self.repository
                .get_language_codes_for_specialist(
                    result.specialist.id,
                )
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
        rating: float = 0.0,
        is_premium: bool = False,
    ) -> float:
        if distance_km is None:
            distance_score = 0.5
        elif radius_km <= 0:
            distance_score = 1.0
        else:
            distance_score = max(0.0, 1.0 - (distance_km / radius_km))

        rating_score = min(
            float(rating or 0) / 5.0,
            1.0,
        )

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
        premium_boost = (
            1.0 if is_premium else 0.0
        )

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
            + premium_boost * 0.10
            + freshness_score * 0.05
            - risk_penalty
        )

    def _recalculate_ranking_scores(
        self,
        *,
        results: list[SpecialistSearchResult],
        user_metrics: dict[UUID, dict],
        radius_km: float,
    ) -> list[SpecialistSearchResult]:
        for result in results:
            specialist = result.specialist
            metrics = user_metrics.get(
                specialist.id,
                {},
            )

            result.ranking_score = (
                self._calculate_ranking_score(
                    specialist=specialist,
                    distance_km=(
                        result.distance_km
                    ),
                    radius_km=radius_km,
                    profile_completion_score=(
                        metrics.get(
                            "profile_completion_score",
                            0,
                        )
                    ),
                    risk_score=metrics.get(
                        "risk_score",
                        0,
                    ),
                    rating=result.rating,
                    is_premium=result.is_premium,
                )
            )

        return results

    async def get_public_card(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: (
            UUID | None
        ) = None,
        requester_user_id: UUID | None = None,
        distance_km: float | None = None,
        log_event: bool = False,
        language: str = "ru",
    ) -> SpecialistPublicCard | None:
        context = await (
            self.repository
            .get_approved_professional_cabinet_for_card(
                specialist_id=specialist_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                tenant_id=tenant_id,
            )
        )
        if not context:
            return None

        specialist, cabinet = context
        effective_tenant_id = tenant_id

        languages = await (
            self.repository
            .get_language_codes_for_specialist(
                specialist.id
            )
        )
        city_name = await self.repository.get_city_name(
            cabinet.city_id,
            language,
        )
        country_name = (
            await self.repository.get_country_name(
                cabinet.country_id,
                language,
            )
        )
        category_name = (
            await self.repository.get_category_name(
                cabinet.category_id,
                language,
            )
        )
        profession_name = (
            await self.repository.get_profession_name(
                cabinet.profession_id,
                language,
            )
        )
        service_titles = await (
            self.repository
            .get_public_service_titles(
                specialist.id,
                professional_cabinet_id=(
                    cabinet.id
                ),
                limit=5,
            )
        )
        skill_names = await (
            self.repository
            .get_public_skill_names_for_cabinet(
                professional_cabinet_id=(
                    cabinet.id
                ),
                language=language,
                limit=8,
            )
        )

        reputation = await ReviewRepository(
            self.repository.session
        ).get_professional_cabinet_reputation(
            tenant_id=effective_tenant_id,
            professional_cabinet_id=cabinet.id,
        )

        promotion_types_by_cabinet = await (
            self.repository
            .list_active_professional_cabinet_promotion_types(
                tenant_id=effective_tenant_id,
                professional_cabinet_ids=[
                    cabinet.id
                ],
            )
        )
        cabinet_promotion_types = (
            promotion_types_by_cabinet.get(
                cabinet.id,
                set(),
            )
        )

        if log_event:
            await self.repository.log_specialist_viewed(
                tenant_id=effective_tenant_id,
                user_id=requester_user_id,
                specialist_id=specialist.id,
            )

        return SpecialistPublicCard(
            specialist_id=specialist.id,
            professional_cabinet_id=cabinet.id,
            display_name=specialist.display_name,
            short_description=(
                cabinet.description or ""
            ),
            experience_years=(
                specialist.experience_years
            ),
            city_id=cabinet.city_id,
            languages=languages,
            rating=(
                float(reputation.score)
                if reputation
                else 0.0
            ),
            reviews_count=(
                reputation.review_count
                if reputation
                else 0
            ),
            category_name=category_name,
            profession_name=profession_name,
            work_format=cabinet.work_format,
            service_titles=service_titles,
            skill_names=skill_names,
            is_verified=bool(
                specialist.is_verified
            ),
            moderation_status=(
                cabinet.moderation_status
            ),
            is_available=(
                cabinet.availability_status
                == "available"
            ),
            availability_status=(
                cabinet.availability_status
            ),
            is_premium=(
                "premium"
                in cabinet_promotion_types
            ),
            distance_km=distance_km,
            city_name=city_name,
            country_name=country_name,
        )

    async def search_by_city(
        self,
        *,
        city_id: UUID,
        country_id: UUID | None = None,
        sort_by: str = "relevance",
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        profession_ids: list[UUID] | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        available_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 10,
        offset: int = 0,
        requester_user_id: UUID | None = None,
        tenant_id: UUID,
        log_event: bool = False,
        interface_language: str = "ru",
    ) -> list[SpecialistSearchResult]:
        filters = SpecialistSearchFilters(
            city_id=city_id,
            category_id=category_id,
            country_id=country_id,
            profession_id=profession_id,
            profession_ids=profession_ids,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            available_only=available_only,
            rating_min=rating_min,
            work_format=work_format,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
        )
        candidate_filters = SpecialistSearchFilters(
            city_id=city_id,
            category_id=category_id,
            country_id=country_id,
            profession_id=profession_id,
            profession_ids=profession_ids,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            available_only=available_only,
            work_format=work_format,
            rating_min=rating_min,
            limit=200,
            offset=0,
            sort_by=sort_by,
        )
        cabinet_rows = await (
            self.repository
            .search_professional_cabinets(
                candidate_filters,
                tenant_id=tenant_id,
            )
        )
        user_metrics = await (
            self.repository
            .get_user_metrics_by_specialist_ids(
                [
                    specialist.id
                    for specialist, _cabinet
                    in cabinet_rows
                ]
            )
        )

        results = []
        for specialist, cabinet in cabinet_rows:
            metrics = user_metrics.get(
                specialist.id,
                {},
            )
            results.append(
                SpecialistSearchResult(
                    specialist=specialist,
                    professional_cabinet=cabinet,
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
        results = await (
            self._apply_cabinet_reputations(
                results
            )
        )
        results = await (
            self._apply_cabinet_promotions(
                results=results,
                tenant_id=tenant_id,
            )
        )
        results = (
            self._recalculate_ranking_scores(
                results=results,
                user_metrics=user_metrics,
                radius_km=(
                    filters.normalized_radius_km
                ),
            )
        )
        if filters.sort_by == "relevance":
            results.sort(
                key=lambda item: (
                    -int(bool(item.is_premium)),
                    -float(item.promotion_priority or 0),
                    -float(item.rating or 0),
                    -int(bool(item.specialist.is_verified)),
                    -int(item.reviews_count or 0),
                    -self._activity_timestamp(item.specialist),
                    str(item.specialist.id),
                )
            )
        else:
            results.sort(
                key=lambda item: (
                    -float(item.rating or 0),
                    -int(bool(item.specialist.is_verified)),
                    -int(item.reviews_count or 0),
                    -self._activity_timestamp(item.specialist),
                    str(item.specialist.id),
                )
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
        profession_ids: list[UUID] | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        available_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 10,
        offset: int = 0,
        requester_user_id: UUID | None = None,
        tenant_id: UUID,
        log_event: bool = False,
        interface_language: str = "ru",
    ) -> list[SpecialistSearchResult]:
        filters = SpecialistSearchFilters(
            category_id=category_id,
            profession_id=profession_id,
            profession_ids=profession_ids,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            available_only=available_only,
            rating_min=rating_min,
            work_format=work_format,
            limit=limit,
            offset=offset,
            sort_by="relevance" if sort_by == "distance" else sort_by,
        )

        cabinet_rows = await (
            self.repository
            .search_professional_cabinets(
                filters,
                tenant_id=tenant_id,
            )
        )
        user_metrics = await (
            self.repository
            .get_user_metrics_by_specialist_ids(
                [
                    specialist.id
                    for specialist, _cabinet
                    in cabinet_rows
                ]
            )
        )

        results = []
        for specialist, cabinet in cabinet_rows:
            metrics = user_metrics.get(
                specialist.id,
                {},
            )
            results.append(
                SpecialistSearchResult(
                    specialist=specialist,
                    professional_cabinet=cabinet,
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
        results = await (
            self._apply_cabinet_reputations(
                results
            )
        )
        results = await (
            self._apply_cabinet_promotions(
                results=results,
                tenant_id=tenant_id,
            )
        )
        results = (
            self._recalculate_ranking_scores(
                results=results,
                user_metrics=user_metrics,
                radius_km=(
                    filters.normalized_radius_km
                ),
            )
        )
        results.sort(
            key=lambda item: (
                -int(bool(item.is_premium)),
                -float(item.promotion_priority or 0),
                -float(item.rating or 0),
                -int(bool(item.specialist.is_verified)),
                -int(item.reviews_count or 0),
                -self._activity_timestamp(item.specialist),
                str(item.specialist.id),
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
        profession_ids: list[UUID] | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        available_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 10,
        offset: int = 0,
        requester_user_id: UUID | None = None,
        tenant_id: UUID,
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
            profession_ids=profession_ids,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            available_only=available_only,
            work_format=work_format,
            rating_min=rating_min,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
        )

        cabinet_candidates = await (
            self.repository
            .search_professional_cabinets_within_radius(
                tenant_id=tenant_id,
                latitude=latitude,
            longitude=longitude,
            radius_km=filters.normalized_radius_km,
            country_wide=country_wide,
            country_id=country_id,
            category_id=category_id,
            profession_id=profession_id,
            profession_ids=profession_ids,
            language_code=language_code,
            verified_only=verified_only,
            premium_only=premium_only,
            available_only=available_only,
            rating_min=rating_min,
            work_format=work_format,
                limit=200,
            )
        )

        specialist_ids = [
            specialist.id
            for specialist, _cabinet, _distance
            in cabinet_candidates
        ]
        user_metrics = await self.repository.get_user_metrics_by_specialist_ids(
            specialist_ids
        )

        results: list[SpecialistSearchResult] = []

        for (
            specialist,
            cabinet,
            distance,
        ) in cabinet_candidates:
            metrics = user_metrics.get(
                specialist.id,
                {},
            )
            results.append(
                SpecialistSearchResult(
                    specialist=specialist,
                    professional_cabinet=cabinet,
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
        results = await (
            self._apply_cabinet_reputations(
                results
            )
        )
        results = await (
            self._apply_cabinet_promotions(
                results=results,
                tenant_id=tenant_id,
            )
        )
        results = (
            self._recalculate_ranking_scores(
                results=results,
                user_metrics=user_metrics,
                radius_km=(
                    filters.normalized_radius_km
                ),
            )
        )
        if filters.sort_by == "distance":
            results.sort(
                key=lambda item: (
                    -int(bool(item.is_premium)),
                    -float(item.promotion_priority or 0),
                    item.distance_km if item.distance_km is not None else 999999,
                    -float(item.rating or 0),
                    -int(bool(item.specialist.is_verified)),
                    -int(item.reviews_count or 0),
                    -self._activity_timestamp(item.specialist),
                    str(item.specialist.id),
                )
            )
        else:
            results.sort(
                key=lambda item: (
                    -int(bool(item.is_premium)),
                    -float(item.promotion_priority or 0),
                    -float(item.rating or 0),
                    -int(bool(item.specialist.is_verified)),
                    -int(item.reviews_count or 0),
                    -self._activity_timestamp(item.specialist),
                    str(item.specialist.id),
                )
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