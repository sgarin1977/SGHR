"""CON-001 organization creation over the canonical Platform bootstrap.

The public operation uses a Platform principal without tenant membership.
The internal bot adapter uses the existing trusted Platform dispatch context.
Both call the same atomic domain operation.
"""
from services.platform_entry_services import (
    build_organization_creation_repository,
    read_organization_creation_languages,
)
# api_service_boundary_v1
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.platform_bot_context import require_platform_bot_context
from api.platform_principal import get_authenticated_platform_principal
from api.responses import success_envelope
from api.routes.platform_bot_entry import (
    failure as bot_failure,
    get_platform_bot_rates,
    get_platform_bot_users,
    require_canonical_bot_user,
)
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.api_platform_principal import ApiPlatformPrincipal
from services.platform_organization_bootstrap import (
    OrganizationBootstrapDenied, OrganizationBootstrapError,
    OrganizationBootstrapInProgress, OrganizationBootstrapValidationError,
    build_platform_organization_bootstrap,
)


class OrganizationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1)
    language: StrictStr = Field(pattern=r"^[A-Za-z0-9-]{2,10}$")
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("An organization name is required.")
        return value


class BotOrganizationCreateRequest(OrganizationCreateRequest):
    # A comparison selector, never the source of identity or authorization.
    expected_creator_user_id: UUID


class OrganizationCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: StrictStr
    slug: StrictStr
    default_language: StrictStr
    default_currency: StrictStr
    creator_user_id: UUID
    membership_id: UUID | None
    owner_grant_id: UUID


class OrganizationCreateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: OrganizationCreateResponse
    meta: dict
    request_id: str


class OrganizationCreationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_user_id: UUID
    language_code: StrictStr | None
    languages: list[StrictStr]


class OrganizationCreationContextEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: OrganizationCreationContext
    meta: dict
    request_id: str


def require_organization_create_key(
    request: Request,
    value: Annotated[str, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Required. Retry the same operation with the same key and body.",
    )],
):
    if len(request.headers.getlist("Idempotency-Key")) != 1 or not value.strip():
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Idempotency key is invalid.",
        )
    return value


def get_organization_creation_rates():
    return get_platform_bot_rates()



def get_organization_creation_repository(session=Depends(get_api_session)):
    return build_organization_creation_repository(session)


def get_organization_creation_workflow(
    session=Depends(get_api_session),
    key=Depends(require_organization_create_key),
):
    try:
        return build_platform_organization_bootstrap(
            session=session,
            encryption_key=ApiIdempotencySettings.from_env().encryption_key,
        )
    except (ApiConfigurationError, ValueError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Organization creation is unavailable.",
        ) from None


async def require_creation_principal(
    principal=Depends(get_authenticated_platform_principal),
    rates=Depends(get_organization_creation_rates),
):
    if not isinstance(principal, ApiPlatformPrincipal):
        raise ApiHttpError(
            status_code=401, code="invalid_token",
            message="Platform authentication is required.",
        )
    try:
        await rates.ensure_platform_user_allowed(user_id=principal.user_id)
    except Exception as error:
        raise bot_failure(error) from None
    return principal


async def require_bot_creator(
    context=Depends(require_platform_bot_context),
    users=Depends(get_platform_bot_users),
    rates=Depends(get_organization_creation_rates),
):
    try:
        return await require_canonical_bot_user(context, users, rates)
    except Exception as error:
        raise bot_failure(error) from None


def reject_query(request):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Organization query parameters are not supported.",
        )


async def perform_creation(*, request, response, body, principal, workflow, key):
    reject_query(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        result = await workflow.create(
            principal=principal, name=body.name,
            language=body.language, currency=body.currency,
            idempotency_key=key, trace_id=request.state.request_id,
        )
        item = OrganizationCreateResponse.model_validate(result)
        if (
            item.creator_user_id != principal.user_id
            or item.name != body.name.strip()
            or item.default_language != body.language
            or item.default_currency != body.currency
            or item.slug != "org-" + item.id.hex
        ):
            raise OrganizationBootstrapError("Organization receipt binding differs.")
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(
            status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with a different request.",
        ) from None
    except OrganizationBootstrapInProgress:
        raise ApiHttpError(
            status_code=409, code="idempotency_in_progress",
            message="Organization creation is processing.",
        ) from None
    except OrganizationBootstrapDenied:
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Organization creation is unavailable.",
        ) from None
    except OrganizationBootstrapValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Organization creation request is invalid.",
        ) from None
    except OrganizationBootstrapError:
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Organization creation is unavailable.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Organization creation failed.",
        ) from None
    return success_envelope(
        data=item.model_dump(mode="json"),
        request_id=request.state.request_id,
    )


router = APIRouter(prefix="/platform/organizations")
bot_router = APIRouter(
    prefix="/platform/bot", include_in_schema=False,
    dependencies=[Depends(require_platform_bot_context)],
)


@router.post(
    "", status_code=201, response_model=OrganizationCreateEnvelope,
    openapi_extra={"x-idempotency-key-required": True},
    responses={
        401: {"description": "Platform authentication required"},
        403: {"description": "Active verified identity required"},
        409: {"description": "Idempotency conflict or processing"},
        422: {"description": "Invalid request"},
        429: {"description": "Platform rate limit exceeded"},
        503: {"description": "Bootstrap unavailable"},
    },
)
async def create_organization(
    body: OrganizationCreateRequest, request: Request, response: Response,
    principal=Depends(require_creation_principal),
    workflow=Depends(get_organization_creation_workflow),
    key=Depends(require_organization_create_key),
):
    return await perform_creation(
        request=request, response=response, body=body,
        principal=principal, workflow=workflow, key=key,
    )


@bot_router.get(
    "/organization-creation-context",
    response_model=OrganizationCreationContextEnvelope,
)
async def organization_creation_context(
    request: Request, response: Response,
    user=Depends(require_bot_creator),
    repository=Depends(get_organization_creation_repository),
):
    reject_query(request)
    try:
        languages = await read_organization_creation_languages(repository)
        if (
            not isinstance(languages, list)
            or len(set(languages)) != len(languages)
            or any(
                not isinstance(code, str)
                or re.fullmatch(r"[A-Za-z0-9-]{2,10}", code) is None
                for code in languages
            )
        ):
            raise ValueError("Organization languages are invalid.")
        item = OrganizationCreationContext(
            actor_user_id=user.id,
            language_code=user.language_code,
            languages=languages,
        )
    except Exception:
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Organization creation context is unavailable.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=item.model_dump(mode="json"),
        request_id=request.state.request_id,
    )


@bot_router.post(
    "/organizations", status_code=201,
    response_model=OrganizationCreateEnvelope,
)
async def create_bot_organization(
    body: BotOrganizationCreateRequest, request: Request, response: Response,
    user=Depends(require_bot_creator),
    workflow=Depends(get_organization_creation_workflow),
    key=Depends(require_organization_create_key),
):
    if body.expected_creator_user_id != user.id:
        raise ApiHttpError(
            status_code=403, code="account_unavailable",
            message="Platform identity changed.",
        )
    return await perform_creation(
        request=request, response=response, body=body,
        principal=ApiPlatformPrincipal(user.id), workflow=workflow, key=key,
    )
