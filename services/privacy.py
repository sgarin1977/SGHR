import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from database.models import DataSubjectRequest, DeletionJob, Specialist
from database.repositories.privacy import PrivacyRepository


class PrivacyError(Exception):
    pass


@dataclass(frozen=True)
class PrivacyActionResult:
    status: str
    message_key: str


@dataclass(frozen=True)
class PrivacyJobResult:
    processed_count: int
    failed_count: int


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

    async def process_scheduled_deletions(self, *, limit: int = 20) -> PrivacyJobResult:
        jobs = await self.repository.list_scheduled_deletion_jobs(limit=limit)

        processed_count = 0
        failed_count = 0

        for job in jobs:
            job_id = job.id

            try:
                await self.repository.mark_deletion_job_processing(job)
                await self.repository.session.commit()

                await self.repository.anonymize_user_for_deletion(
                    job=job,
                )
                await self.repository.session.commit()

                processed_count += 1
            except Exception as exc:
                failed_count += 1
                await self.repository.session.rollback()

                failed_job = await self.repository.session.get(DeletionJob, job_id)
                if failed_job:
                    await self.repository.mark_deletion_job_failed(
                        job=failed_job,
                        error_message=str(exc),
                    )
                    await self.repository.session.commit()

        return PrivacyJobResult(
            processed_count=processed_count,
            failed_count=failed_count,
        )

    async def process_requested_data_exports(
        self,
        *,
        export_dir: str | Path = "exports/data_subject_requests",
        limit: int = 20,
    ) -> PrivacyJobResult:
        requests = await self.repository.list_requested_data_exports(limit=limit)

        processed_count = 0
        failed_count = 0
        export_path = Path(export_dir)
        export_path.mkdir(parents=True, exist_ok=True)

        for request in requests:
            request_id = request.id

            try:
                request.status = "processing"
                await self.repository.session.commit()

                export_data = await self.repository.collect_user_export_data(
                    request=request,
                )
                file_path = export_path / f"dsr_export_{request.id}.json"

                file_path.write_text(
                    json.dumps(
                        export_data,
                        ensure_ascii=False,
                        indent=2,
                        default=self._json_default,
                    ),
                    encoding="utf-8",
                )

                await self.repository.mark_data_export_completed(
                    request=request,
                    result_comment=f"Export prepared: {file_path}",
                )
                await self.repository.session.commit()

                processed_count += 1
            except Exception as exc:
                failed_count += 1
                await self.repository.session.rollback()

                failed_request = await self.repository.session.get(
                    DataSubjectRequest,
                    request_id,
                )
                if failed_request:
                    await self.repository.mark_data_export_failed(
                        request=failed_request,
                        error_message=str(exc),
                    )
                    await self.repository.session.commit()

        return PrivacyJobResult(
            processed_count=processed_count,
            failed_count=failed_count,
        )

    @staticmethod
    def _json_default(value: Any) -> str | int | float | bool | None:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)

        return str(value)