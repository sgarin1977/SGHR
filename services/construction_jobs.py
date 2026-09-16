from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping
from database.models import (
    ConstructionBackgroundJobRecord,
)
from database.repositories.job_queue import (
    PostgresJobQueue,
)
from services.job_queue import JobQueue
from uuid import UUID


class ConstructionJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    READY = "READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ConstructionJobType(str, Enum):
    EXPORT_PDF = "EXPORT_PDF"
    EXPORT_XLSX = "EXPORT_XLSX"
    BULK_IMPORT = "BULK_IMPORT"
    SUPPLIER_SYNC = "SUPPLIER_SYNC"
    THUMBNAIL_GENERATION = (
        "THUMBNAIL_GENERATION"
    )
    VOICE_TRANSCRIPTION = (
        "VOICE_TRANSCRIPTION"
    )
    RETAIL_PRICE_SEARCH = (
        "RETAIL_PRICE_SEARCH"
    )


@dataclass(frozen=True)
class ConstructionBackgroundJob:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    resource_type: str
    resource_id: UUID
    job_type: ConstructionJobType
    status: ConstructionJobStatus
    idempotency_key: str
    attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    progress: int
    error_code: str | None
    result_reference: str | None



def build_construction_job_queue(
    *,
    session,
) -> JobQueue[ConstructionBackgroundJob]:
    return PostgresJobQueue(
        session,
        model=ConstructionBackgroundJobRecord,
        idempotency_columns=(
            "tenant_id",
            "user_id",
            "job_type",
            "idempotency_key",
        ),
        idempotency_payload_columns=(
            "resource_type",
            "resource_id",
        ),
    )


ConstructionJobQueue = JobQueue


class ConstructionBackgroundJobDispatcher:
    def __init__(
        self,
        *,
        queue: JobQueue[ConstructionBackgroundJob],
    ) -> None:
        self.queue = queue

    async def enqueue(
        self,
        *,
        job: ConstructionBackgroundJob,
    ) -> UUID:
        return await self.queue.enqueue(
            job=job,
        )


ConstructionJobHandler = Callable[
    [Any],
    Awaitable[str],
]


class ConstructionJobRetryError(RuntimeError):
    def __init__(
        self,
        *,
        error_code: str,
        available_at: datetime,
    ) -> None:
        normalized_error_code = (
            error_code.strip()
            if isinstance(error_code, str)
            else ""
        )

        if not normalized_error_code:
            raise ValueError(
                "Construction job retry error code "
                "is required."
            )

        if (
            available_at.tzinfo is None
            or available_at.utcoffset() is None
        ):
            raise ValueError(
                "Construction job retry time "
                "must be timezone-aware."
            )

        self.error_code = normalized_error_code
        self.available_at = available_at

        super().__init__(
            "Construction job retry requested."
        )



class ConstructionJobPermanentError(RuntimeError):
    def __init__(
        self,
        *,
        error_code: str,
    ) -> None:
        normalized_error_code = (
            error_code.strip()
            if isinstance(error_code, str)
            else ""
        )

        if not normalized_error_code:
            raise ValueError(
                "Construction job failure error code "
                "is required."
            )

        self.error_code = normalized_error_code

        super().__init__(
            "Construction job permanently failed."
        )


class ConstructionBackgroundJobWorker:
    def __init__(
        self,
        *,
        queue: JobQueue[Any],
        handlers: Mapping[
            str,
            ConstructionJobHandler,
        ],
        lease_owner: str,
        lease_duration: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        normalized_lease_owner = (
            lease_owner.strip()
            if isinstance(lease_owner, str)
            else ""
        )

        if not normalized_lease_owner:
            raise ValueError(
                "Construction worker lease owner "
                "is required."
            )

        if lease_duration <= timedelta(0):
            raise ValueError(
                "Construction worker lease duration "
                "must be positive."
            )

        self.queue = queue
        self.handlers = dict(handlers)
        self.lease_owner = normalized_lease_owner
        self.lease_duration = lease_duration
        self.clock = clock

    async def run_once(self) -> UUID | None:
        started_at = self.clock()

        job = await self.queue.claim_next(
            now=started_at,
            lease_owner=self.lease_owner,
            lease_expires_at=(
                started_at + self.lease_duration
            ),
        )

        if job is None:
            return None

        raw_job_type = job.job_type
        job_type = (
            raw_job_type.value
            if isinstance(raw_job_type, Enum)
            else str(raw_job_type)
        )

        handler = self.handlers.get(job_type)

        if handler is None:
            failed_at = self.clock()

            await self.queue.mark_failed(
                job_id=job.id,
                lease_owner=self.lease_owner,
                error_code="HANDLER_NOT_CONFIGURED",
                now=failed_at,
            )

            return job.id

        try:
            result_reference = await handler(job)
        except ConstructionJobRetryError as exc:
            retry_requested_at = self.clock()

            await self.queue.schedule_retry(
                job_id=job.id,
                lease_owner=self.lease_owner,
                error_code=exc.error_code,
                available_at=exc.available_at,
                now=retry_requested_at,
            )

            return job.id
        except ConstructionJobPermanentError as exc:
            failed_at = self.clock()

            await self.queue.mark_failed(
                job_id=job.id,
                lease_owner=self.lease_owner,
                error_code=exc.error_code,
                now=failed_at,
            )

            return job.id
        except Exception:
            failed_at = self.clock()

            await self.queue.mark_failed(
                job_id=job.id,
                lease_owner=self.lease_owner,
                error_code="UNEXPECTED_JOB_ERROR",
                now=failed_at,
            )

            return job.id

        completed_at = self.clock()

        await self.queue.mark_ready(
            job_id=job.id,
            lease_owner=self.lease_owner,
            result_reference=result_reference,
            now=completed_at,
        )

        return job.id
