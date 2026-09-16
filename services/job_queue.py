from datetime import datetime
from typing import Protocol, TypeVar
from uuid import UUID


JobT = TypeVar("JobT")


class JobQueue(Protocol[JobT]):
    async def enqueue(
        self,
        *,
        job: JobT,
    ) -> UUID:
        ...

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> JobT | None:
        ...

    async def update_progress(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        progress: int,
        now: datetime,
    ) -> UUID:
        ...

    async def mark_ready(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        result_reference: str,
        now: datetime,
    ) -> UUID:
        ...

    async def schedule_retry(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        error_code: str,
        available_at: datetime,
        now: datetime,
    ) -> UUID:
        ...

    async def mark_failed(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> UUID:
        ...

    async def cancel(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        now: datetime,
    ) -> UUID:
        ...
