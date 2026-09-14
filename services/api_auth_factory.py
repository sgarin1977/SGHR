import os
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.security import (
    AccessTokenCodec,
    EmailAuthChallengeCodec,
    RefreshTokenCodec,
)
from api.settings import (
    ApiAuthSettings,
    ApiConfigurationError,
    ApiEmailAuthSettings,
    ApiEmailDeliverySettings,
)
from api.telegram_auth import (
    TelegramInitDataVerifier,
)
from database.repositories.api_auth import (
    ApiAuthSessionRepository,
)
from database.repositories.api_email_auth import (
    ApiEmailAuthChallengeRepository,
)
from database.repositories.event import (
    EventRepository,
)
from database.repositories.user import (
    UserRepository,
)
from services.api_auth import ApiAuthService
from services.api_email_auth import (
    ApiEmailAuthService,
)
from services.api_email_identity import (
    ApiEmailIdentityService,
)
from services.email_delivery import (
    ApiEmailChallengeDelivery,
)
from services.resend_email_provider import (
    ResendEmailProvider,
)
from services.user import UserService


def build_api_auth_service(
    *,
    session: AsyncSession,
    telegram_verifier: (
        TelegramInitDataVerifier | None
    ),
    access_token_codec: AccessTokenCodec,
    refresh_token_codec: RefreshTokenCodec,
) -> ApiAuthService:
    settings = ApiAuthSettings.from_env()

    return ApiAuthService(
        session=session,
        telegram_verifier=telegram_verifier,
        user_service=UserService(session),
        access_token_codec=access_token_codec,
        refresh_token_codec=refresh_token_codec,
        session_repository=(
            ApiAuthSessionRepository(session)
        ),
        refresh_token_ttl_seconds=(
            settings.refresh_token_ttl_seconds
        ),
        event_repository=EventRepository(
            session
        ),
    )


def build_email_challenge_delivery(
) -> ApiEmailChallengeDelivery:
    settings = (
        ApiEmailDeliverySettings.from_env()
    )
    provider = ResendEmailProvider(
        api_key=settings.resend_api_key,
    )

    return ApiEmailChallengeDelivery(
        provider=provider,
        from_address=settings.from_address,
        web_app_url=settings.web_app_url,
        mobile_deep_link_scheme=(
            settings.mobile_deep_link_scheme
        ),
    )


def build_api_email_auth_service(
    *,
    session: AsyncSession,
    email_delivery: (
        ApiEmailChallengeDelivery
    ),
    access_token_codec: AccessTokenCodec,
    refresh_token_codec: RefreshTokenCodec,
) -> ApiEmailAuthService:
    email_settings = (
        ApiEmailAuthSettings.from_env()
    )
    auth_settings = (
        ApiAuthSettings.from_env()
    )

    raw_tenant_id = os.getenv(
        "DEFAULT_TENANT_ID",
        "",
    ).strip()

    try:
        default_tenant_id = UUID(
            raw_tenant_id
        )
    except ValueError as exc:
        raise ApiConfigurationError(
            "DEFAULT_TENANT_ID must be "
            "a valid UUID."
        ) from exc

    token_service = ApiAuthService(
        session=session,
        telegram_verifier=None,
        user_service=UserService(session),
        access_token_codec=access_token_codec,
        refresh_token_codec=refresh_token_codec,
        session_repository=(
            ApiAuthSessionRepository(session)
        ),
        refresh_token_ttl_seconds=(
            auth_settings
            .refresh_token_ttl_seconds
        ),
    )

    identity_service = (
        ApiEmailIdentityService(
            user_repository=(
                UserRepository(session)
            ),
            default_tenant_id=(
                default_tenant_id
            ),
        )
    )

    return ApiEmailAuthService(
        session=session,
        challenge_repository=(
            ApiEmailAuthChallengeRepository(
                session
            )
        ),
        challenge_codec=(
            EmailAuthChallengeCodec(
                secret=(
                    email_settings
                    .challenge_secret
                )
            )
        ),
        email_delivery=email_delivery,
        identity_service=identity_service,
        token_service=token_service,
        event_repository=EventRepository(
            session
        ),
        code_ttl_seconds=(
            email_settings.code_ttl_seconds
        ),
        max_attempts=(
            email_settings.max_attempts
        ),
        request_limit=(
            email_settings.request_limit
        ),
        request_window_seconds=(
            email_settings
            .request_window_seconds
        ),
    )
