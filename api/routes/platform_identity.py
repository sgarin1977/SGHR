"""Own canonical Platform identity, without organization authority."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from api.auth import invalid_token_error
from api.dependencies import get_api_request_rate_limit_service
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.responses import success_envelope
from services.api_platform_principal import ApiPlatformPrincipal
from services.api_rate_limits import ApiRequestRateLimitExceededError


class PlatformIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    status: Literal["active"]


class PlatformIdentityEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: PlatformIdentityResponse
    meta: dict
    request_id: str


router = APIRouter(prefix="/platform")


@router.get(
    "/identity", response_model=PlatformIdentityEnvelope,
    responses={401: {"description": "Authentication required"},
               429: {"description": "RATE_LIMIT_EXCEEDED"}},
)
async def read_own_platform_identity(
    request: Request, response: Response,
    principal=Depends(get_authenticated_platform_principal),
    rate=Depends(get_api_request_rate_limit_service),
):
    if not isinstance(principal, ApiPlatformPrincipal):
        raise invalid_token_error()
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Identity selectors are not supported.",
        )
    try:
        await rate.ensure_platform_user_allowed(user_id=principal.user_id)
    except ApiRequestRateLimitExceededError:
        raise ApiHttpError(
            status_code=429, code="RATE_LIMIT_EXCEEDED",
            message="Rate limit exceeded.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(
        data={"id": str(principal.user_id), "status": "active"},
        request_id=request.state.request_id,
    )

# invitation_identity_only_v1
