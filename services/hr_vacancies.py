from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from database.repositories.hr import (
    HrRepository,
)


class HrVacancyAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class HrVacancyListItem:
    id: UUID
    profession_id: UUID | None
    country_id: UUID | None
    city_id: UUID | None
    title: str
    description: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    currency: str
    employment_type: str | None
    work_format: str | None
    recruitment_mode: str
    status: str
    published_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class HrVacancyService:
    def __init__(
        self,
        repository: HrRepository,
    ):
        self.repository = repository

    async def list_vacancies_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[HrVacancyListItem, ...]:
        employer = await (
            self.repository
            .get_active_employer_for_user(
                tenant_id=tenant_id,
                user_id=user_id,
            )
        )

        if employer is None:
            raise HrVacancyAccessError(
                "Active employer was not found."
            )

        rows = await (
            self.repository
            .list_employer_vacancies(
                tenant_id=tenant_id,
                employer_id=employer.id,
                limit=max(1, int(limit)),
                offset=max(0, int(offset)),
            )
        )

        return tuple(
            HrVacancyListItem(
                id=row.id,
                profession_id=(
                    row.profession_id
                ),
                country_id=row.country_id,
                city_id=row.city_id,
                title=row.title,
                description=row.description,
                salary_min=(
                    Decimal(str(row.salary_min))
                    if row.salary_min is not None
                    else None
                ),
                salary_max=(
                    Decimal(str(row.salary_max))
                    if row.salary_max is not None
                    else None
                ),
                currency=row.currency,
                employment_type=(
                    row.employment_type
                ),
                work_format=row.work_format,
                recruitment_mode=(
                    row.recruitment_mode
                ),
                status=row.status,
                published_at=(
                    row.published_at
                ),
                expires_at=row.expires_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        )


def build_hr_vacancy_service(
    session,
) -> HrVacancyService:
    return HrVacancyService(
        HrRepository(session)
    )

