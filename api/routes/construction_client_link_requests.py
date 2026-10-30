"""Approved operator request issuance; target confirmation remains separate."""
from dataclasses import dataclass, field
from datetime import timezone
import hmac
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, require_authenticated_user_rate_limit, require_module, require_suite
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context, get_construction_scoped_session,
    require_construction_permission,
)
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from services.api_identity import ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction.construction_client_link_email_queue import (
    build_construction_client_link_email_issuance_workflow,
)
from services.construction.construction_client_link_email import (
    ClientLinkPlatformUserNotFoundError, normalize_client_link_email,
)
from services.construction.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec
from services.construction.construction_client_link_request_issuance import (
    CLIENT_LINK_CONFIRMATION_TTL, ConstructionClientLinkIssuanceResult, hash_client_link_confirmation_token,
)
from services.construction.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class ClientLinkRequestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_email: StrictStr = Field(min_length=1)
    expected_row_version: StrictInt = Field(ge=1, le=2**63-1)
    previous_user_id: UUID | None = None

    @field_validator("target_email")
    @classmethod
    def normalized_exact_email(cls, value):
        try:
            return normalize_client_link_email(value)
        except ConstructionClientValidationError:
            raise ValueError("Target email is invalid.") from None


class ClientLinkRequestIssuedResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    tenant_id: UUID
    client_id: UUID
    target_user_id: UUID
    row_version: StrictInt = Field(ge=1, le=1)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    recipient_context: StrictStr = Field(min_length=1, max_length=4096, repr=False)
    token: StrictStr = Field(min_length=48, max_length=48, repr=False)


class ClientLinkRequestQueuedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    tenant_id: UUID
    client_id: UUID
    target_user_id: UUID
    row_version: StrictInt = Field(ge=1, le=1)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    delivery_status: Literal["queued"]


class ClientLinkRequestIssuedEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: ClientLinkRequestQueuedResponse
    meta: dict
    request_id: StrictStr


@dataclass(frozen=True)
class ClientLinkRequestRuntime:
    workflow: object = field(repr=False)
    context_codec: ConstructionClientLinkRecipientContextCodec = field(repr=False)


def require_client_link_request_key(request: Request, value: Annotated[str, Header(
    alias='Idempotency-Key', min_length=1, max_length=255,
    description='Required. The same key and request replay the original pending confirmation receipt.',
)]):
    if request.query_params or len(request.headers.getlist('Idempotency-Key')) != 1 or not value.strip():
        raise ApiHttpError(status_code=422, code='validation_error', message='Client link request is invalid.')
    return value


def get_client_link_request_settings(_key=Depends(require_client_link_request_key)):
    try:
        return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Client link issuance is not configured.') from None


def get_client_link_request_runtime(key=Depends(require_client_link_request_key),
    settings=Depends(get_client_link_request_settings), session=Depends(get_construction_scoped_session),
    platform_session=Depends(get_api_session)):
    try:
        return ClientLinkRequestRuntime(
            build_construction_client_link_email_issuance_workflow(session=session, platform_session=platform_session,
                encryption_key=settings.encryption_key),
            ConstructionClientLinkRecipientContextCodec(encryption_key=settings.encryption_key))
    except (ValueError, TypeError):
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Client link issuance is not configured.') from None


def public_client_link_request(material, *, actor, client_id, body, codec):
    if not isinstance(material, ConstructionClientLinkIssuanceResult):
        raise ValueError("Client link issuance receipt is invalid.")
    state = material.request
    if (
        not isinstance(material.audit_id, UUID)
        or not isinstance(state.target_user_id, UUID)
        or state.target_user_id == actor.user_id
        or (
            state.tenant_id, state.client_id, state.initiated_by_user_id
        ) != (actor.tenant_id, client_id, actor.user_id)
        or state.expected_client_row_version != body.expected_row_version
        or (
            body.previous_user_id is not None
            and state.expected_linked_user_id != body.previous_user_id
        )
        or (
            body.previous_user_id is None
            and state.expected_linked_user_id not in (None, state.target_user_id)
        )
        or state.expires_at - state.issued_at != CLIENT_LINK_CONFIRMATION_TTL
        or state.updated_at != state.issued_at
        or type(state.row_version) is not int or state.row_version != 1
        or state.consumed_at is not None or state.invalidated_at is not None
        or not hmac.compare_digest(
            state.token_hash,
            hash_client_link_confirmation_token(material.token),
        )
    ):
        raise ValueError("Client link issuance receipt is invalid.")
    # The original queued receipt is replayed as originally issued. This
    # status acknowledges enqueueing and does not claim email delivery.
    return ClientLinkRequestQueuedResponse(
        request_id=state.request_id, tenant_id=state.tenant_id,
        client_id=state.client_id, target_user_id=state.target_user_id,
        row_version=state.row_version,
        issued_at=state.issued_at.astimezone(timezone.utc),
        expires_at=state.expires_at.astimezone(timezone.utc),
        delivery_status="queued",
    ).model_dump(mode="json")


edit_permission = require_construction_permission('construction.projects.edit')
actor_rate_limit = require_authenticated_user_rate_limit()
router = ConstructionAPIRouter(prefix='/construction/clients', dependencies=[
    Depends(get_current_actor), Depends(require_suite('construction', error_code='MODULE_DISABLED')),
    Depends(require_module('construction.core', error_code='MODULE_DISABLED')),
])


@router.post('/{id}/user-link/requests', status_code=201, response_model=ClientLinkRequestIssuedEnvelope,
    dependencies=[Depends(edit_permission), Depends(actor_rate_limit)],
    responses={401: {'description': 'Platform authentication required or invalid token'},
        403: {'description': 'Module disabled or client edit access unavailable'},
        404: {'description': 'Client unavailable, or PLATFORM_USER_NOT_FOUND for the exact verified email'},
        409: {'description': 'ROW_VERSION_CONFLICT, IDEMPOTENCY_KEY_REUSED or idempotency_in_progress'},
        422: {'description': 'Invalid request or missing Idempotency-Key'}, 429: {'description': 'Rate limit exceeded'},
        500: {'description': 'Client link issuance failed'}, 503: {'description': 'Issuance is not configured'}})
async def issue_client_link_request(id: UUID, body: ClientLinkRequestBody, request: Request, response: Response,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    key=Depends(require_client_link_request_key), runtime=Depends(get_client_link_request_runtime)):
    try:
        material = await runtime.workflow.request_email_link(
            actor=actor, access_context=access_context, client_id=id,
            target_email=body.target_email,
            expected_row_version=body.expected_row_version,
            previous_user_id=body.previous_user_id,
            idempotency_key=key, trace_id=request.state.request_id,
        )
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code='ROW_VERSION_CONFLICT', message='Construction client has changed.') from None
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code='IDEMPOTENCY_KEY_REUSED', message='Idempotency key was reused with a different request.') from None
    except ConstructionClientLinkIdempotencyInProgressError:
        raise ApiHttpError(status_code=409, code='idempotency_in_progress', message='Client link issuance is processing.') from None
    except (ApiIdentityAccessError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code='forbidden', message='Client link issuance access is not available.') from None
    except ClientLinkPlatformUserNotFoundError:
        raise ApiHttpError(
            status_code=404, code="PLATFORM_USER_NOT_FOUND",
            message="Verified Platform user is not available.",
        ) from None
    except ConstructionClientNotFoundError:
        raise ApiHttpError(status_code=404, code='not_found', message='Construction client is not available.') from None
    except ConstructionClientValidationError:
        raise ApiHttpError(status_code=422, code='validation_error', message='Client link request is invalid.') from None
    except Exception:
        raise ApiHttpError(status_code=500, code='internal_error', message='Client link issuance failed.') from None
    try:
        data = public_client_link_request(material, actor=actor, client_id=id, body=body, codec=runtime.context_codec)
    except Exception:
        raise ApiHttpError(status_code=500, code='internal_error', message='Client link issuance failed.') from None
    response.headers['Cache-Control'] = 'private, no-store'
    return success_envelope(data=data, request_id=request.state.request_id)

# client_link_email_http_v1
