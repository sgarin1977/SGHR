from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.api_admin import (
    AdminApiRepository,
)
from services.api_admin_access import (
    build_api_admin_scope_context,
)
from services.api_identity import (
    ApiActorContext,
)


class ApiAdminUserNotFoundError(
    LookupError
):
    pass


@dataclass(frozen=True)
class ApiAdminUserView:
    id: UUID
    active_role: str | None
    language_code: str
    country_id: UUID | None
    city_id: UUID | None
    status: str
    last_seen_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class ApiAdminUserPage:
    items: tuple[ApiAdminUserView, ...]
    page: int
    has_next: bool


class ApiAdminUsersService:
    def __init__(
        self,
        *,
        repository: AdminApiRepository,
    ) -> None:
        self.repository = repository

    @staticmethod
    def _to_user_view(
        row,
    ) -> ApiAdminUserView:
        user = (
            row
            if hasattr(row, "id")
            else row[0]
        )

        return ApiAdminUserView(
            id=user.id,
            active_role=user.active_role,
            language_code=user.language_code,
            country_id=user.country_id,
            city_id=user.city_id,
            status=user.status,
            last_seen_at=user.last_seen_at,
            created_at=user.created_at,
        )

    async def list_users(
        self,
        *,
        actor: ApiActorContext,
        page: int = 0,
        page_size: int = 20,
    ) -> ApiAdminUserPage:
        normalized_page = max(
            0,
            int(page),
        )
        normalized_size = max(
            1,
            min(int(page_size), 100),
        )

        scope_context = (
            build_api_admin_scope_context(
                actor
            )
        )

        rows = await self.repository.list_users(
            scope_context=scope_context,
            limit=normalized_size + 1,
            offset=(
                normalized_page
                * normalized_size
            ),
        )

        return ApiAdminUserPage(
            items=tuple(
                self._to_user_view(row)
                for row in rows[
                    :normalized_size
                ]
            ),
            page=normalized_page,
            has_next=(
                len(rows) > normalized_size
            ),
        )


    async def get_user(
        self,
        *,
        actor: ApiActorContext,
        user_id: UUID,
    ) -> ApiAdminUserView:
        scope_context = (
            build_api_admin_scope_context(
                actor
            )
        )

        user = await self.repository.get_user(
            scope_context=scope_context,
            user_id=user_id,
        )

        if user is None:
            raise ApiAdminUserNotFoundError(
                "Admin user resource not found."
            )

        return self._to_user_view(user)



def build_api_admin_users_service(
    session: AsyncSession,
) -> ApiAdminUsersService:
    return ApiAdminUsersService(
        repository=AdminApiRepository(
            session
        ),
    )
