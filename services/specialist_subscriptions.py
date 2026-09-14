from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.billing import (
    BillingRepository,
)
from services.billing import BillingService
from services.specialist_cabinets import (
    SpecialistCabinetsActor,
    SpecialistCabinetsService,
)


@dataclass(frozen=True)
class SpecialistSubscriptionsAction:
    actor: SpecialistCabinetsActor
    result: Any


class SpecialistSubscriptionsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cabinets: (
            SpecialistCabinetsService | None
        ) = None,
        subscriptions: BillingService | None = None,
    ):
        self.session = session
        self.cabinets = (
            cabinets
            or SpecialistCabinetsService(session)
        )
        self.subscriptions = (
            subscriptions
            or BillingService(
                BillingRepository(session)
            )
        )

    async def list_subscriptions_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> SpecialistSubscriptionsAction:
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
            self.subscriptions
            .list_subscriptions_for_cabinet(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                limit=max(1, int(limit)),
                offset=max(0, int(offset)),
            )
        )

        return SpecialistSubscriptionsAction(
            actor=actor,
            result=result,
        )
