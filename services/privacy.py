import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from database.models import DataSubjectRequest, DeletionJob, Specialist
from database.repositories.privacy import PrivacyRepository
from database.repositories.event import EventRepository
import os
import secrets

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


    async def schedule_profile_deletion(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID | None = None,
        source: str = "privacy_settings",
    ) -> DeletionJob:
        try:
            job = (
                await self.repository
                .schedule_profile_deletion(
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
            )

            if specialist_id is not None:
                await EventRepository(
                    self.repository.session
                ).create_event(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    event_type="profile_action",
                    entity_type="specialist",
                    entity_id=specialist_id,
                    payload={
                        "action": (
                            "delete_requested"
                        ),
                        "source": source,
                    },
                    platform="telegram",
                )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return job
    
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

    async def process_scheduled_deletions(
        self,
        *,
        limit: int = 20,
    ) -> PrivacyJobResult:
        processed_count = 0
        failed_count = 0
        job_limit = max(
            1,
            min(int(limit), 100),
        )

        for _ in range(job_limit):
            job = await (
                self.repository
                .get_next_scheduled_deletion_job()
            )

            if not job:
                await (
                    self.repository
                    .session
                    .rollback()
                )
                break

            job_id = job.id

            try:
                await (
                    self.repository
                    .mark_deletion_job_processing(
                        job
                    )
                )
                await (
                    self.repository
                    .anonymize_user_for_deletion(
                        job=job,
                    )
                )
                await (
                    self.repository
                    .session
                    .commit()
                )

                processed_count += 1
            except Exception as exc:
                failed_count += 1
                await (
                    self.repository
                    .session
                    .rollback()
                )

                failed_job = await (
                    self.repository
                    .session
                    .get(
                        DeletionJob,
                        job_id,
                    )
                )

                if failed_job:
                    await (
                        self.repository
                        .mark_deletion_job_failed(
                            job=failed_job,
                            error_message=str(exc),
                        )
                    )
                    await (
                        self.repository
                        .session
                        .commit()
                    )

        return PrivacyJobResult(
            processed_count=processed_count,
            failed_count=failed_count,
        )

    def _write_secure_export(
        self,
        *,
        export_dir: str | Path,
        request_id: object,
        export_data: dict,
    ) -> Path:
        export_path = Path(export_dir)
        export_path.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )
        export_path.chmod(0o700)

        file_path = export_path / (
            f"dsr_export_{request_id}_"
            f"{secrets.token_urlsafe(18)}.json"
        )

        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
        )

        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        descriptor = os.open(
            file_path,
            flags,
            0o600,
        )
        export_file = None

        try:
            export_file = os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
            )

            with export_file:
                json.dump(
                    export_data,
                    export_file,
                    ensure_ascii=False,
                    indent=2,
                    default=self._json_default,
                )

            file_path.chmod(0o600)
        except Exception:
            if export_file is None:
                os.close(descriptor)

            file_path.unlink(
                missing_ok=True
            )
            raise

        return file_path

    def _cleanup_expired_exports(
        self,
        *,
        export_dir: str | Path,
        retention_seconds: int,
        now_timestamp: float | None = None,
    ) -> int:
        export_path = Path(export_dir)

        if not export_path.exists():
            return 0

        current_timestamp = (
            now_timestamp
            if now_timestamp is not None
            else datetime.now().timestamp()
        )
        retention = max(
            1,
            int(retention_seconds),
        )
        removed_count = 0

        for file_path in export_path.glob(
            "dsr_export_*.json"
        ):
            try:
                modified_at = file_path.stat(
                    follow_symlinks=False
                ).st_mtime
            except FileNotFoundError:
                continue

            if (
                current_timestamp - modified_at
                < retention
            ):
                continue

            try:
                file_path.unlink(
                    missing_ok=True
                )
            except FileNotFoundError:
                continue

            removed_count += 1

        return removed_count

    async def process_requested_data_exports(
        self,
        *,
        export_dir: str | Path = "exports/data_subject_requests",
        limit: int = 20,
        retention_seconds: int = (
            7 * 24 * 60 * 60
        ),
    ) -> PrivacyJobResult:
        self._cleanup_expired_exports(
            export_dir=export_dir,
            retention_seconds=retention_seconds,
        )

        processed_count = 0
        failed_count = 0
        request_limit = max(
            1,
            min(int(limit), 100),
        )

        for _ in range(request_limit):
            request = await (
                self.repository
                .get_next_requested_data_export()
            )

            if not request:
                await (
                    self.repository
                    .session
                    .rollback()
                )
                break

            request_id = request.id
            file_path = None

            try:
                request.status = "processing"
                await (
                    self.repository
                    .session
                    .flush()
                )

                export_data = await (
                    self.repository
                    .collect_user_export_data(
                        request=request,
                    )
                )
                file_path = (
                    self._write_secure_export(
                        export_dir=export_dir,
                        request_id=request.id,
                        export_data=export_data,
                    )
                )

                await (
                    self.repository
                    .mark_data_export_completed(
                        request=request,
                        result_comment=(
                            "Export prepared: "
                            f"{file_path}"
                        ),
                    )
                )
                await (
                    self.repository
                    .session
                    .commit()
                )

                processed_count += 1
            except Exception as exc:
                failed_count += 1
                await (
                    self.repository
                    .session
                    .rollback()
                )

                if file_path is not None:
                    try:
                        file_path.unlink(
                            missing_ok=True
                        )
                    except OSError:
                        pass

                failed_request = await (
                    self.repository
                    .session
                    .get(
                        DataSubjectRequest,
                        request_id,
                    )
                )

                if failed_request:
                    await (
                        self.repository
                        .mark_data_export_failed(
                            request=failed_request,
                            error_message=str(exc),
                        )
                    )
                    await (
                        self.repository
                        .session
                        .commit()
                    )

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
