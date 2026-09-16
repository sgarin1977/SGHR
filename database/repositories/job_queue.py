from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import (
    insert as postgresql_insert,
)
from sqlalchemy.ext.asyncio import AsyncSession


class JobLeaseLostError(RuntimeError):
    pass


class JobStateConflictError(RuntimeError):
    pass


class JobIdempotencyConflictError(RuntimeError):
    pass


class PostgresJobQueue:
    def __init__(
        self,
        session: AsyncSession,
        *,
        model: type[Any] | None = None,
        idempotency_columns: tuple[str, ...] = (),
        idempotency_payload_columns: tuple[
            str,
            ...,
        ] = (),
    ) -> None:
        self.session = session
        self.model = model
        self.idempotency_columns = (
            idempotency_columns
        )
        self.idempotency_payload_columns = (
            idempotency_payload_columns
        )

    def _require_model(self) -> type[Any]:
        if self.model is None:
            raise RuntimeError(
                "PostgreSQL job model is not configured."
            )
        return self.model

    async def enqueue(
        self,
        *,
        job: Any,
    ) -> UUID:
        model = self._require_model()
        values = {}

        for column in model.__table__.columns:
            attribute_name = column.key

            if not hasattr(
                job,
                attribute_name,
            ):
                continue

            value = getattr(
                job,
                attribute_name,
            )

            if isinstance(value, Enum):
                value = value.value

            values[attribute_name] = value

        if self.idempotency_columns:
            missing_columns = [
                column_name
                for column_name
                in self.idempotency_columns
                if column_name not in values
            ]

            if missing_columns:
                raise ValueError(
                    "Job idempotency scope is incomplete."
                )

            conflict_columns = [
                getattr(model, column_name)
                for column_name
                in self.idempotency_columns
            ]

            statement = (
                postgresql_insert(model)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=conflict_columns,
                )
                .returning(model.id)
            )

            result = await self.session.execute(
                statement
            )
            inserted_job_id = (
                result.scalar_one_or_none()
            )

            if inserted_job_id is not None:
                return inserted_job_id

            lookup_statement = select(
                model
            ).where(
                *(
                    getattr(model, column_name)
                    == values[column_name]
                    for column_name
                    in self.idempotency_columns
                )
            )

            lookup_result = (
                await self.session.execute(
                    lookup_statement
                )
            )
            existing_job = (
                lookup_result.scalar_one_or_none()
            )

            if existing_job is None:
                raise JobStateConflictError(
                    "Idempotent job could not be resolved."
                )

            for column_name in (
                self.idempotency_payload_columns
            ):
                requested_value = values.get(
                    column_name
                )
                existing_value = getattr(
                    existing_job,
                    column_name,
                )

                if isinstance(existing_value, Enum):
                    existing_value = (
                        existing_value.value
                    )

                if existing_value != requested_value:
                    raise JobIdempotencyConflictError(
                        "Idempotency key was reused "
                        "for a different job."
                    )

            return existing_job.id

        if isinstance(job, model):
            record = job
        else:
            record = model(**values)

        self.session.add(record)
        await self.session.flush()
        return record.id

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> Any | None:
        model = self._require_model()

        statement = (
            select(model)
            .where(
                model.status.in_(
                    (
                        "QUEUED",
                        "RETRY",
                        "RUNNING",
                    )
                ),
                model.available_at <= now,
                or_(
                    model.lease_expires_at.is_(None),
                    model.lease_expires_at <= now,
                ),
            )
            .order_by(
                model.available_at,
                model.id,
            )
            .limit(1)
            .with_for_update(
                skip_locked=True,
            )
        )

        result = await self.session.execute(
            statement
        )
        job = result.scalar_one_or_none()

        if job is None:
            return None

        job.status = "RUNNING"
        job.attempts += 1
        job.lease_owner = lease_owner
        job.lease_expires_at = lease_expires_at

        await self.session.flush()
        return job

    async def update_progress(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        progress: int,
        now: datetime,
    ) -> UUID:
        if (
            isinstance(progress, bool)
            or not isinstance(progress, int)
            or progress < 0
            or progress > 100
        ):
            raise ValueError(
                "Job progress must be between 0 and 100."
            )

        if not lease_owner.strip():
            raise ValueError(
                "Job lease owner is required."
            )

        model = self._require_model()

        statement = (
            update(model)
            .where(
                model.id == job_id,
                model.status == "RUNNING",
                model.lease_owner == lease_owner,
                model.lease_expires_at > now,
            )
            .values(
                progress=progress,
                updated_at=now,
            )
            .returning(model.id)
        )

        result = await self.session.execute(
            statement
        )
        updated_job_id = (
            result.scalar_one_or_none()
        )

        if updated_job_id is None:
            raise JobLeaseLostError(
                "Job lease is unavailable."
            )

        return updated_job_id

    async def mark_ready(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        result_reference: str,
        now: datetime,
    ) -> UUID:
        normalized_reference = (
            result_reference.strip()
            if isinstance(result_reference, str)
            else ""
        )

        if not normalized_reference:
            raise ValueError(
                "Job result reference is required."
            )

        if not lease_owner.strip():
            raise ValueError(
                "Job lease owner is required."
            )

        model = self._require_model()

        statement = (
            update(model)
            .where(
                model.id == job_id,
                model.status == "RUNNING",
                model.lease_owner == lease_owner,
                model.lease_expires_at > now,
            )
            .values(
                status="READY",
                progress=100,
                result_reference=normalized_reference,
                error_code=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .returning(model.id)
        )

        result = await self.session.execute(
            statement
        )
        updated_job_id = (
            result.scalar_one_or_none()
        )

        if updated_job_id is None:
            raise JobLeaseLostError(
                "Job lease is unavailable."
            )

        return updated_job_id

    async def schedule_retry(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        error_code: str,
        available_at: datetime,
        now: datetime,
    ) -> UUID:
        normalized_error_code = (
            error_code.strip()
            if isinstance(error_code, str)
            else ""
        )

        if not normalized_error_code:
            raise ValueError(
                "Job error code is required."
            )

        if not lease_owner.strip():
            raise ValueError(
                "Job lease owner is required."
            )

        model = self._require_model()

        statement = (
            update(model)
            .where(
                model.id == job_id,
                model.status == "RUNNING",
                model.lease_owner == lease_owner,
                model.lease_expires_at > now,
            )
            .values(
                status="RETRY",
                available_at=available_at,
                error_code=normalized_error_code,
                result_reference=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .returning(model.id)
        )

        result = await self.session.execute(
            statement
        )
        updated_job_id = (
            result.scalar_one_or_none()
        )

        if updated_job_id is None:
            raise JobLeaseLostError(
                "Job lease is unavailable."
            )

        return updated_job_id

    async def mark_failed(
        self,
        *,
        job_id: UUID,
        lease_owner: str,
        error_code: str,
        now: datetime,
    ) -> UUID:
        normalized_error_code = (
            error_code.strip()
            if isinstance(error_code, str)
            else ""
        )

        if not normalized_error_code:
            raise ValueError(
                "Job error code is required."
            )

        if not lease_owner.strip():
            raise ValueError(
                "Job lease owner is required."
            )

        model = self._require_model()

        statement = (
            update(model)
            .where(
                model.id == job_id,
                model.status == "RUNNING",
                model.lease_owner == lease_owner,
                model.lease_expires_at > now,
            )
            .values(
                status="FAILED",
                error_code=normalized_error_code,
                result_reference=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .returning(model.id)
        )

        result = await self.session.execute(
            statement
        )
        updated_job_id = (
            result.scalar_one_or_none()
        )

        if updated_job_id is None:
            raise JobLeaseLostError(
                "Job lease is unavailable."
            )

        return updated_job_id

    async def cancel(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        now: datetime,
    ) -> UUID:
        model = self._require_model()

        statement = (
            update(model)
            .where(
                model.id == job_id,
                model.tenant_id == tenant_id,
                model.status.in_(
                    (
                        "QUEUED",
                        "RETRY",
                        "RUNNING",
                    )
                ),
            )
            .values(
                status="CANCELLED",
                error_code=None,
                result_reference=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .returning(model.id)
        )

        result = await self.session.execute(
            statement
        )
        updated_job_id = (
            result.scalar_one_or_none()
        )

        if updated_job_id is None:
            raise JobStateConflictError(
                "Job cannot be cancelled."
            )

        return updated_job_id
