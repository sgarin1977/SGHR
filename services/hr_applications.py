from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from database.repositories.hr import (
    HrRepository,
)


class HrApplicationAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class HrApplicationListItem:
    id: UUID
    vacancy_id: UUID
    message: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class HrApplicationService:
    def __init__(
        self,
        repository: HrRepository,
    ):
        self.repository = repository

    async def list_applications_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[HrApplicationListItem, ...]:
        seeker = await (
            self.repository
            .get_active_seeker_for_user(
                tenant_id=tenant_id,
                user_id=user_id,
            )
        )

        if seeker is None:
            raise HrApplicationAccessError(
                "Active seeker was not found."
            )

        rows = await (
            self.repository
            .list_seeker_applications(
                tenant_id=tenant_id,
                seeker_id=seeker.id,
                limit=max(1, int(limit)),
                offset=max(0, int(offset)),
            )
        )

        return tuple(
            HrApplicationListItem(
                id=row.id,
                vacancy_id=row.vacancy_id,
                message=row.message,
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        )


def build_hr_application_service(
    session,
) -> HrApplicationService:
    return HrApplicationService(
        HrRepository(session)
    )
