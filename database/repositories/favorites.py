from dataclasses import dataclass
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


@dataclass(frozen=True)
class SavedProfessionalCabinetRow:
    specialist: Specialist
    professional_cabinet: ProfessionalCabinet


class FavoriteRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def get_public_professional_cabinet(
        self,
        *,
        tenant_id: UUID,
        professional_cabinet_id: UUID,
    ) -> tuple[
        Specialist,
        ProfessionalCabinet,
    ] | None:
        result = await self.session.execute(
            select(
                Specialist,
                ProfessionalCabinet,
            )
            .select_from(
                ProfessionalCabinet
            )
            .join(
                Specialist,
                Specialist.id
                == ProfessionalCabinet.specialist_id,
            )
            .join(
                User,
                User.id == Specialist.user_id,
            )
            .where(
                ProfessionalCabinet.id
                == professional_cabinet_id,
                ProfessionalCabinet.tenant_id
                == tenant_id,
                ProfessionalCabinet.is_active.is_(
                    True
                ),
                ProfessionalCabinet.moderation_status.in_(
                    PUBLIC_CABINET_MODERATION_STATUSES
                ),
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
            )
        )

        row = result.first()

        if not row:
            return None

        specialist, cabinet = row
        return specialist, cabinet

    async def get_saved_professional_cabinet(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
    ) -> SavedSpecialist | None:
        result = await self.session.execute(
            select(
                SavedSpecialist
            ).where(
                SavedSpecialist.tenant_id
                == tenant_id,
                SavedSpecialist.user_id
                == user_id,
                SavedSpecialist.professional_cabinet_id
                == professional_cabinet_id,
            )
        )

        return result.scalar_one_or_none()

    async def is_saved(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
    ) -> bool:
        saved = await (
            self.get_saved_professional_cabinet(
                tenant_id=tenant_id,
                user_id=user_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        return saved is not None

    async def list_saved_professional_cabinet_ids(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_ids: list[UUID],
    ) -> set[UUID]:
        if not professional_cabinet_ids:
            return set()

        result = await self.session.execute(
            select(
                SavedSpecialist
                .professional_cabinet_id
            ).where(
                SavedSpecialist.tenant_id
                == tenant_id,
                SavedSpecialist.user_id
                == user_id,
                SavedSpecialist
                .professional_cabinet_id.in_(
                    professional_cabinet_ids
                ),
            )
        )

        return set(
            result.scalars().all()
        )

    async def save_professional_cabinet(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
    ) -> bool:
        context = await (
            self.get_public_professional_cabinet(
                tenant_id=tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        if not context:
            raise ValueError(
                "Professional cabinet is not available."
            )

        specialist, cabinet = context

        saved = await (
            self.get_saved_professional_cabinet(
                tenant_id=tenant_id,
                user_id=user_id,
                professional_cabinet_id=(
                    cabinet.id
                ),
            )
        )

        if saved:
            return False

        self.session.add(
            SavedSpecialist(
                tenant_id=tenant_id,
                user_id=user_id,
                professional_cabinet_id=(
                    cabinet.id
                ),
                specialist_id=specialist.id,
            )
        )
        await self.session.flush()

        return True

    async def toggle_professional_cabinet(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
    ) -> bool:
        context = await (
            self.get_public_professional_cabinet(
                tenant_id=tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        if not context:
            raise ValueError(
                "Professional cabinet is not available."
            )

        specialist, cabinet = context

        saved = await (
            self.get_saved_professional_cabinet(
                tenant_id=tenant_id,
                user_id=user_id,
                professional_cabinet_id=(
                    cabinet.id
                ),
            )
        )

        if saved:
            await self.session.execute(
                delete(
                    SavedSpecialist
                ).where(
                    SavedSpecialist.id
                    == saved.id
                )
            )
            await self.session.flush()
            return False

        self.session.add(
            SavedSpecialist(
                tenant_id=tenant_id,
                user_id=user_id,
                professional_cabinet_id=(
                    cabinet.id
                ),
                specialist_id=specialist.id,
            )
        )
        await self.session.flush()

        return True

    async def list_saved_professional_cabinets(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> list[
        SavedProfessionalCabinetRow
    ]:
        result = await self.session.execute(
            select(
                Specialist,
                ProfessionalCabinet,
            )
            .select_from(
                SavedSpecialist
            )
            .join(
                ProfessionalCabinet,
                ProfessionalCabinet.id
                == SavedSpecialist
                .professional_cabinet_id,
            )
            .join(
                Specialist,
                Specialist.id
                == ProfessionalCabinet.specialist_id,
            )
            .join(
                User,
                User.id == Specialist.user_id,
            )
            .where(
                SavedSpecialist.tenant_id
                == tenant_id,
                SavedSpecialist.user_id
                == user_id,
                ProfessionalCabinet.tenant_id
                == tenant_id,
                ProfessionalCabinet.is_active.is_(
                    True
                ),
                ProfessionalCabinet.moderation_status.in_(
                    PUBLIC_CABINET_MODERATION_STATUSES
                ),
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
            )
            .order_by(
                SavedSpecialist.created_at.desc()
            )
            .limit(limit)
            .offset(offset)
        )

        return [
            SavedProfessionalCabinetRow(
                specialist=specialist,
                professional_cabinet=cabinet,
            )
            for specialist, cabinet
            in result.all()
        ]

    async def remove_professional_cabinet(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
    ) -> bool:
        saved = await (
            self.get_saved_professional_cabinet(
                tenant_id=tenant_id,
                user_id=user_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        if not saved:
            return False

        await self.session.execute(
            delete(
                SavedSpecialist
            ).where(
                SavedSpecialist.id
                == saved.id
            )
        )
        await self.session.flush()

        return True