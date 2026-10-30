"""Internal Platform bootstrap for the trusted ordinary chat-bot transport.

These operations are unavailable to ordinary HTTP callers. Business API
requests still use the standard Platform Bearer session.
"""
from services.platform_entry_services import (
    PlatformTelegramAccountUnlinkedError,
    build_bot_users,
    build_bot_organizations,
    read_bot_organizations,
    require_canonical_bot_user,
)
# api_service_boundary_v1
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.platform_bot_context import require_platform_bot_context
from api.responses import success_envelope
from api.settings import ApiConfigurationError
from api.telegram_auth import TelegramAuthenticationError
from services.api_auth import ApiAuthAccessError
from services.api_rate_limits import (
    ApiRequestRateLimitExceededError,
)
from services.construction.construction_bot_rate_limit import (
    ConstructionBotPostgresRateLimiter,
)
from services.platform_bot_auth import (
    build_platform_bot_authentication_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platform/bot",
    include_in_schema=False,
    dependencies=[Depends(require_platform_bot_context)],
)


class PlatformBotSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID


class OrganizationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: StrictStr = Field(min_length=1)



def get_platform_bot_users(session=Depends(get_api_session)):
    return build_bot_users(session)



def get_platform_bot_organizations(session=Depends(get_api_session)):
    return build_bot_organizations(session)


def get_platform_bot_rates():
    from database.session import get_session

    return ConstructionBotPostgresRateLimiter(
        session_factory=get_session
    )


def get_platform_bot_authentication(
    session=Depends(get_api_session),
):
    try:
        return build_platform_bot_authentication_service(
            session=session
        )
    except ApiConfigurationError:
        raise ApiHttpError(
            status_code=503,
            code="platform_auth_configuration_required",
            message="Platform authentication is not configured.",
        ) from None





def failure(error):
    if isinstance(
        error,
        (
            ApiAuthAccessError,
            PlatformTelegramAccountUnlinkedError,
            TelegramAuthenticationError,
        ),
    ):
        return ApiHttpError(
            status_code=403,
            code="account_unavailable",
            message="Platform account is unavailable.",
        )
    if isinstance(error, ApiRequestRateLimitExceededError):
        return ApiHttpError(
            status_code=429,
            code="RATE_LIMIT_EXCEEDED",
            message="Too many requests.",
            headers={
                "Retry-After": str(
                    error.retry_after_seconds
                )
            },
        )
    logger.warning(
        "platform_bot_entry_failed error_type=%s",
        type(error).__name__,
    )
    return ApiHttpError(
        status_code=500,
        code="internal_error",
        message="Platform bot operation failed.",
    )


@router.get("/organizations")
async def list_bot_organizations(
    request: Request,
    response: Response,
    limit: int = Query(default=10, ge=1, le=20),
    cursor: UUID | None = Query(default=None),
    context=Depends(require_platform_bot_context),
    users=Depends(get_platform_bot_users),
    repository=Depends(get_platform_bot_organizations),
    rates=Depends(get_platform_bot_rates),
):
    if (
        set(request.query_params) - {"limit", "cursor"}
        or any(
            len(request.query_params.getlist(name)) != 1
            for name in request.query_params
        )
    ):
        raise ApiHttpError(
            status_code=422,
            code="validation_error",
            message="Organization query is invalid.",
        )

    try:
        rows = await read_bot_organizations(
            context=context, users=users, rates=rates,
            repository=repository, after=cursor, limit=limit,
        )
        if not isinstance(rows, list) or len(rows) > limit + 1:
            raise ValueError("Invalid organization page.")

        items = [
            OrganizationItem.model_validate(row)
            for row in rows
        ]
        previous = cursor
        for item in items:
            if previous is not None and item.id <= previous:
                raise ValueError("Invalid organization ordering.")
            previous = item.id

        visible = items[:limit]
        next_cursor = (
            str(visible[-1].id)
            if len(items) > limit
            else None
        )
    except Exception as error:
        raise failure(error) from None

    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=[
            item.model_dump(mode="json")
            for item in visible
        ],
        meta={
            "pagination": {
                "next_cursor": next_cursor
            }
        },
        request_id=request.state.request_id,
    )


@router.post("/session")
async def create_bot_session(
    body: PlatformBotSessionRequest,
    request: Request,
    response: Response,
    context=Depends(require_platform_bot_context),
    users=Depends(get_platform_bot_users),
    rates=Depends(get_platform_bot_rates),
    service=Depends(get_platform_bot_authentication),
):
    if request.query_params:
        raise ApiHttpError(
            status_code=422,
            code="validation_error",
            message="Session query parameters are not supported.",
        )

    try:
        user = await require_canonical_bot_user(
            context, users, rates
        )
        result = await service.authenticate(
            verifier=context.verifier,
            proof=context.proof,
            tenant_id=body.tenant_id,
        )
        if (
            result.user_id != user.id
            or result.tenant_id != body.tenant_id
        ):
            raise ValueError("Invalid authentication result.")
    except Exception as error:
        raise failure(error) from None

    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data={
            "user_id": result.user_id,
            "tenant_id": result.tenant_id,
            "access_token": (
                result.access_token.get_secret_value()
            ),
            "refresh_token": (
                result.refresh_token.get_secret_value()
            ),
            "token_type": "bearer",
            "expires_in": result.expires_in,
        },
        request_id=request.state.request_id,
    )
