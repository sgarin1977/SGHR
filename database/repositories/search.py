from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Specialist


@dataclass
class SpecialistSearchFilters:
    city_id: UUID | None = None
    category_id: UUID | None = None
    profession_id: UUID | None = None
    status: str = "active"
    limit: int = 10
    offset: int = 0


class SpecialistSearchRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def search_specialists(
        self,
        filters: SpecialistSearchFilters,
    ) -> list[Specialist]:
        stmt = select(Specialist).where(Specialist.status == filters.status)

        if filters.city_id:
            stmt = stmt.where(Specialist.city_id == filters.city_id)

        if filters.category_id:
            stmt = stmt.where(Specialist.category_id == filters.category_id)

        if filters.profession_id:
            stmt = stmt.where(Specialist.profession_id == filters.profession_id)

        stmt = (
            stmt.order_by(
                Specialist.is_premium.desc(),
                Specialist.priority_score.desc(),
                Specialist.rating.desc(),
                Specialist.created_at.desc(),
            )
            .offset(filters.offset)
            .limit(filters.limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_with_coordinates(
        self,
        *,
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        limit: int = 200,
    ) -> list[Specialist]:
        stmt = select(Specialist).where(
            Specialist.status == "active",
            Specialist.latitude.isnot(None),
            Specialist.longitude.isnot(None),
        )

        if category_id:
            stmt = stmt.where(Specialist.category_id == category_id)

        if profession_id:
            stmt = stmt.where(Specialist.profession_id == profession_id)

        stmt = stmt.order_by(
            Specialist.is_premium.desc(),
            Specialist.priority_score.desc(),
            Specialist.rating.desc(),
            Specialist.created_at.desc(),
        ).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())