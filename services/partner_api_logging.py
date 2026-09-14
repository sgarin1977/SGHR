from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from database.repositories.partner_api import (
    PartnerApiRepository,
)
from database.session import async_session


API_LOG_RETENTION_DAYS = 90

class PartnerApiPrincipalProtocol(Protocol):
    tenant_id: UUID
    api_client_id: UUID
    api_key_id: UUID


class PartnerApiRequestLogService:
    def __init__(
        self,
        *,
        session,
        repository,
    ):
        self.session = session
        self.repository = repository

    async def record_request(
        self,
        *,
        principal: PartnerApiPrincipalProtocol,
        request_id: str,
        endpoint: str,
        method: str,
        status_code: int,
        duration_ms: int,
        ip: str | None,
        user_agent: str | None,
        user_id: UUID | None = None,
    ):
        api_log = await self.repository.create_api_log(
            request_id=request_id,
            tenant_id=principal.tenant_id,
            api_key_id=principal.api_key_id,
            api_client_id=(
                principal.api_client_id
            ),
            user_id=user_id,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            duration_ms=duration_ms,
            ip=ip,
            user_agent=user_agent,
            created_at=datetime.now(UTC),
        )

        await self.session.commit()
        return api_log


class PartnerApiRequestLogger:
    async def record_request(
        self,
        **kwargs,
    ):
        async with async_session() as session:
            repository = PartnerApiRepository(
                session
            )
            await repository.delete_api_logs_before(
                cutoff=(
                    datetime.now(UTC)
                    - timedelta(
                        days=(
                            API_LOG_RETENTION_DAYS
                        )
                    )
                ),
            )

            service = PartnerApiRequestLogService(
                session=session,
                repository=repository,
            )
            return await service.record_request(
                **kwargs
            )

class PartnerApiLogRetentionService:
    def __init__(
        self,
        *,
        session,
        repository,
    ):
        self.session = session
        self.repository = repository

    async def purge_expired_logs(self) -> int:
        cutoff = (
            datetime.now(UTC)
            - timedelta(
                days=API_LOG_RETENTION_DAYS,
            )
        )

        try:
            deleted = (
                await self.repository
                .delete_api_logs_before(
                    cutoff=cutoff,
                )
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return deleted
