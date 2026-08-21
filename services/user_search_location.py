from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.repositories.geo_repository import (
    GeoRepository,
)
from services.geo_service import GeoService
from services.user_search import (
    UserSearchAction,
    UserSearchService,
)


class UserSearchLocationError(ValueError):
    pass


class UserSearchLocationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        search: UserSearchService | None = None,
        repository: GeoRepository | None = None,
        geo: GeoService | None = None,
    ):
        self.session = session
        self.search = (
            search
            or UserSearchService(session)
        )
        self.repository = (
            repository
            or GeoRepository(session)
        )
        self.geo = (
            geo
            or GeoService(self.repository)
        )

    @staticmethod
    def parse_coordinate(
        value,
        *,
        field: str,
    ) -> float:
        try:
            return float(value)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise UserSearchLocationError(
                f"Invalid {field}."
            ) from exc

    async def search_places(
        self,
        *,
        platform_user_id: int | str | None,
        query: str,
        fallback_language: (
            str | None
        ) = None,
        limit: int = 8,
    ) -> UserSearchAction:
        normalized_query = (
            query or ""
        ).strip()

        if len(normalized_query) < 2:
            raise UserSearchLocationError(
                "Location query is too short."
            )

        actor = await self.search.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )
        result = await self.geo.search_places(
            query=normalized_query,
            language=actor.language,
            limit=max(1, int(limit)),
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    async def nearby_places(
        self,
        *,
        platform_user_id: int | str | None,
        latitude,
        longitude,
        fallback_language: (
            str | None
        ) = None,
        limit: int = 4,
    ) -> UserSearchAction:
        actor = await self.search.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )
        result = await self.geo.nearby_places(
            latitude=self.parse_coordinate(
                latitude,
                field="latitude",
            ),
            longitude=self.parse_coordinate(
                longitude,
                field="longitude",
            ),
            language=actor.language,
            limit=max(1, int(limit)),
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )

    async def confirm_place(
        self,
        *,
        platform_user_id: int | str | None,
        candidate: Any,
        fallback_language: (
            str | None
        ) = None,
        source: str = "search_filter",
    ) -> UserSearchAction:
        actor = await self.search.resolve_actor(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                fallback_language
            ),
        )
        result = await (
            self.geo.confirm_search_place(
                candidate,
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                language=actor.language,
                source=source,
            )
        )

        return UserSearchAction(
            actor=actor,
            result=result,
        )
