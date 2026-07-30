from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ProfessionalCabinet,
    SavedSpecialist,
    Specialist,
    User,
)
from database.repositories.search import (
    PUBLIC_CABINET_MODERATION_STATUSES,
)

class FavoriteRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def _get_public_specialist(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
    ) -> Specialist | None:
        result = await self.session.execute(
            select(
                Specialist
            )
            .join(
                User,
                User.id == Specialist.user_id,
            )
            .join(
                ProfessionalCabinet,
                ProfessionalCabinet.id
                == Specialist.active_professional_cabinet_id,
            )
            .where(
                Specialist.id == specialist_id,
                Specialist.tenant_id == tenant_id,
                Specialist.status
                != "deleted",
                User.tenant_id == tenant_id,
                User.status.notin_(
                    [
                        "blocked",
                        "deleted",
                    ]
                ),
                ProfessionalCabinet.tenant_id
                == tenant_id,
                ProfessionalCabinet.specialist_id
                == Specialist.id,
                ProfessionalCabinet.is_active.is_(
                    True
                ),
                ProfessionalCabinet.moderation_status.in_(
                    PUBLIC_CABINET_MODERATION_STATUSES
                ),
            )
        )

        return result.scalar_one_or_none()

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

    async def list_saved_specialist_ids(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_ids: list[UUID],
    ) -> set[UUID]:
        if not specialist_ids:
            return set()

        result = await self.session.execute(
            select(
                SavedSpecialist.specialist_id
            ).where(
                SavedSpecialist.tenant_id == tenant_id,
                SavedSpecialist.user_id == user_id,
                SavedSpecialist.specialist_id.in_(
                    specialist_ids
                ),
            )
        )

        return set(result.scalars().all())

    async def save_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        specialist = await self._get_public_specialist(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
        )

        if not specialist:
            raise ValueError(
                "Specialist is not available."
            )

        saved = await self.get_saved_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )
        if saved:
            return False

        self.session.add(
            SavedSpecialist(
                tenant_id=tenant_id,
                user_id=user_id,
                specialist_id=specialist_id,
            )
        )
        await self.session.flush()

        return True

    async def toggle_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        specialist = await self._get_public_specialist(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
        )

        if not specialist:
            raise ValueError(
                "Specialist is not available."
            )

        saved = await self.get_saved_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )

        if saved:
            await self.session.execute(
                delete(SavedSpecialist).where(
                    SavedSpecialist.id == saved.id
                )
            )
            await self.session.flush()

            return False

        self.session.add(
            SavedSpecialist(
                tenant_id=tenant_id,
                user_id=user_id,
                specialist_id=specialist_id,
            )
        )
        await self.session.flush()

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
            select(
                Specialist
            )
            .join(
                SavedSpecialist,
                SavedSpecialist.specialist_id
                == Specialist.id,
            )
            .join(
                User,
                User.id == Specialist.user_id,
            )
            .join(
                ProfessionalCabinet,
                ProfessionalCabinet.id
                == Specialist.active_professional_cabinet_id,
            )
            .where(
                SavedSpecialist.tenant_id
                == tenant_id,
                SavedSpecialist.user_id
                == user_id,
                Specialist.tenant_id
                == tenant_id,
                Specialist.status
                != "deleted",
                User.tenant_id
                == tenant_id,
                User.status.notin_(
                    [
                        "blocked",
                        "deleted",
                    ]
                ),
                ProfessionalCabinet.tenant_id
                == tenant_id,
                ProfessionalCabinet.specialist_id
                == Specialist.id,
                ProfessionalCabinet.is_active.is_(
                    True
                ),
                ProfessionalCabinet.moderation_status.in_(
                    PUBLIC_CABINET_MODERATION_STATUSES
                ),
            )
            .order_by(
                SavedSpecialist.created_at.desc()
            )
            .limit(limit)
            .offset(offset)
        )

        return list(
            result.scalars().all()
        )

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
            delete(SavedSpecialist).where(
                SavedSpecialist.id == saved.id
            )
        )
        await self.session.flush()

        return True