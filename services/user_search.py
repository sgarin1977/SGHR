from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.repositories.specialist import (
    SpecialistRepository,
)
from database.repositories.search import (
    SpecialistSearchRepository,
)
from database.repositories.favorites import (
    FavoriteRepository,
)
from services.specialist import (
    SpecialistSearchSelectionService,
    SpecialistSearchTextService,
)
from services.geo_search import (
    EmptySearchEvent,
    GeoSearchService,
    PublicCardViewEvent,
    SearchResultsViewedEvent,
)
from services.favorites import (
    FavoriteService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
    UserSettingsValidationError,
)


class UserSearchSelectionError(ValueError):
    pass


class UserSearchQueryError(ValueError):
    pass


class UserSearchAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class UserSearchActor:
    user_id: UUID | None
    tenant_id: UUID | None
    language: str


@dataclass(frozen=True)
class UserSearchAction:
    actor: UserSearchActor
    result: Any


@dataclass(frozen=True)
class UserSearchFilters:
    category_id: UUID | None
    profession_id: UUID | None
    profession_ids: tuple[UUID, ...]
    city_id: UUID | None
    country_id: UUID | None
    latitude: float | None
    longitude: float | None
    radius_km: float
    location_state: str
    country_wide: bool
    language_code: str | None
    verified_only: bool
    available_only: bool
    premium_only: bool
    work_format: str | None
    rating_min: float | None
    sort_by: str
    category_name: str | None
    profession_name: str | None
    city_name: str | None
    search_text_query: str | None

    @property
    def has_geo(self) -> bool:
        return (
            self.latitude is not None
            and self.longitude is not None
        )

    @property
    def remote_only(self) -> bool:
        return self.work_format == "remote"

    @property
    def without_location(self) -> bool:
        return (
            self.location_state == "without"
            or self.remote_only
        )



@dataclass(frozen=True)
class UserSearchPage:
    actor: UserSearchActor
    filters: UserSearchFilters
    page: int
    visible_results: tuple[Any, ...]
    total_count: int
    has_next: bool
    saved_professional_cabinet_ids: (
        frozenset[UUID]
    )


class UserSearchService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: (
            UserSettingsService | None
        ) = None,
        repository: (
            SpecialistRepository | None
        ) = None,
        selection: (
            SpecialistSearchSelectionService
            | None
        ) = None,
        text_search: (
            SpecialistSearchTextService | None
        ) = None,
        search_repository: (
            SpecialistSearchRepository | None
        ) = None,
        geo_search: (
            GeoSearchService | None
        ) = None,
        favorite_repository: (
            FavoriteRepository | None
        ) = None,
        favorites: (
            FavoriteService | None
        ) = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.repository = (
            repository
            or SpecialistRepository(
                session
            )
        )
        self.selection = (
            selection
            or SpecialistSearchSelectionService(
                self.repository
            )
        )
        self.text_search = (
            text_search
            or SpecialistSearchTextService(
                self.repository
            )
        )
        self.search_repository = (
            search_repository
            or SpecialistSearchRepository(
                session
            )
        )
        self.geo_search = (
            geo_search
            or GeoSearchService(
                self.search_repository
            )
        )
        self.favorite_repository = (
            favorite_repository
            or FavoriteRepository(session)
        )
        self.favorites = (
            favorites
            or FavoriteService(
                self.favorite_repository
            )
        )

    @staticmethod
    def fallback_language(
        language: str | None,
    ) -> str:
        try:
            return (
                UserSettingsService
                .validate_language_code(
                    language or "ru"
                )
            )
        except UserSettingsValidationError:
            return "ru"

    @staticmethod
    def parse_id(
        value: UUID | str,
        *,
        field: str,
    ) -> UUID:
        try:
            return (
                value
                if isinstance(value, UUID)
                else UUID(str(value))
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise UserSearchSelectionError(
                f"Invalid {field}."
            ) from exc

    @classmethod
    def parse_optional_id(
        cls,
        value: UUID | str | None,
        *,
        field: str,
    ) -> UUID | None:
        if value is None:
            return None

        return cls.parse_id(
            value,
            field=field,
        )

    async def resolve_actor(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        fallback_language: (
            str | None
        ) = None,
    ) -> UserSearchActor:
        if platform_user_id is not None:
            try:
                context = await (
                    self.settings
                    .get_context(
                        platform_user_id=(
                            platform_user_id
                        ),
                    )
                )
            except UserSettingsNotFoundError:
                pass
            else:
                return UserSearchActor(
                    user_id=context.user_id,
                    tenant_id=(
                        context.tenant_id
                    ),
                    language=(
                        context
                        .interface_language
                    ),
                )

        return UserSearchActor(
            user_id=None,
            tenant_id=None,
            language=(
                self.fallback_language(
                    fallback_language
                )
            ),
        )

    async def list_categories(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        fallback_language: (
            str | None
        ) = None,
        limit: int = 100,
    ) -> UserSearchAction:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )
        result = await (
            self.selection
            .list_active_categories(
                language=actor.language,
                limit=max(1, int(limit)),
            )
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    async def list_professions(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        category_id: (
            UUID | str | None
        ),
        fallback_language: (
            str | None
        ) = None,
        limit: int = 100,
    ) -> UserSearchAction:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )
        parsed_category_id = (
            self.parse_optional_id(
                category_id,
                field="category",
            )
        )
        result = await (
            self.selection
            .list_profession_options(
                category_id=(
                    parsed_category_id
                ),
                language=actor.language,
                limit=max(1, int(limit)),
            )
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    async def select_category(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        category_id: UUID | str,
        fallback_language: (
            str | None
        ) = None,
    ) -> UserSearchAction:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )
        result = await (
            self.selection.select_category(
                category_id=self.parse_id(
                    category_id,
                    field="category",
                ),
                language=actor.language,
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
            )
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    async def select_profession(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        profession_id: UUID | str,
        category_id: (
            UUID | str | None
        ),
        fallback_language: (
            str | None
        ) = None,
    ) -> UserSearchAction:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )
        result = await (
            self.selection
            .select_profession(
                profession_id=self.parse_id(
                    profession_id,
                    field="profession",
                ),
                category_id=(
                    self.parse_optional_id(
                        category_id,
                        field="category",
                    )
                ),
                language=actor.language,
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
            )
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    async def open_search(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        fallback_language: (
            str | None
        ) = None,
        source: str | None = None,
    ) -> UserSearchActor:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )

        if (
            actor.user_id is not None
            and actor.tenant_id is not None
        ):
            await (
                self.geo_search
                .record_search_opened(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    source=source,
                )
            )

        return actor

    async def list_history(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        fallback_language: (
            str | None
        ) = None,
        limit: int = 5,
    ) -> UserSearchAction:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )

        if (
            actor.user_id is None
            or actor.tenant_id is None
        ):
            return UserSearchAction(
                actor=actor,
                result=[],
            )

        result = await (
            self.geo_search
            .list_recent_search_history(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                limit=max(1, int(limit)),
            )
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    async def open_location_filter(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        fallback_language: (
            str | None
        ) = None,
        source: str | None = (
            "search_filter"
        ),
    ) -> UserSearchActor:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )

        if (
            actor.user_id is not None
            and actor.tenant_id is not None
        ):
            await (
                self.geo_search
                .record_location_opened(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    source=source,
                )
            )

        return actor

    async def record_filter_changed(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        filter_name: str,
        value: (
            str
            | int
            | float
            | bool
            | None
        ),
        fallback_language: (
            str | None
        ) = None,
    ) -> UserSearchActor:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )

        if (
            actor.user_id is not None
            and actor.tenant_id is not None
        ):
            await (
                self.geo_search
                .record_filter_changed(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    filter_name=filter_name,
                    value=value,
                )
            )

        return actor

    async def search_text(
        self,
        *,
        platform_user_id: (
            int | str | None
        ),
        query: str,
        fallback_language: (
            str | None
        ) = None,
        limit: int = 10,
    ) -> UserSearchAction:
        normalized_query = (
            query or ""
        ).strip()

        if len(normalized_query) < 2:
            raise UserSearchQueryError(
                "Search query is too short."
            )

        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )
        result = await self.text_search.search(
            normalized_query,
            language=actor.language,
            limit=max(1, int(limit)),
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    @staticmethod
    def parse_nonnegative_int(
        value,
        *,
        field: str,
    ) -> int:
        try:
            parsed = int(value)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise UserSearchSelectionError(
                f"Invalid {field}."
            ) from exc

        if parsed < 0:
            raise UserSearchSelectionError(
                f"Invalid {field}."
            )

        return parsed

    @staticmethod
    def parse_optional_float(
        value,
        *,
        field: str,
    ) -> float | None:
        if value is None:
            return None

        try:
            return float(value)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise UserSearchSelectionError(
                f"Invalid {field}."
            ) from exc

    async def require_registered_actor(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: (
            str | None
        ) = None,
    ) -> UserSearchActor:
        actor = await self.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )

        if (
            actor.user_id is None
            or actor.tenant_id is None
        ):
            raise UserSearchAccessError(
                "Registered search actor required."
            )

        return actor

    async def open_result_card(
        self,
        *,
        platform_user_id: int | str,
        specialist_id: UUID | str,
        professional_cabinet_id: (
            UUID | str | None
        ),
        results_page: int | str,
        result_index: int | str,
        distance_km: (
            float | str | None
        ) = None,
        fallback_language: (
            str | None
        ) = None,
        source: str = "search_results",
    ) -> UserSearchAction:
        actor = await (
            self.require_registered_actor(
                platform_user_id=(
                    platform_user_id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )

        parsed_specialist_id = self.parse_id(
            specialist_id,
            field="specialist",
        )
        parsed_cabinet_id = (
            self.parse_optional_id(
                professional_cabinet_id,
                field="professional cabinet",
            )
        )
        parsed_page = (
            self.parse_nonnegative_int(
                results_page,
                field="results page",
            )
        )
        parsed_index = (
            self.parse_nonnegative_int(
                result_index,
                field="result index",
            )
        )
        parsed_distance = (
            self.parse_optional_float(
                distance_km,
                field="distance",
            )
        )

        result = await (
            self.geo_search
            .get_public_card_for_viewer(
                specialist_id=(
                    parsed_specialist_id
                ),
                professional_cabinet_id=(
                    parsed_cabinet_id
                ),
                viewer_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                event=PublicCardViewEvent(
                    source=source,
                    results_page=parsed_page,
                    result_index=parsed_index,
                    distance_km=(
                        parsed_distance
                    ),
                ),
                language=actor.language,
            )
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    async def get_selected_card(
        self,
        *,
        platform_user_id: int | str,
        specialist_id: UUID | str,
        professional_cabinet_id: (
            UUID | str | None
        ) = None,
        fallback_language: (
            str | None
        ) = None,
    ) -> UserSearchAction:
        actor = await (
            self.require_registered_actor(
                platform_user_id=(
                    platform_user_id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )

        result = await (
            self.geo_search.get_public_card(
                tenant_id=actor.tenant_id,
                specialist_id=self.parse_id(
                    specialist_id,
                    field="specialist",
                ),
                professional_cabinet_id=(
                    self.parse_optional_id(
                        professional_cabinet_id,
                        field=(
                            "professional cabinet"
                        ),
                    )
                ),
                requester_user_id=actor.user_id,
                language=actor.language,
            )
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    @classmethod
    def build_filters(
        cls,
        data: Mapping[str, Any],
        *,
        default_radius_km: (
            int | float
        ) = 25,
    ) -> UserSearchFilters:
        raw_profession_ids = (
            data.get(
                "selected_profession_ids"
            )
            or []
        )

        if isinstance(
            raw_profession_ids,
            (
                str,
                bytes,
            ),
        ):
            raw_profession_ids = [
                raw_profession_ids
            ]

        profession_ids = tuple(
            cls.parse_id(
                item,
                field="profession",
            )
            for item in raw_profession_ids
        )

        latitude = cls.parse_optional_float(
            data.get("latitude"),
            field="latitude",
        )
        longitude = cls.parse_optional_float(
            data.get("longitude"),
            field="longitude",
        )
        radius = cls.parse_optional_float(
            data.get("radius_km"),
            field="radius",
        )
        rating_min = cls.parse_optional_float(
            data.get("rating_min"),
            field="rating",
        )

        city_id = cls.parse_optional_id(
            data.get("city_id"),
            field="city",
        )
        work_format = data.get("work_format")
        location_state = (
            data.get("location_state")
            or ""
        )

        has_geo = (
            latitude is not None
            and longitude is not None
        )
        without_location = (
            location_state == "without"
            or work_format == "remote"
        )

        if (
            city_id is None
            and not has_geo
            and not without_location
        ):
            location_state = "without"

        return UserSearchFilters(
            category_id=(
                cls.parse_optional_id(
                    data.get("category_id"),
                    field="category",
                )
            ),
            profession_id=(
                cls.parse_optional_id(
                    data.get("profession_id"),
                    field="profession",
                )
            ),
            profession_ids=profession_ids,
            city_id=city_id,
            country_id=(
                cls.parse_optional_id(
                    data.get("country_id"),
                    field="country",
                )
            ),
            latitude=latitude,
            longitude=longitude,
            radius_km=(
                radius
                if radius is not None
                else float(default_radius_km)
            ),
            location_state=location_state,
            country_wide=bool(
                data.get("country_wide")
            ),
            language_code=(
                data.get("language_code")
            ),
            verified_only=bool(
                data.get("verified_only")
            ),
            available_only=bool(
                data.get("available_only")
            ),
            premium_only=bool(
                data.get("premium_only")
            ),
            work_format=work_format,
            rating_min=rating_min,
            sort_by=(
                data.get("sort_by")
                or "distance"
            ),
            category_name=(
                data.get("category_name")
            ),
            profession_name=(
                data.get("profession_name")
            ),
            city_name=data.get("city_name"),
            search_text_query=(
                data.get("search_text_query")
            ),
        )

    async def _execute_search(
        self,
        *,
        actor: UserSearchActor,
        filters: UserSearchFilters,
        limit: int,
        offset: int,
        log_event: bool,
    ):
        common = {
            "category_id": filters.category_id,
            "profession_id": (
                filters.profession_id
            ),
            "profession_ids": list(
                filters.profession_ids
            ),
            "interface_language": (
                actor.language
            ),
            "language_code": (
                filters.language_code
            ),
            "verified_only": (
                filters.verified_only
            ),
            "premium_only": (
                filters.premium_only
            ),
            "available_only": (
                filters.available_only
            ),
            "work_format": (
                filters.work_format
            ),
            "rating_min": filters.rating_min,
            "limit": limit,
            "offset": offset,
            "requester_user_id": (
                actor.user_id
            ),
            "tenant_id": actor.tenant_id,
            "log_event": log_event,
            "sort_by": filters.sort_by,
        }

        if filters.remote_only:
            return await (
                self.geo_search
                .search_without_location(
                    **common
                )
            )

        if filters.has_geo:
            return await (
                self.geo_search.search_by_radius(
                    latitude=filters.latitude,
                    longitude=filters.longitude,
                    radius_km=filters.radius_km,
                    country_id=(
                        filters.country_id
                    ),
                    country_wide=(
                        filters.country_wide
                    ),
                    **common,
                )
            )

        if filters.city_id is not None:
            return await (
                self.geo_search.search_by_city(
                    city_id=filters.city_id,
                    country_id=(
                        filters.country_id
                    ),
                    **common,
                )
            )

        return await (
            self.geo_search
            .search_without_location(
                **common
            )
        )

    async def search_results(
        self,
        *,
        platform_user_id: int | str,
        data: Mapping[str, Any],
        page: int | str,
        fallback_language: (
            str | None
        ) = None,
        page_size: int = 5,
        default_radius_km: (
            int | float
        ) = 25,
    ) -> UserSearchPage:
        actor = await (
            self.require_registered_actor(
                platform_user_id=(
                    platform_user_id
                ),
                fallback_language=(
                    fallback_language
                ),
            )
        )
        parsed_page = (
            self.parse_nonnegative_int(
                page,
                field="results page",
            )
        )
        normalized_page_size = max(
            1,
            int(page_size),
        )
        filters = self.build_filters(
            data,
            default_radius_km=(
                default_radius_km
            ),
        )

        results = list(
            await self._execute_search(
                actor=actor,
                filters=filters,
                limit=(
                    normalized_page_size + 1
                ),
                offset=(
                    parsed_page
                    * normalized_page_size
                ),
                log_event=True,
            )
        )

        total_results = results

        if (
            len(results)
            >= normalized_page_size + 1
            or parsed_page > 0
        ):
            total_results = list(
                await self._execute_search(
                    actor=actor,
                    filters=filters,
                    limit=200,
                    offset=0,
                    log_event=False,
                )
            )

        total_count = len(total_results)
        visible_results = tuple(
            results[:normalized_page_size]
        )
        has_next = (
            (parsed_page + 1)
            * normalized_page_size
            < total_count
        )

        cabinet_ids = [
            result.professional_cabinet.id
            for result in visible_results
            if result.professional_cabinet
        ]

        saved_ids: set[UUID] = set()

        if cabinet_ids:
            saved_ids = await (
                self.favorites
                .list_saved_professional_cabinet_ids(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    professional_cabinet_ids=(
                        cabinet_ids
                    ),
                )
            )

        await (
            self.geo_search.record_results_viewed(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                event=SearchResultsViewedEvent(
                    platform_user_id=str(
                        platform_user_id
                    ),
                    page=parsed_page,
                    visible_count=len(
                        visible_results
                    ),
                    has_next=has_next,
                    category_id=(
                        str(filters.category_id)
                        if filters.category_id
                        else None
                    ),
                    profession_id=(
                        str(filters.profession_id)
                        if filters.profession_id
                        else None
                    ),
                    city_id=(
                        str(filters.city_id)
                        if filters.city_id
                        else None
                    ),
                    location_state=(
                        filters.location_state
                    ),
                    radius_km=(
                        filters.radius_km
                    ),
                    country_wide=(
                        filters.country_wide
                    ),
                    sort_by=filters.sort_by,
                    category_name=(
                        filters.category_name
                    ),
                    profession_name=(
                        filters.profession_name
                    ),
                    city_name=filters.city_name,
                    search_text_query=(
                        filters.search_text_query
                    ),
                ),
            )
        )

        if not visible_results:
            await (
                self.geo_search
                .record_empty_search(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    event=EmptySearchEvent(
                        page=parsed_page,
                        category_id=(
                            str(filters.category_id)
                            if filters.category_id
                            else None
                        ),
                        profession_id=(
                            str(
                                filters.profession_id
                            )
                            if filters.profession_id
                            else None
                        ),
                        city_id=(
                            str(filters.city_id)
                            if filters.city_id
                            else None
                        ),
                        location_state=(
                            filters.location_state
                        ),
                        radius_km=(
                            filters.radius_km
                        ),
                        country_wide=(
                            filters.country_wide
                        ),
                        language_code=(
                            filters.language_code
                        ),
                        work_format=(
                            filters.work_format
                        ),
                    ),
                )
            )

        return UserSearchPage(
            actor=actor,
            filters=filters,
            page=parsed_page,
            visible_results=visible_results,
            total_count=total_count,
            has_next=has_next,
            saved_professional_cabinet_ids=(
                frozenset(saved_ids)
            ),
        )
