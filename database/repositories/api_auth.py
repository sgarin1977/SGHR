from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.models import ApiAuthSession


class ApiAuthSessionRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def create_session(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        refresh_token_hash: str,
        auth_method: str,
        device_id: str | None,
        device_name: str | None,
        expires_at: datetime,
    ) -> ApiAuthSession:
        if len(refresh_token_hash) != 64:
            raise ValueError(
                "Refresh token hash must "
                "contain 64 characters."
            )

        if expires_at.tzinfo is None:
            raise ValueError(
                "Session expiration must be "
                "timezone-aware."
            )

        auth_session = ApiAuthSession(
            tenant_id=tenant_id,
            user_id=user_id,
            refresh_token_hash=(
                refresh_token_hash
            ),
            auth_method=auth_method,
            device_id=device_id,
            device_name=device_name,
            status="active",
            expires_at=expires_at,
        )

        self.session.add(auth_session)
        await self.session.flush()

        return auth_session


    async def get_active_session_for_update(
        self,
        *,
        refresh_token_hash: str,
        now: datetime,
    ) -> ApiAuthSession | None:
        if len(refresh_token_hash) != 64:
            raise ValueError(
                "Refresh token hash must "
                "contain 64 characters."
            )

        if now.tzinfo is None:
            raise ValueError(
                "Refresh lookup time must be "
                "timezone-aware."
            )

        statement = (
            select(ApiAuthSession)
            .where(
                ApiAuthSession
                .refresh_token_hash
                == refresh_token_hash,
                ApiAuthSession.status
                == "active",
                ApiAuthSession.expires_at
                > now,
                ApiAuthSession.revoked_at
                .is_(None),
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()


    async def rotate_session(
        self,
        *,
        auth_session: ApiAuthSession,
        refresh_token_hash: str,
        expires_at: datetime,
        now: datetime,
    ) -> ApiAuthSession:
        if len(refresh_token_hash) != 64:
            raise ValueError(
                "Refresh token hash must "
                "contain 64 characters."
            )

        if (
            expires_at.tzinfo is None
            or now.tzinfo is None
        ):
            raise ValueError(
                "Session rotation times must "
                "be timezone-aware."
            )

        auth_session.refresh_token_hash = (
            refresh_token_hash
        )
        auth_session.expires_at = expires_at
        auth_session.last_used_at = now
        auth_session.updated_at = now

        await self.session.flush()
        return auth_session


    async def revoke_session(
        self,
        *,
        auth_session: ApiAuthSession,
        now: datetime,
    ) -> ApiAuthSession:
        if now.tzinfo is None:
            raise ValueError(
                "Session revocation time must "
                "be timezone-aware."
            )

        auth_session.status = "revoked"
        auth_session.revoked_at = now
        auth_session.updated_at = now

        await self.session.flush()
        return auth_session


    async def revoke_all_sessions(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        now: datetime,
    ) -> int:
        if not isinstance(
            tenant_id,
            UUID,
        ):
            raise ValueError(
                "Tenant ID is invalid."
            )

        if not isinstance(
            user_id,
            UUID,
        ):
            raise ValueError(
                "User ID is invalid."
            )

        if now.tzinfo is None:
            raise ValueError(
                "Session revocation time must "
                "be timezone-aware."
            )

        statement = (
            update(ApiAuthSession)
            .where(
                ApiAuthSession.tenant_id
                == tenant_id,
                ApiAuthSession.user_id
                == user_id,
                ApiAuthSession.status
                == "active",
                ApiAuthSession.revoked_at
                .is_(None),
            )
            .values(
                status="revoked",
                revoked_at=now,
                updated_at=now,
            )
        )

        result = await self.session.execute(
            statement
        )
        return int(result.rowcount or 0)


    async def list_active_sessions(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        now: datetime,
    ) -> list[ApiAuthSession]:
        if now.tzinfo is None:
            raise ValueError(
                "Session list time must be "
                "timezone-aware."
            )

        statement = (
            select(ApiAuthSession)
            .where(
                ApiAuthSession.tenant_id
                == tenant_id,
                ApiAuthSession.user_id
                == user_id,
                ApiAuthSession.status
                == "active",
                ApiAuthSession.expires_at
                > now,
                ApiAuthSession.revoked_at
                .is_(None),
            )
            .order_by(
                ApiAuthSession.created_at.desc()
            )
        )

        result = await self.session.execute(
            statement
        )
        return list(
            result.scalars().all()
        )
