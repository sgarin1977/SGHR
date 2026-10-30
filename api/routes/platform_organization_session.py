"""Platform organization session selection; never creates membership or roles."""
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from api.auth import get_access_token_codec, get_refresh_token_codec
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.responses import success_envelope
from api.settings import ApiConfigurationError
from services.api_auth import ApiAuthRefreshError
from services.api_identity import ApiIdentityAccessError
from services.api_platform_principal import ApiPlatformPrincipal
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.platform_organization_session import build_platform_organization_session_service


class OrganizationSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    refresh_token: SecretStr = Field(min_length=1, max_length=1024)


class OrganizationSessionData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    tenant_id: UUID
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    token_type: str
    expires_in: int


class OrganizationSessionEnvelope(BaseModel):
    data: OrganizationSessionData
    meta: dict
    request_id: str


def get_organization_session_service(
    session=Depends(get_api_session),
    access_codec=Depends(get_access_token_codec),
    refresh_codec=Depends(get_refresh_token_codec),
):
    try:
        return build_platform_organization_session_service(
            session=session, access_codec=access_codec, refresh_codec=refresh_codec)
    except (ApiConfigurationError, ValueError):
        raise ApiHttpError(status_code=503, code="service_unavailable",
                           message="Organization session is unavailable.") from None


router = APIRouter(prefix="/platform/organizations")


@router.post("/session", response_model=OrganizationSessionEnvelope)
async def select_organization_session(
    body: OrganizationSessionRequest, request: Request, response: Response,
    principal=Depends(get_authenticated_platform_principal),
    rates=Depends(get_api_request_rate_limit_service),
    service=Depends(get_organization_session_service),
):
    if request.query_params:
        raise ApiHttpError(status_code=422, code="validation_error",
                           message="Organization selector is invalid.")
    if not isinstance(principal, ApiPlatformPrincipal):
        raise ApiHttpError(status_code=401, code="invalid_token",
                           message="Platform authentication is required.")
    try:
        await rates.ensure_platform_user_allowed(user_id=principal.user_id)
        pair = await service.select(principal=principal, tenant_id=body.tenant_id,
                                    refresh_token=body.refresh_token.get_secret_value())
    except ApiRequestRateLimitExceededError:
        raise ApiHttpError(status_code=429, code="RATE_LIMIT_EXCEEDED",
                           message="Rate limit exceeded.") from None
    except ApiAuthRefreshError:
        raise ApiHttpError(status_code=401, code="invalid_refresh_token",
                           message="Refresh session is unavailable.") from None
    except ApiIdentityAccessError:
        raise ApiHttpError(status_code=403, code="forbidden",
                           message="Organization is unavailable.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error",
                           message="Organization session failed.") from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(data={
        "user_id": str(principal.user_id), "tenant_id": str(body.tenant_id),
        "access_token": pair.access_token, "refresh_token": pair.refresh_token,
        "token_type": pair.token_type, "expires_in": pair.expires_in,
    }, request_id=request.state.request_id)
