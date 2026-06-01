from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    DataSubjectRequest,
    DeletionJob,
    EventLog,
    Specialist,
    SpecialistLocation,
    UserLocation,
)


class PrivacyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_specialist_by_user_id(self, user_id: UUID) -> Specialist | None:
        result = await self.session.execute(
            select(Specialist).where(Specialist.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def pause_specialist_profile(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        platform: str = "telegram",
    ) -> Specialist | None:
        specialist = await self.get_specialist_by_user_id(user_id)
        if not specialist:
            return None

        before_status = specialist.status
        specialist.status = "paused"
        specialist.updated_at = datetime.utcnow()

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="specialist_profile_paused",
                entity_type="specialist",
                entity_id=specialist.id,
                platform=platform,
                payload={
                    "before_status": before_status,
                    "after_status": "paused",
                },
            )
        )

        await self.session.commit()
        return specialist

    async def schedule_profile_deletion(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        platform: str = "telegram",
    ) -> DeletionJob:
        existing_result = await self.session.execute(
            select(DeletionJob)
            .where(
                DeletionJob.tenant_id == tenant_id,
                DeletionJob.user_id == user_id,
                DeletionJob.status.in_(["scheduled", "processing"]),
            )
            .order_by(DeletionJob.scheduled_at.desc())
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            return existing

        job = DeletionJob(
            tenant_id=tenant_id,
            user_id=user_id,
            status="scheduled",
            anonymization_report={},
            scheduled_at=datetime.utcnow(),
        )
        self.session.add(job)
        await self.session.flush()

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="deletion_job_scheduled",
                entity_type="deletion_job",
                entity_id=job.id,
                platform=platform,
                payload={},
            )
        )

        await self.session.commit()
        return job

    async def request_data_export(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        platform: str = "telegram",
    ) -> DataSubjectRequest:
        request = DataSubjectRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            request_type="export_data",
            status="requested",
            requested_at=datetime.utcnow(),
        )
        self.session.add(request)
        await self.session.flush()

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="data_export_requested",
                entity_type="data_subject_request",
                entity_id=request.id,
                platform=platform,
                payload={
                    "request_type": "export_data",
                },
            )
        )

        await self.session.commit()
        return request

    async def clear_user_geo(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        platform: str = "telegram",
    ) -> int:
        specialist = await self.get_specialist_by_user_id(user_id)

        deleted_user_locations = await self.session.execute(
            delete(UserLocation).where(
                UserLocation.tenant_id == tenant_id,
                UserLocation.user_id == user_id,
            )
        )

        deleted_specialist_locations_count = 0
        if specialist:
            deleted_specialist_locations = await self.session.execute(
                delete(SpecialistLocation).where(
                    SpecialistLocation.tenant_id == tenant_id,
                    SpecialistLocation.specialist_id == specialist.id,
                )
            )
            deleted_specialist_locations_count = deleted_specialist_locations.rowcount or 0

            specialist.country_id = None
            specialist.city_id = None
            specialist.latitude = None
            specialist.longitude = None
            specialist.service_radius_km = 0
            specialist.updated_at = datetime.utcnow()

        deleted_count = (deleted_user_locations.rowcount or 0) + deleted_specialist_locations_count

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="geo_deleted",
                entity_type="user",
                entity_id=user_id,
                platform=platform,
                payload={
                    "deleted_locations": deleted_count,
                    "specialist_id": str(specialist.id) if specialist else None,
                },
            )
        )

        await self.session.commit()
        return deleted_count