from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.specialist import (
    SpecialistRepository,
)
from services.specialist import (
    SpecialistService,
    SpecialistSkillsEditData,
)
from services.specialist_cabinets import (
    SpecialistCabinetsActor,
    SpecialistCabinetsService,
)


@dataclass(frozen=True)
class SpecialistSkillsAction:
    actor: SpecialistCabinetsActor
    result: SpecialistSkillsEditData


class SpecialistSkillsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cabinets: (
            SpecialistCabinetsService | None
        ) = None,
        specialists: SpecialistService | None = None,
    ):
        self.session = session
        self.cabinets = (
            cabinets
            or SpecialistCabinetsService(session)
        )
        self.specialists = (
            specialists
            or SpecialistService(
                SpecialistRepository(session)
            )
        )

    async def list_skills_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        limit: int = 30,
    ) -> SpecialistSkillsAction:
        cabinet_action = await (
            self.cabinets
            .require_owned_cabinet_for_user(
                user_id=user_id,
                tenant_id=tenant_id,
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )
        actor = cabinet_action.actor

        result = await (
            self.specialists
            .get_skills_for_cabinet(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                language=actor.language,
                limit=max(1, int(limit)),
            )
        )

        return SpecialistSkillsAction(
            actor=actor,
            result=result,
        )

    async def update_skills_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        skill_ids: list[UUID],
        platform: str = "telegram",
    ) -> SpecialistSkillsAction:
        cabinet_action = await (
            self.cabinets
            .require_owned_cabinet_for_user(
                user_id=user_id,
                tenant_id=tenant_id,
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )
        actor = cabinet_action.actor

        result = await (
            self.specialists
            .update_skills_for_cabinet(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                skill_ids=list(skill_ids),
                platform=platform,
            )
        )

        return SpecialistSkillsAction(
            actor=actor,
            result=result,
        )

