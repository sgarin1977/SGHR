from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.security import (
    AccessTokenCodec,
    RefreshTokenCodec,
)
from api.telegram_auth import (
    TelegramAuthenticationError,
    TelegramInitDataVerifier,
)
from services.user import (
    TelegramUserData,
    UserService,
)


class ApiAuthAccessError(Exception):
    pass


class ApiAuthRefreshError(Exception):
    pass


@dataclass(frozen=True)
class ApiAuthSessionView:
    id: UUID
    auth_method: str
    device_id: str | None
    device_name: str | None
    status: str
    expires_at: datetime
    last_used_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class ApiTokenPair:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class ApiAuthSessionRepository(Protocol):
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
    ) -> object:
        ...


class ApiAuthEventRepository(Protocol):
    async def create_event(
        self,
        **kwargs,
    ) -> object:
        ...


class ApiAuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        telegram_verifier: (
            TelegramInitDataVerifier | None
        ),
        user_service: UserService,
        access_token_codec: AccessTokenCodec,
        refresh_token_codec: RefreshTokenCodec,
        session_repository: (
            ApiAuthSessionRepository
        ),
        refresh_token_ttl_seconds: int,
        event_repository: (
            ApiAuthEventRepository | None
        ) = None,
    ):
        if refresh_token_ttl_seconds <= 0:
            raise ValueError(
                "Refresh token TTL must be positive."
            )

        self.session = session
        self.telegram_verifier = (
            telegram_verifier
        )
        self.user_service = user_service
        self.access_token_codec = (
            access_token_codec
        )
        self.refresh_token_codec = (
            refresh_token_codec
        )
        self.session_repository = (
            session_repository
        )
        self.refresh_token_ttl_seconds = (
            refresh_token_ttl_seconds
        )
        self.event_repository = (
            event_repository
        )


    async def prepare_token_pair(
        self,
        *,
        user: object,
        auth_method: str,
        device_id: str | None,
        device_name: str | None,
        now: datetime | None = None,
    ) -> ApiTokenPair:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Token issue time must be "
                "timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )

        if (
            user is None
            or not isinstance(user.id, UUID)
            or not isinstance(
                user.tenant_id,
                UUID,
            )
            or user.status != "active"
        ):
            raise ApiAuthAccessError(
                "Authenticated user is not "
                "available."
            )

        if auth_method not in {
            "telegram",
            "email",
        }:
            raise ValueError(
                "Authentication method is "
                "invalid."
            )

        refresh_material = (
            self.refresh_token_codec
            .issue_refresh_token()
        )
        access_token = (
            self.access_token_codec
            .issue_access_token(
                user_id=user.id,
                tenant_id=user.tenant_id,
                now=current_time,
            )
        )

        await (
            self.session_repository
            .create_session(
                tenant_id=user.tenant_id,
                user_id=user.id,
                refresh_token_hash=(
                    refresh_material.token_hash
                ),
                auth_method=auth_method,
                device_id=device_id,
                device_name=device_name,
                expires_at=(
                    current_time
                    + timedelta(
                        seconds=(
                            self
                            .refresh_token_ttl_seconds
                        )
                    )
                ),
            )
        )

        return ApiTokenPair(
            access_token=access_token,
            refresh_token=(
                refresh_material.token
            ),
            token_type="bearer",
            expires_in=(
                self.access_token_codec
                .access_ttl_seconds
            ),
        )


    async def authenticate_telegram(
        self,
        *,
        init_data: str,
        device_id: str | None,
        device_name: str | None,
        now: datetime | None = None,
    ) -> ApiTokenPair:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Authentication time must be "
                "timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )
        if self.telegram_verifier is None:
            raise ApiAuthAccessError(
                "Telegram authentication "
                "is not configured."
            )

        try:
            identity = (
                self.telegram_verifier.verify(
                    init_data,
                    now=current_time,
                )
            )
        except TelegramAuthenticationError:
            if self.event_repository is not None:
                try:
                    await (
                        self.event_repository
                        .create_event(
                            event_type=(
                                "api_telegram_auth_"
                                "failed"
                            ),
                            tenant_id=None,
                            user_id=None,
                            entity_type=None,
                            entity_id=None,
                            payload={
                                "auth_method": (
                                    "telegram"
                                ),
                                "reason": (
                                    "invalid_init_data"
                                ),
                            },
                            platform="api",
                        )
                    )
                    await self.session.commit()
                except Exception:
                    await self.session.rollback()

            raise

        registration = await (
            self.user_service
            .register_telegram_user(
                TelegramUserData(
                    platform_user_id=str(
                        identity.platform_user_id
                    ),
                    username=identity.username,
                    first_name=identity.first_name,
                    last_name=identity.last_name,
                    language_code=(
                        identity.language_code
                        or "ru"
                    ),
                )
            )
        )
        user = await (
            self.user_service.get_user_by_id(
                registration.user_id
            )
        )

        if (
            user is None
            or user.id != registration.user_id
            or user.tenant_id is None
            or user.status != "active"
        ):
            raise ApiAuthAccessError(
                "Authenticated user is not "
                "available."
            )

        try:
            token_pair = await (
                self.prepare_token_pair(
                    user=user,
                    auth_method="telegram",
                    device_id=device_id,
                    device_name=device_name,
                    now=current_time,
                )
            )

            if self.event_repository is not None:
                await (
                    self.event_repository
                    .create_event(
                        event_type=(
                            "api_telegram_auth_"
                            "succeeded"
                        ),
                        tenant_id=user.tenant_id,
                        user_id=user.id,
                        entity_type="user",
                        entity_id=user.id,
                        payload={
                            "auth_method": (
                                "telegram"
                            ),
                        },
                        platform="api",
                    )
                )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return token_pair


    async def refresh_tokens(
        self,
        *,
        refresh_token: str,
        now: datetime | None = None,
    ) -> ApiTokenPair:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Refresh time must be "
                "timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )

        try:
            current_hash = (
                self.refresh_token_codec
                .hash_refresh_token(
                    refresh_token
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            raise ApiAuthRefreshError(
                "Refresh session is not "
                "available."
            ) from None

        try:
            auth_session = await (
                self.session_repository
                .get_active_session_for_update(
                    refresh_token_hash=(
                        current_hash
                    ),
                    now=current_time,
                )
            )

            if auth_session is None:
                raise ApiAuthRefreshError(
                    "Refresh session is not "
                    "available."
                )

            user = await (
                self.user_service.get_user_by_id(
                    auth_session.user_id
                )
            )

            if (
                user is None
                or user.id
                != auth_session.user_id
                or user.tenant_id is None
                or user.tenant_id
                != auth_session.tenant_id
                or user.status != "active"
            ):
                raise ApiAuthRefreshError(
                    "Refresh session is not "
                    "available."
                )

            refresh_material = (
                self.refresh_token_codec
                .issue_refresh_token()
            )
            access_token = (
                self.access_token_codec
                .issue_access_token(
                    user_id=user.id,
                    tenant_id=user.tenant_id,
                    now=current_time,
                )
            )

            await (
                self.session_repository
                .rotate_session(
                    auth_session=auth_session,
                    refresh_token_hash=(
                        refresh_material
                        .token_hash
                    ),
                    expires_at=(
                        current_time
                        + timedelta(
                            seconds=(
                                self
                                .refresh_token_ttl_seconds
                            )
                        )
                    ),
                    now=current_time,
                )
            )
            await self.session.commit()
        except ApiAuthRefreshError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise

        return ApiTokenPair(
            access_token=access_token,
            refresh_token=(
                refresh_material.token
            ),
            token_type="bearer",
            expires_in=(
                self.access_token_codec
                .access_ttl_seconds
            ),
        )


    async def logout(
        self,
        *,
        refresh_token: str,
        now: datetime | None = None,
    ) -> None:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Logout time must be "
                "timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )

        try:
            refresh_token_hash = (
                self.refresh_token_codec
                .hash_refresh_token(
                    refresh_token
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return

        try:
            auth_session = await (
                self.session_repository
                .get_active_session_for_update(
                    refresh_token_hash=(
                        refresh_token_hash
                    ),
                    now=current_time,
                )
            )

            if auth_session is not None:
                await (
                    self.session_repository
                    .revoke_session(
                        auth_session=auth_session,
                        now=current_time,
                    )
                )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise


    async def logout_all(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        now: datetime | None = None,
    ) -> int:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Logout time must be "
                "timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )

        try:
            revoked_count = await (
                self.session_repository
                .revoke_all_sessions(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    now=current_time,
                )
            )

            if self.event_repository is not None:
                await (
                    self.event_repository
                    .create_event(
                        event_type=(
                            "api_auth_all_"
                            "sessions_revoked"
                        ),
                        tenant_id=tenant_id,
                        user_id=user_id,
                        entity_type="user",
                        entity_id=user_id,
                        payload={
                            "revoked_count": (
                                revoked_count
                            ),
                        },
                        platform="api",
                    )
                )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return revoked_count


    async def list_sessions(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        now: datetime | None = None,
    ) -> tuple[ApiAuthSessionView, ...]:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Session list time must be "
                "timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )
        sessions = await (
            self.session_repository
            .list_active_sessions(
                tenant_id=tenant_id,
                user_id=user_id,
                now=current_time,
            )
        )

        return tuple(
            ApiAuthSessionView(
                id=item.id,
                auth_method=item.auth_method,
                device_id=item.device_id,
                device_name=item.device_name,
                status=item.status,
                expires_at=item.expires_at,
                last_used_at=item.last_used_at,
                created_at=item.created_at,
            )
            for item in sessions
        )
