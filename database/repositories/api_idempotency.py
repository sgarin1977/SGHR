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
        )
        return result.scalar_one_or_none()


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
