from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SavedSpecialist, Specialist


class FavoriteRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_saved_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> SavedSpecialist | None:
        result = await self.session.execute(
            select(SavedSpecialist).where(
                SavedSpecialist.tenant_id == tenant_id,
                SavedSpecialist.user_id == user_id,
                SavedSpecialist.specialist_id == specialist_id,
            )
        )
        return result.scalar_one_or_none()

    async def is_saved(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        saved = await self.get_saved_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )
        return saved is not None

    async def toggle_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.tenant_id != tenant_id or specialist.status != "active":
            raise ValueError("Specialist is not available.")

        saved = await self.get_saved_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )

        if saved:
            await self.session.execute(
                delete(SavedSpecialist).where(SavedSpecialist.id == saved.id)
            )
            await self.session.commit()
            return False

        self.session.add(
            SavedSpecialist(
                tenant_id=tenant_id,
                user_id=user_id,
                specialist_id=specialist_id,
            )
        )
        await self.session.commit()
        return True
    
    async def list_saved_specialists(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Specialist]:
        result = await self.session.execute(
            select(Specialist)
            .join(
                SavedSpecialist,
                SavedSpecialist.specialist_id == Specialist.id,
            )
            .where(
                SavedSpecialist.tenant_id == tenant_id,
                SavedSpecialist.user_id == user_id,
                Specialist.tenant_id == tenant_id,
                Specialist.status == "active",
            )
            .order_by(SavedSpecialist.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def remove_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        saved = await self.get_saved_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )

        if not saved:
            return False

        await self.session.execute(
            delete(SavedSpecialist).where(SavedSpecialist.id == saved.id)
        )
        await self.session.commit()
        return True