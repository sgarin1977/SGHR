from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.api_admin import (
    AdminApiRepository,
)
from database.repositories.moderation import (
    ModerationRepository,
)
from services.api_admin_access import (
    build_api_admin_scope_context,
)
from services.api_identity import (
    ApiActorContext,
)
from services.moderation import (
    ModerationService,
)


class ApiAdminSpecialistNotFoundError(
    LookupError
):
    pass


@dataclass(frozen=True)
class ApiAdminSpecialistView:
    id: UUID
    user_id: UUID
    professional_cabinet_id: UUID | None
    category_id: UUID
    profession_id: UUID
    country_id: UUID | None
    city_id: UUID | None
    display_name: str
    status: str
    moderation_status: str | None
    is_verified: bool
    availability_status: str | None
    rating: float
    reviews_count: int
    created_at: datetime


@dataclass(frozen=True)
class ApiAdminSpecialistPage:
    items: tuple[
        ApiAdminSpecialistView,
        ...,
    ]
    page: int
    has_next: bool


class ApiAdminSpecialistsService:
    def __init__(
        self,
        *,
        repository: AdminApiRepository,
        moderation: (
            ModerationService | None
        ) = None,
    ) -> None:
        self.repository = repository
        self.moderation = moderation

    @staticmethod
    def _split_row(row):
        if hasattr(row, "id"):
            return row, None

        return row[0], row[1]

    @classmethod
    def _to_specialist_view(
        cls,
        row,
    ) -> ApiAdminSpecialistView:
        specialist, cabinet = (
            cls._split_row(row)
        )

        return ApiAdminSpecialistView(
            id=specialist.id,
            user_id=specialist.user_id,
            professional_cabinet_id=(
                cabinet.id
                if cabinet is not None
                else specialist
                .active_professional_cabinet_id
            ),
            category_id=(
                specialist.category_id
            ),
            profession_id=(
                specialist.profession_id
            ),
            country_id=(
                specialist.country_id
                or (
                    cabinet.country_id
                    if cabinet is not None
                    else None
                )
            ),
            city_id=(
                specialist.city_id
                or (
                    cabinet.city_id
                    if cabinet is not None
                    else None
                )
            ),
            display_name=(
                specialist.display_name
            ),
            status=specialist.status,
            moderation_status=(
                cabinet.moderation_status
                if cabinet is not None
                else None
            ),
            is_verified=bool(
                specialist.is_verified
            ),
            availability_status=(
                cabinet.availability_status
                if cabinet is not None
                else None
            ),
            rating=float(
                specialist.rating or 0
            ),
            reviews_count=int(
                specialist.reviews_count or 0
            ),
            created_at=(
                specialist.created_at
            ),
        )

    async def list_specialists(
        self,
        *,
        actor: ApiActorContext,
        page: int = 0,
        page_size: int = 20,
    ) -> ApiAdminSpecialistPage:
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

        rows = await (
            self.repository.list_specialists(
                scope_context=scope_context,
                limit=normalized_size + 1,
                offset=(
                    normalized_page
                    * normalized_size
                ),
            )
        )

        return ApiAdminSpecialistPage(
            items=tuple(
                self._to_specialist_view(row)
                for row in rows[
                    :normalized_size
                ]
            ),
            page=normalized_page,
            has_next=(
                len(rows) > normalized_size
            ),
        )


    async def get_specialist(
        self,
        *,
        actor: ApiActorContext,
        specialist_id: UUID,
    ) -> ApiAdminSpecialistView:
        scope_context = (
            build_api_admin_scope_context(
                actor
            )
        )

        row = await (
            self.repository.get_specialist(
                scope_context=scope_context,
                specialist_id=specialist_id,
            )
        )

        if row is None:
            raise (
                ApiAdminSpecialistNotFoundError(
                    "Admin specialist resource "
                    "not found."
                )
            )

        return self._to_specialist_view(row)


    async def approve_specialist(
        self,
        *,
        actor: ApiActorContext,
        specialist_id: UUID,
        reason: str,
    ):
        if self.moderation is None:
            raise RuntimeError(
                "Moderation service "
                "is not configured."
            )

        return await (
            self.moderation
            .approve_specialist(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                reason=reason,
                specialist_id=specialist_id,
                professional_cabinet_id=None,
            )
        )


    async def reject_specialist(
        self,
        *,
        actor: ApiActorContext,
        specialist_id: UUID,
        reason: str,
    ):
        if self.moderation is None:
            raise RuntimeError(
                "Moderation service "
                "is not configured."
            )

        return await (
            self.moderation
            .reject_specialist(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                reason=reason,
                specialist_id=specialist_id,
                professional_cabinet_id=None,
            )
        )


    async def _get_scoped_cabinet_id(
        self,
        *,
        actor: ApiActorContext,
        specialist_id: UUID,
    ) -> UUID:
        scope_context = (
            build_api_admin_scope_context(
                actor
            )
        )

        row = await (
            self.repository.get_specialist(
                scope_context=scope_context,
                specialist_id=specialist_id,
            )
        )

        if row is None:
            raise (
                ApiAdminSpecialistNotFoundError(
                    "Admin specialist resource "
                    "not found."
                )
            )

        _specialist, cabinet = (
            self._split_row(row)
        )

        if cabinet is None:
            raise (
                ApiAdminSpecialistNotFoundError(
                    "Admin specialist resource "
                    "not found."
                )
            )

        return cabinet.id

    async def hide_specialist(
        self,
        *,
        actor: ApiActorContext,
        specialist_id: UUID,
        reason: str,
    ):
        if self.moderation is None:
            raise RuntimeError(
                "Moderation service "
                "is not configured."
            )

        cabinet_id = await (
            self._get_scoped_cabinet_id(
                actor=actor,
                specialist_id=specialist_id,
            )
        )

        return await (
            self.moderation
            .hide_professional_cabinet(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                professional_cabinet_id=(
                    cabinet_id
                ),
                reason=reason,
            )
        )

    async def unhide_specialist(
        self,
        *,
        actor: ApiActorContext,
        specialist_id: UUID,
        reason: str,
    ):
        if self.moderation is None:
            raise RuntimeError(
                "Moderation service "
                "is not configured."
            )

        cabinet_id = await (
            self._get_scoped_cabinet_id(
                actor=actor,
                specialist_id=specialist_id,
            )
        )

        return await (
            self.moderation
            .restore_professional_cabinet(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                professional_cabinet_id=(
                    cabinet_id
                ),
                reason=reason,
            )
        )


def build_api_admin_specialists_service(
    session: AsyncSession,
) -> ApiAdminSpecialistsService:
    return ApiAdminSpecialistsService(
        repository=AdminApiRepository(
            session
        ),
        moderation=ModerationService(
            ModerationRepository(session)
        ),
    )
