"""Approved operator-link confirmation by the authenticated target user."""
from dataclasses import dataclass, field
from datetime import timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt, StrictStr

from api.dependencies import get_api_session, get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.responses import success_envelope
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from database.construction_session import construction_transaction
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.api_platform_principal import ApiPlatformPrincipal
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction.construction_client_link_confirmation import ConstructionClientLinkConfirmationDeniedError, ConstructionClientLinkConfirmationResult
from services.construction.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError, build_construction_client_link_confirmation_workflow
from services.construction.construction_client_link_recipient_context import ConstructionClientLinkRecipientContext, ConstructionClientLinkRecipientContextCodec
from services.construction.construction_clients import ConstructionClientValidationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.module_access import ModuleDisabledError, SuiteDisabledError


class ClientConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient_context: SecretStr = Field(min_length=1, max_length=ConstructionClientLinkRecipientContextCodec.MAX_CONTEXT_LENGTH)
    token: SecretStr = Field(min_length=48, max_length=48)


class ClientConfirmationResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    tenant_id: UUID
    client_id: UUID
    linked_user_id: UUID
    client_row_version: StrictInt = Field(ge=1, le=2**63-1)
    request_row_version: StrictInt = Field(ge=1, le=2**63-1)
    consumed_at: AwareDatetime
    client_changed: StrictBool


class ClientConfirmationEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: ClientConfirmationResponse
    meta: dict
    request_id: StrictStr


@dataclass(frozen=True)
class ClientConfirmationInput:
    principal: ApiPlatformPrincipal
    body: ClientConfirmationRequest = field(repr=False)
    key: str = field(repr=False)


def get_confirmation_input(request: Request, body: ClientConfirmationRequest,
    key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=255)],
    principal=Depends(get_authenticated_platform_principal)):
    if not isinstance(principal, ApiPlatformPrincipal) or not isinstance(principal.user_id, UUID):
        raise ApiHttpError(status_code=401, code='invalid_token', message='Authenticated Platform identity is not available.')
    if request.query_params or len(request.headers.getlist('Idempotency-Key')) != 1 or not key.strip():
        raise ApiHttpError(status_code=422, code='validation_error', message='Client confirmation request is invalid.')
    return ClientConfirmationInput(principal, body, key)


def get_confirmation_crypto_settings():
    try: return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Client confirmation is not configured.') from None


def get_confirmation_context_codec(settings=Depends(get_confirmation_crypto_settings)):
    try: return ConstructionClientLinkRecipientContextCodec(encryption_key=settings.encryption_key)
    except (ValueError, TypeError):
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Client confirmation is not configured.') from None


def get_confirmation_context(inputs=Depends(get_confirmation_input), codec=Depends(get_confirmation_context_codec)):
    try:
        route = codec.resolve(recipient_context=inputs.body.recipient_context.get_secret_value(), token=inputs.body.token.get_secret_value())
        if not isinstance(route, ConstructionClientLinkRecipientContext) or route.target_user_id != inputs.principal.user_id:
            raise ConstructionClientLinkConfirmationDeniedError()
        return route
    except ConstructionClientLinkConfirmationDeniedError:
        raise ApiHttpError(status_code=403, code='forbidden', message='Client confirmation is not available.') from None


async def require_confirmation_context(route=Depends(get_confirmation_context),
    inputs=Depends(get_confirmation_input), rate=Depends(get_api_request_rate_limit_service),
    suites=Depends(get_tenant_suite_access_service), modules=Depends(get_tenant_module_access_service)):
    try:
        await rate.ensure_authenticated_user_allowed(tenant_id=route.tenant_id, user_id=inputs.principal.user_id)
        await suites.require_enabled(tenant_id=route.tenant_id, suite_code='construction')
        await modules.require_enabled(tenant_id=route.tenant_id, module_code='construction.core')
    except ApiRequestRateLimitExceededError:
        raise ApiHttpError(status_code=429, code='RATE_LIMIT_EXCEEDED', message='Rate limit exceeded.') from None
    except (SuiteDisabledError, ModuleDisabledError):
        raise ApiHttpError(status_code=403, code='MODULE_DISABLED', message='Required module is not available.') from None
    return route


async def get_confirmation_scoped_session(route=Depends(require_confirmation_context)):
    # Tenant is authenticated by the server-issued envelope, never a body ID,
    # JWT selected tenant, or membership inferred from the identity link.
    async with construction_transaction(tenant_id=route.tenant_id) as session:
        yield session


def get_confirmation_workflow(session=Depends(get_confirmation_scoped_session),
    platform_session=Depends(get_api_session), settings=Depends(get_confirmation_crypto_settings)):
    return build_construction_client_link_confirmation_workflow(
        session=session, platform_session=platform_session, encryption_key=settings.encryption_key)


def public_confirmation(result, *, route, principal):
    if (not isinstance(result, ConstructionClientLinkConfirmationResult)
            or any(not isinstance(getattr(result, name), UUID) for name in ('request_id', 'tenant_id', 'client_id', 'linked_user_id', 'request_audit_id'))
            or (result.request_id, result.tenant_id, result.client_id, result.linked_user_id) != (
                route.request_id, route.tenant_id, route.client_id, principal.user_id)
            or result.linked_user_id != route.target_user_id or type(result.client_changed) is not bool
            or type(result.request_row_version) is not int or result.request_row_version != route.expected_request_row_version+1
            or (result.client_changed and not isinstance(result.client_audit_id, UUID))
            or (not result.client_changed and result.client_audit_id is not None)):
        raise ValueError('Client confirmation receipt is invalid.')
    data = ClientConfirmationResponse.model_validate({name: getattr(result, name) for name in ClientConfirmationResponse.model_fields})
    return data.model_copy(update={'consumed_at': data.consumed_at.astimezone(timezone.utc)}).model_dump(mode='json')


router = APIRouter(prefix='/construction/client-user-link-requests')


@router.post('/confirm', response_model=ClientConfirmationEnvelope,
    dependencies=[Depends(require_confirmation_context)],
    responses={401: {'description': 'Platform authentication required or invalid token'},
        403: {'description': 'Recipient proof, module or initiator authority unavailable'},
        409: {'description': 'ROW_VERSION_CONFLICT, IDEMPOTENCY_KEY_REUSED or idempotency_in_progress'},
        422: {'description': 'Invalid request or missing Idempotency-Key'}, 429: {'description': 'Rate limit exceeded'},
        500: {'description': 'Client confirmation failed'}, 503: {'description': 'Confirmation is not configured'}})
async def confirm_client_link(request: Request, response: Response,
    inputs=Depends(get_confirmation_input), route=Depends(require_confirmation_context), workflow=Depends(get_confirmation_workflow)):
    # This adapter creates no membership, role or tenant authority on the target.
    actor = ApiActorContext(inputs.principal.user_id, None, None, (), 'ru', None, 'active')
    try:
        result = await workflow.confirm_request(actor=actor, recipient_context=inputs.body.recipient_context.get_secret_value(),
            token=inputs.body.token.get_secret_value(), idempotency_key=inputs.key, trace_id=request.state.request_id)
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code='ROW_VERSION_CONFLICT', message='Construction client has changed.') from None
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code='IDEMPOTENCY_KEY_REUSED', message='Idempotency key was reused with a different request.') from None
    except ConstructionClientLinkIdempotencyInProgressError:
        raise ApiHttpError(status_code=409, code='idempotency_in_progress', message='Client confirmation is processing.') from None
    except (ConstructionClientLinkConfirmationDeniedError, ApiIdentityAccessError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code='forbidden', message='Client confirmation is not available.') from None
    except ConstructionClientValidationError:
        raise ApiHttpError(status_code=422, code='validation_error', message='Client confirmation request is invalid.') from None
    except Exception:
        raise ApiHttpError(status_code=500, code='internal_error', message='Client confirmation failed.') from None
    try: data = public_confirmation(result, route=route, principal=inputs.principal)
    except Exception:
        raise ApiHttpError(status_code=500, code='internal_error', message='Client confirmation failed.') from None
    response.headers['Cache-Control'] = 'private, no-store'
    return success_envelope(data=data, request_id=request.state.request_id)
