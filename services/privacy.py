from dataclasses import dataclass
from uuid import UUID

from database.models import DataSubjectRequest, DeletionJob, Specialist
from database.repositories.privacy import PrivacyRepository


class PrivacyError(Exception):
    pass


@dataclass(frozen=True)
class PrivacyActionResult:
    status: str
    message_key: str


class PrivacyService:
    def __init__(self, repository: PrivacyRepository):
        self.repository = repository

    async def hide_specialist_profile(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Specialist:
        specialist = await self.repository.pause_specialist_profile(
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if not specialist:
            raise PrivacyError("Specialist profile not found.")

        return specialist

    async def schedule_profile_deletion(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> DeletionJob:
        return await self.repository.schedule_profile_deletion(
            tenant_id=tenant_id,
            user_id=user_id,
        )

    async def request_data_export(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> DataSubjectRequest:
        return await self.repository.request_data_export(
            tenant_id=tenant_id,
            user_id=user_id,
        )

    async def delete_geo_data(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> int:
        return await self.repository.clear_user_geo(
            tenant_id=tenant_id,
            user_id=user_id,
        )