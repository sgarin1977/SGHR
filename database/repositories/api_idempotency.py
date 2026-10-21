from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ApiIdempotencyRecord


class ApiIdempotencyRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def get_active_record_for_update(
        self,
        *,
        tenant_id: UUID,
        principal_type: str,
        principal_id: UUID,
        operation: str,
        key_hash: str,
        now: datetime,
    ) -> ApiIdempotencyRecord | None:
        result = await self.session.execute(
            select(ApiIdempotencyRecord)
            .where(
                ApiIdempotencyRecord.tenant_id
                == tenant_id,
                ApiIdempotencyRecord.principal_type
                == principal_type,
                ApiIdempotencyRecord.principal_id
                == principal_id,
                ApiIdempotencyRecord.operation
                == operation,
                ApiIdempotencyRecord.key_hash
                == key_hash,
                ApiIdempotencyRecord.expires_at
                > now,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()


    async def reserve_record(
        self, *, tenant_id: UUID, principal_type: str,
        principal_id: UUID, operation: str, key_hash: str,
        request_hash: str, now: datetime, expires_at: datetime,
    ) -> tuple[ApiIdempotencyRecord, bool]:
        from uuid import uuid4
        from sqlalchemy.dialects.postgresql import insert

        if expires_at <= now:
            raise ValueError("Idempotency expiration must be in the future.")

        renewed = {
            "request_hash": request_hash,
            "status": "processing",
            "response_status": None,
            "response_ciphertext": None,
            "completed_at": None,
            "created_at": now,
            "expires_at": expires_at,
        }
        statement = (
            insert(ApiIdempotencyRecord)
            .values(
                id=uuid4(), tenant_id=tenant_id,
                principal_type=principal_type, principal_id=principal_id,
                operation=operation, key_hash=key_hash, **renewed,
            )
            .on_conflict_do_update(
                index_elements=[
                    ApiIdempotencyRecord.tenant_id,
                    ApiIdempotencyRecord.principal_type,
                    ApiIdempotencyRecord.principal_id,
                    ApiIdempotencyRecord.operation,
                    ApiIdempotencyRecord.key_hash,
                ],
                set_=renewed,
                where=ApiIdempotencyRecord.expires_at <= now,
            )
            .returning(ApiIdempotencyRecord)
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(statement)
        record = result.scalar_one_or_none()
        if record is not None:
            return record, True

        record = await self.get_active_record_for_update(
            tenant_id=tenant_id, principal_type=principal_type,
            principal_id=principal_id, operation=operation,
            key_hash=key_hash, now=now,
        )
        if record is None:
            raise RuntimeError("Idempotency reservation could not be resolved.")
        return record, False


    async def create_record(
        self,
        *,
        tenant_id: UUID,
        principal_type: str,
        principal_id: UUID,
        operation: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> ApiIdempotencyRecord:
        record = ApiIdempotencyRecord(
            tenant_id=tenant_id,
            principal_type=principal_type,
            principal_id=principal_id,
            operation=operation,
            key_hash=key_hash,
            request_hash=request_hash,
            status="processing",
            expires_at=expires_at,
        )
        self.session.add(record)
        await self.session.flush()
        return record


    async def complete_record(
        self,
        *,
        record: ApiIdempotencyRecord,
        response_status: int,
        response_ciphertext: bytes,
        completed_at: datetime,
    ) -> ApiIdempotencyRecord:
        record.status = "completed"
        record.response_status = response_status
        record.response_ciphertext = (
            response_ciphertext
        )
        record.completed_at = completed_at

        await self.session.flush()
        return record
