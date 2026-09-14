from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.contact import (
    ContactChatRepository,
)
from services.contact_chat import (
    ContactChatService,
)
from services.specialist_cabinets import (
    SpecialistCabinetsActor,
    SpecialistCabinetsService,
)


@dataclass(frozen=True)
class SpecialistClientsAction:
    actor: SpecialistCabinetsActor
    result: Any


class SpecialistClientsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cabinets: (
            SpecialistCabinetsService | None
        ) = None,
        clients: ContactChatService | None = None,
    ):
        self.session = session
        self.cabinets = (
            cabinets
            or SpecialistCabinetsService(session)
        )
        self.clients = (
            clients
            or ContactChatService(
                ContactChatRepository(session)
            )
        )

    async def list_clients_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> SpecialistClientsAction:
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
            self.clients
            .list_specialist_clients_for_cabinet(
                tenant_id=actor.tenant_id,
                specialist_id=(
                    actor.specialist_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                limit=max(1, int(limit)),
                offset=max(0, int(offset)),
            )
        )

        return SpecialistClientsAction(
            actor=actor,
            result=result,
        )
