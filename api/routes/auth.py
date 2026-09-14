from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Request,
    status,
)

from api.auth import (
    get_api_auth_service,
    get_api_email_auth_service,
    get_current_actor,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    AuthSessionListResponse,
    EmailAuthChallengeRequest,
    EmailAuthChallengeResponse,
    EmailAuthVerifyRequest,
    EmailMagicLinkVerifyRequest,
    EmailAuthVerifyResponse,
    LogoutResponse,
    LogoutAllResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    TelegramAuthRequest,
    TelegramAuthResponse,
)
from api.telegram_auth import (
    TelegramAuthenticationError,
)
from services.api_email_auth import (
    ApiEmailAuthDeliveryError,
    ApiEmailAuthRateLimitError,
    ApiEmailAuthVerificationError,
    ApiEmailAuthService,
)
from services.api_auth import (
    ApiAuthAccessError,
    ApiAuthRefreshError,
    ApiAuthService,
)

from services.api_email_identity import (
    ApiEmailIdentityConflictError,
)
from services.api_identity import (
    ApiActorContext,
)


router = APIRouter()


@router.post(
    "/auth/telegram",
    response_model=TelegramAuthResponse,
)
async def authenticate_telegram(
    payload: TelegramAuthRequest,
    request: Request,
    auth_service: Annotated[
        ApiAuthService,
        Depends(get_api_auth_service),
    ],
):
    try:
        tokens = (
            await auth_service
            .authenticate_telegram(
                init_data=payload.init_data,
                device_id=payload.device_id,
                device_name=payload.device_name,
            )
        )
    except TelegramAuthenticationError:
        raise ApiHttpError(
            status_code=401,
            code="invalid_telegram_auth",
            message=(
                "Telegram authentication "
                "data is invalid."
            ),
        ) from None
    except ApiAuthAccessError:
        raise ApiHttpError(
            status_code=403,
            code="account_unavailable",
            message=(
                "User account is not "
                "available."
            ),
        ) from None

    return success_envelope(
        data={
            "access_token": (
                tokens.access_token
            ),
            "refresh_token": (
                tokens.refresh_token
            ),
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
        },
        request_id=request.state.request_id,
    )


@router.post(
    "/auth/refresh",
    response_model=RefreshTokenResponse,
)
async def refresh_tokens(
    payload: RefreshTokenRequest,
    request: Request,
    auth_service: Annotated[
        ApiAuthService,
        Depends(get_api_auth_service),
    ],
):
    try:
        tokens = await (
            auth_service.refresh_tokens(
                refresh_token=(
                    payload.refresh_token
                ),
            )
        )
    except ApiAuthRefreshError:
        raise ApiHttpError(
            status_code=401,
            code="invalid_refresh_token",
            message=(
                "Refresh token is invalid "
                "or expired."
            ),
        ) from None

    return success_envelope(
        data={
            "access_token": (
                tokens.access_token
            ),
            "refresh_token": (
                tokens.refresh_token
            ),
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
        },
        request_id=request.state.request_id,
    )


@router.post(
    "/auth/logout",
    response_model=LogoutResponse,
)
async def logout(
    payload: RefreshTokenRequest,
    request: Request,
    auth_service: Annotated[
        ApiAuthService,
        Depends(get_api_auth_service),
    ],
):
    await auth_service.logout(
        refresh_token=payload.refresh_token,
    )

    return success_envelope(
        data={
            "logged_out": True,
        },
        request_id=request.state.request_id,
    )


@router.get(
    "/auth/sessions",
    response_model=AuthSessionListResponse,
)
async def list_auth_sessions(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    auth_service: Annotated[
        ApiAuthService,
        Depends(get_api_auth_service),
    ],
):
    sessions = await (
        auth_service.list_sessions(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
        )
    )

    return success_envelope(
        data={
            "items": [
                {
                    "id": item.id,
                    "auth_method": (
                        item.auth_method
                    ),
                    "device_id": item.device_id,
                    "device_name": (
                        item.device_name
                    ),
                    "status": item.status,
                    "expires_at": (
                        item.expires_at
                    ),
                    "last_used_at": (
                        item.last_used_at
                    ),
                    "created_at": (
                        item.created_at
                    ),
                }
                for item in sessions
            ],
        },
        request_id=request.state.request_id,
    )



@router.post(
    "/auth/email/request",
    response_model=EmailAuthChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_auth_challenge(
    payload: EmailAuthChallengeRequest,
    request: Request,
    email_auth_service: Annotated[
        ApiEmailAuthService,
        Depends(get_api_email_auth_service),
    ],
):
    try:
        challenge = await (
            email_auth_service.request_challenge(
                email=payload.email,
                challenge_type=(
                    payload.challenge_type
                ),
                device_id=payload.device_id,
                device_name=payload.device_name,
            )
        )
    except ApiEmailAuthRateLimitError:
        raise ApiHttpError(
            status_code=429,
            code="email_auth_rate_limited",
            message=(
                "Too many email authentication "
                "requests."
            ),
        ) from None
    except ApiEmailAuthDeliveryError:
        raise ApiHttpError(
            status_code=503,
            code="email_delivery_unavailable",
            message=(
                "Email authentication is "
                "temporarily unavailable."
            ),
        ) from None
    except ValueError:
        raise ApiHttpError(
            status_code=422,
            code="invalid_email_auth_request",
            message=(
                "Email authentication request "
                "is invalid."
            ),
        ) from None

    return success_envelope(
        data={
            "challenge_id": challenge.challenge_id,
            "challenge_type": (
                challenge.challenge_type
            ),
            "expires_at": challenge.expires_at,
        },
        request_id=request.state.request_id,
    )



@router.post(
    "/auth/email/verify",
    response_model=EmailAuthVerifyResponse,
)
async def verify_email_auth_challenge(
    payload: EmailAuthVerifyRequest,
    request: Request,
    email_auth_service: Annotated[
        ApiEmailAuthService,
        Depends(get_api_email_auth_service),
    ],
):
    try:
        tokens = await (
            email_auth_service.verify_challenge(
                challenge_id=(
                    payload.challenge_id
                ),
                email=payload.email,
                secret=payload.secret,
                language_code=(
                    payload.language_code
                ),
            )
        )
    except ApiEmailAuthVerificationError:
        raise ApiHttpError(
            status_code=401,
            code="invalid_email_challenge",
            message=(
                "Email authentication "
                "challenge is invalid or expired."
            ),
        ) from None
    except ApiEmailIdentityConflictError:
        raise ApiHttpError(
            status_code=409,
            code="email_identity_conflict",
            message=(
                "Email identity is already "
                "linked to another account."
            ),
        ) from None
    except ValueError:
        raise ApiHttpError(
            status_code=422,
            code="invalid_email_auth_request",
            message=(
                "Email authentication request "
                "is invalid."
            ),
        ) from None

    return success_envelope(
        data={
            "access_token": (
                tokens.access_token
            ),
            "refresh_token": (
                tokens.refresh_token
            ),
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
        },
        request_id=request.state.request_id,
    )



@router.post(
    "/auth/email/magic-link/verify",
    response_model=EmailAuthVerifyResponse,
)
async def verify_email_magic_link(
    payload: EmailMagicLinkVerifyRequest,
    request: Request,
    email_auth_service: Annotated[
        ApiEmailAuthService,
        Depends(get_api_email_auth_service),
    ],
):
    try:
        tokens = await (
            email_auth_service
            .verify_magic_link_token(
                token=payload.token,
                language_code=(
                    payload.language_code
                ),
            )
        )
    except ApiEmailAuthVerificationError:
        raise ApiHttpError(
            status_code=401,
            code="invalid_email_challenge",
            message=(
                "Email authentication "
                "challenge is invalid or expired."
            ),
        ) from None
    except ApiEmailIdentityConflictError:
        raise ApiHttpError(
            status_code=409,
            code="email_identity_conflict",
            message=(
                "Email identity is already "
                "linked to another account."
            ),
        ) from None
    except ValueError:
        raise ApiHttpError(
            status_code=422,
            code="invalid_email_auth_request",
            message=(
                "Email authentication request "
                "is invalid."
            ),
        ) from None

    return success_envelope(
        data={
            "access_token": (
                tokens.access_token
            ),
            "refresh_token": (
                tokens.refresh_token
            ),
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
        },
        request_id=request.state.request_id,
    )



@router.post(
    "/auth/logout-all",
    response_model=LogoutAllResponse,
)
async def logout_all_sessions(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    auth_service: Annotated[
        ApiAuthService,
        Depends(get_api_auth_service),
    ],
):
    revoked_count = await (
        auth_service.logout_all(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
        )
    )

    return success_envelope(
        data={
            "logged_out": True,
            "revoked_sessions": (
                revoked_count
            ),
        },
        request_id=request.state.request_id,
    )


@router.post(
    "/auth/email/link/request",
    response_model=EmailAuthChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_link_challenge(
    payload: EmailAuthChallengeRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    email_auth_service: Annotated[
        ApiEmailAuthService,
        Depends(get_api_email_auth_service),
    ],
):
    try:
        challenge = await (
            email_auth_service.request_challenge(
                email=payload.email,
                challenge_type=(
                    payload.challenge_type
                ),
                device_id=payload.device_id,
                device_name=payload.device_name,
                tenant_id=actor.tenant_id,
                requested_user_id=(
                    actor.user_id
                ),
            )
        )
    except ApiEmailAuthRateLimitError:
        raise ApiHttpError(
            status_code=429,
            code="email_auth_rate_limited",
            message=(
                "Too many email authentication "
                "requests."
            ),
        ) from None
    except ApiEmailAuthDeliveryError:
        raise ApiHttpError(
            status_code=503,
            code="email_delivery_unavailable",
            message=(
                "Email authentication is "
                "temporarily unavailable."
            ),
        ) from None
    except ValueError:
        raise ApiHttpError(
            status_code=422,
            code="invalid_email_auth_request",
            message=(
                "Email authentication request "
                "is invalid."
            ),
        ) from None

    return success_envelope(
        data={
            "challenge_id": (
                challenge.challenge_id
            ),
            "challenge_type": (
                challenge.challenge_type
            ),
            "expires_at": (
                challenge.expires_at
            ),
        },
        request_id=request.state.request_id,
    )
