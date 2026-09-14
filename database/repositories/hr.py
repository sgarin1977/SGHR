from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Application,
    Employer,
    Seeker,
    Vacancy,
)


class HrRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def get_active_employer_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Employer | None:
        result = await self.session.execute(
            select(Employer)
            .where(
                Employer.tenant_id == tenant_id,
                Employer.user_id == user_id,
                Employer.status == "active",
            )
            .limit(1)
        )

        return result.scalar_one_or_none()

    async def get_active_seeker_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Seeker | None:
        result = await self.session.execute(
            select(Seeker)
            .where(
                Seeker.tenant_id == tenant_id,
                Seeker.user_id == user_id,
                Seeker.status == "active",
            )
            .limit(1)
        )

        return result.scalar_one_or_none()

    async def list_seeker_applications(
        self,
        *,
        tenant_id: UUID,
        seeker_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Application]:
        result = await self.session.execute(
            select(Application)
            .where(
                Application.tenant_id
                == tenant_id,
                Application.seeker_id
                == seeker_id,
            )
            .order_by(
                Application.created_at.desc()
            )
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all())

    async def list_employer_vacancies(
        self,
        *,
        tenant_id: UUID,
        employer_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Vacancy]:
        result = await self.session.execute(
            select(Vacancy)
            .where(
                Vacancy.tenant_id == tenant_id,
                Vacancy.employer_id
                == employer_id,
            )
            .order_by(
                Vacancy.created_at.desc()
            )
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all())

