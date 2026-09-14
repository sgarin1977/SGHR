from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ApiLog
from database.models import ApiKeyScope
from database.models import (
    ApiClient,
    ApiKey,
    Tenant,
)


API_CLIENT_OWNER_TYPES = frozenset(
    {
        "agency",
        "partner",
        "enterprise",
        "service_account",
    }
)


class PartnerApiRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def create_api_client(
        self,
        *,
        tenant_id: UUID,
        owner_type: str,
        owner_id: UUID | None,
        name: str,
        metadata: dict[str, object],
    ) -> ApiClient:
        if owner_type not in API_CLIENT_OWNER_TYPES:
            raise ValueError(
                "API client owner type is invalid."
            )

        if not isinstance(metadata, dict):
            raise ValueError(
                "API client metadata must be an object."
            )

        api_client = ApiClient(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            name=name,
            status="active",
            extra_metadata=dict(metadata),
        )

        self.session.add(api_client)
        await self.session.flush()

        return api_client

    async def list_api_clients(
        self,
        *,
        tenant_id: UUID,
        limit: int,
        offset: int = 0,
    ) -> list[ApiClient]:
        if limit <= 0:
            raise ValueError(
                "API client list limit must be positive."
            )

        if offset < 0:
            raise ValueError(
                "API client list offset cannot be negative."
            )

        statement = (
            select(ApiClient)
            .where(
                ApiClient.tenant_id == tenant_id,
            )
            .order_by(
                ApiClient.created_at.desc(),
                ApiClient.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def get_api_client(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
    ) -> ApiClient | None:
        statement = (
            select(ApiClient)
            .where(
                ApiClient.tenant_id == tenant_id,
                ApiClient.id == api_client_id,
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def get_api_client_for_update(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
    ) -> ApiClient | None:
        statement = (
            select(ApiClient)
            .where(
                ApiClient.tenant_id == tenant_id,
                ApiClient.id == api_client_id,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()


    async def update_api_client(
        self,
        *,
        api_client: ApiClient,
        name: str,
        status: str,
        metadata: dict[str, object],
    ) -> ApiClient:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError(
                "API client name is required."
            )

        if status not in {
            "active",
            "suspended",
            "disabled",
        }:
            raise ValueError(
                "API client status is invalid."
            )

        if not isinstance(metadata, dict):
            raise ValueError(
                "API client metadata must be an object."
            )

        api_client.name = normalized_name
        api_client.status = status
        api_client.extra_metadata = dict(metadata)

        await self.session.flush()
        return api_client


    async def create_api_key(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
        name: str,
        key_prefix: str,
        key_hash: str,
        environment: str,
        expires_at: datetime | None,
        ip_allowlist: list[str] | None,
    ) -> ApiKey:
        if (
            len(key_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in key_hash
            )
        ):
            raise ValueError(
                "API key hash must be a lowercase "
                "SHA-256 hexadecimal value."
            )

        if environment not in {
            "sandbox",
            "production",
        }:
            raise ValueError(
                "API key environment is invalid."
            )

        if (
            expires_at is not None
            and expires_at.tzinfo is None
        ):
            raise ValueError(
                "API key expiration must be "
                "timezone-aware."
            )

        api_key = ApiKey(
            tenant_id=tenant_id,
            api_client_id=api_client_id,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            environment=environment,
            status="active",
            expires_at=expires_at,
            last_used_at=None,
            ip_allowlist=(
                list(ip_allowlist)
                if ip_allowlist is not None
                else None
            ),
        )

        self.session.add(api_key)
        await self.session.flush()

        return api_key

    async def create_api_key_scopes(
        self,
        *,
        tenant_id: UUID,
        api_key_id: UUID,
        scopes: tuple[str, ...],
    ) -> tuple[ApiKeyScope, ...]:
        normalized_scopes = tuple(
            dict.fromkeys(
                scope.strip()
                for scope in scopes
                if isinstance(scope, str)
                and scope.strip()
            )
        )

        if not normalized_scopes:
            raise ValueError(
                "At least one API Key scope "
                "is required."
            )

        records = tuple(
            ApiKeyScope(
                tenant_id=tenant_id,
                api_key_id=api_key_id,
                scope=scope,
            )
            for scope in normalized_scopes
        )

        for record in records:
            self.session.add(record)

        await self.session.flush()
        return records

    async def list_api_key_scopes(
        self,
        *,
        tenant_id: UUID,
        api_key_id: UUID,
    ) -> tuple[str, ...]:
        statement = (
            select(ApiKeyScope.scope)
            .where(
                ApiKeyScope.tenant_id
                == tenant_id,
                ApiKeyScope.api_key_id
                == api_key_id,
            )
            .order_by(
                ApiKeyScope.created_at.asc(),
                ApiKeyScope.id.asc(),
            )
        )

        result = await self.session.execute(
            statement
        )
        return tuple(
            result.scalars().all()
        )

    async def list_api_keys(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
        limit: int,
        offset: int = 0,
    ) -> list[ApiKey]:
        if limit <= 0:
            raise ValueError(
                "API key list limit must be positive."
            )

        if offset < 0:
            raise ValueError(
                "API key list offset cannot be negative."
            )

        statement = (
            select(ApiKey)
            .where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.api_client_id
                == api_client_id,
            )
            .order_by(
                ApiKey.created_at.desc(),
                ApiKey.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def get_api_key_for_update(
        self,
        *,
        tenant_id: UUID,
        api_key_id: UUID,
    ) -> ApiKey | None:
        statement = (
            select(ApiKey)
            .where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.id == api_key_id,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def revoke_api_key(
        self,
        *,
        api_key: ApiKey,
    ) -> ApiKey:
        if api_key.status == "revoked":
            return api_key

        api_key.status = "revoked"
        await self.session.flush()

        return api_key

    async def get_active_api_key(
        self,
        *,
        key_prefix: str,
        environment: str,
        now: datetime,
    ) -> ApiKey | None:
        if environment not in {
            "sandbox",
            "production",
        }:
            raise ValueError(
                "API key environment is invalid."
            )

        if now.tzinfo is None:
            raise ValueError(
                "API key lookup time must be "
                "timezone-aware."
            )

        statement = (
            select(ApiKey)
            .join(
                ApiClient,
                and_(
                    ApiClient.id
                    == ApiKey.api_client_id,
                    ApiClient.tenant_id
                    == ApiKey.tenant_id,
                ),
            )
            .join(
                Tenant,
                Tenant.id == ApiKey.tenant_id,
            )
            .where(
                ApiKey.key_prefix == key_prefix,
                ApiKey.environment
                == environment,
                ApiKey.status == "active",
                or_(
                    ApiKey.expires_at.is_(None),
                    ApiKey.expires_at > now,
                ),
                ApiClient.status == "active",
                Tenant.status == "active",
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def mark_api_key_used(
        self,
        *,
        api_key: ApiKey,
        now: datetime,
    ) -> ApiKey:
        if now.tzinfo is None:
            raise ValueError(
                "API key usage time must be "
                "timezone-aware."
            )

        api_key.last_used_at = now
        await self.session.flush()

        return api_key




    async def list_api_logs(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID | None = None,
        api_key_id: UUID | None = None,
        status_code: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        endpoint: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[ApiLog]:
        if limit <= 0:
            raise ValueError(
                "API log list limit must be positive."
            )

        if offset < 0:
            raise ValueError(
                "API log list offset cannot be negative."
            )

        if (
            status_code is not None
            and not 100 <= status_code <= 599
        ):
            raise ValueError(
                "API log status code is invalid."
            )

        for value in (date_from, date_to):
            if (
                value is not None
                and (
                    value.tzinfo is None
                    or value.utcoffset() is None
                )
            ):
                raise ValueError(
                    "API log date filter must be "
                    "timezone-aware."
                )

        if (
            date_from is not None
            and date_to is not None
            and date_from >= date_to
        ):
            raise ValueError(
                "API log date range is invalid."
            )

        normalized_endpoint = None
        if endpoint is not None:
            normalized_endpoint = endpoint.strip()
            if not normalized_endpoint:
                raise ValueError(
                    "API log endpoint filter is invalid."
                )

        statement = select(ApiLog).where(
            ApiLog.tenant_id == tenant_id
        )

        if api_client_id is not None:
            statement = statement.where(
                ApiLog.api_client_id
                == api_client_id
            )

        if api_key_id is not None:
            statement = statement.where(
                ApiLog.api_key_id == api_key_id
            )

        if status_code is not None:
            statement = statement.where(
                ApiLog.status_code == status_code
            )

        if date_from is not None:
            statement = statement.where(
                ApiLog.created_at >= date_from
            )

        if date_to is not None:
            statement = statement.where(
                ApiLog.created_at < date_to
            )

        if normalized_endpoint is not None:
            statement = statement.where(
                ApiLog.endpoint
                == normalized_endpoint
            )

        statement = (
            statement
            .order_by(
                ApiLog.created_at.desc(),
                ApiLog.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())


    async def create_api_log(
        self,
        *,
        request_id: str,
        tenant_id: UUID,
        api_key_id: UUID,
        api_client_id: UUID,
        user_id: UUID | None,
        endpoint: str,
        method: str,
        status_code: int,
        duration_ms: int,
        ip: str | None,
        user_agent: str | None,
        created_at: datetime,
    ) -> ApiLog:
        normalized_request_id = (
            request_id.strip()
        )
        normalized_endpoint = endpoint.strip()
        normalized_method = (
            method.strip().upper()
        )

        if not normalized_request_id:
            raise ValueError(
                "API log request ID is required."
            )

        if not normalized_endpoint:
            raise ValueError(
                "API log endpoint is required."
            )

        if not normalized_method:
            raise ValueError(
                "API log method is required."
            )

        if not 100 <= status_code <= 599:
            raise ValueError(
                "API log status code is invalid."
            )

        if duration_ms < 0:
            raise ValueError(
                "API log duration cannot be negative."
            )

        if (
            created_at.tzinfo is None
            or created_at.utcoffset() is None
        ):
            raise ValueError(
                "API log timestamp must be "
                "timezone-aware."
            )

        api_log = ApiLog(
            request_id=normalized_request_id,
            tenant_id=tenant_id,
            api_key_id=api_key_id,
            api_client_id=api_client_id,
            user_id=user_id,
            endpoint=normalized_endpoint,
            method=normalized_method,
            status_code=status_code,
            duration_ms=duration_ms,
            ip=ip,
            user_agent=user_agent,
            created_at=created_at,
        )

        self.session.add(api_log)
        await self.session.flush()

        return api_log

    async def delete_api_logs_before(
        self,
        *,
        cutoff: datetime,
    ) -> int:
        if (
            cutoff.tzinfo is None
            or cutoff.utcoffset() is None
        ):
            raise ValueError(
                "API log retention cutoff must "
                "be timezone-aware."
            )

        result = await self.session.execute(
            delete(ApiLog).where(
                ApiLog.created_at < cutoff
            )
        )

        return max(
            0,
            int(result.rowcount or 0),
        )

