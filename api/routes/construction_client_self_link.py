"""Approved authenticated client self-link; target identity is server-owned."""
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

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
from services.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction_client_self_link import ConstructionClientSelfLinkResult
from services.construction_client_self_link_workflow import build_construction_client_self_link_workflow
from services.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class ClientSelfLinkRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_row_version: StrictInt = Field(ge=1, le=2**63-1)
    previous_user_id: UUID | None = None


class ClientSelfLinkResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_id: UUID
    tenant_id: UUID
    linked_user_id: UUID
    row_version: StrictInt = Field(ge=1, le=2**63-1)
    changed: StrictBool


class ClientSelfLinkEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: ClientSelfLinkResponse
    meta: dict
    request_id: StrictStr


def optional_self_link_key(request: Request, value: Annotated[str | None, Header(
    alias='Idempotency-Key', min_length=1, max_length=255,
    description='Optional receipt replay; expected_row_version is required with or without a key.',
)] = None):
    if len(request.headers.getlist('Idempotency-Key')) > 1 or (value is not None and not value.strip()):
        raise ApiHttpError(status_code=422, code='validation_error', message='Idempotency key is invalid.')
    return value


def get_client_self_link_workflow(session=Depends(get_construction_scoped_session),
    platform_session=Depends(get_api_session), key=Depends(optional_self_link_key)):
    try:
        encryption_key = ApiIdempotencySettings.from_env().encryption_key if key is not None else None
        return build_construction_client_self_link_workflow(
            session=session, platform_session=platform_session, encryption_key=encryption_key)
    except ApiConfigurationError:
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Client self-link replay is not configured.') from None


def public_self_link(result, *, actor, client_id, body):
    if (not isinstance(result, ConstructionClientSelfLinkResult)
            or any(not isinstance(getattr(result, name), UUID) for name in ('client_id', 'tenant_id', 'linked_user_id'))
            or result.client_id != client_id or result.tenant_id != actor.tenant_id or result.linked_user_id != actor.user_id
            or type(result.changed) is not bool or type(result.row_version) is not int
            or result.row_version != body.expected_row_version + int(result.changed)
            or (result.changed and not isinstance(result.audit_id, UUID))
            or (not result.changed and result.audit_id is not None)
            or (body.previous_user_id is not None and not result.changed)):
        raise ValueError('Client self-link receipt is invalid.')
    return ClientSelfLinkResponse.model_validate({name: getattr(result, name)
        for name in ClientSelfLinkResponse.model_fields}).model_dump(mode='json')


edit_permission = require_construction_permission('construction.projects.edit')
actor_rate_limit = require_authenticated_user_rate_limit()
router = ConstructionAPIRouter(prefix='/construction/clients', dependencies=[
    Depends(get_current_actor), Depends(require_suite('construction', error_code='MODULE_DISABLED')),
    Depends(require_module('construction.core', error_code='MODULE_DISABLED')),
])


@router.post('/{id}/user-link/self', response_model=ClientSelfLinkEnvelope,
    dependencies=[Depends(edit_permission), Depends(actor_rate_limit)],
    responses={401: {'description': 'Authentication required or invalid token'},
        403: {'description': 'Module disabled or client edit access unavailable'},
        404: {'description': 'Client unavailable in the authorized tenant'},
        409: {'description': 'ROW_VERSION_CONFLICT, IDEMPOTENCY_KEY_REUSED or idempotency_in_progress'},
        422: {'description': 'Invalid self-link request'}, 429: {'description': 'Rate limit exceeded'},
        500: {'description': 'Client self-link failed'}, 503: {'description': 'Keyed replay is not configured'}})
async def link_client_to_self(id: UUID, body: ClientSelfLinkRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    key=Depends(optional_self_link_key), workflow=Depends(get_client_self_link_workflow)):
    try:
        if request.query_params:
            raise ConstructionClientValidationError('Self-link query parameters are not supported.')
        common = dict(actor=actor, access_context=access_context, client_id=id,
            expected_row_version=body.expected_row_version, idempotency_key=key, trace_id=request.state.request_id)
        result = await (workflow.replace_with_self(**common, previous_user_id=body.previous_user_id)
            if body.previous_user_id is not None else workflow.link_self(**common))
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code='ROW_VERSION_CONFLICT', message='Construction client has changed.') from None
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code='IDEMPOTENCY_KEY_REUSED', message='Idempotency key was reused with a different request.') from None
    except ConstructionClientLinkIdempotencyInProgressError:
        raise ApiHttpError(status_code=409, code='idempotency_in_progress', message='Client self-link is processing.') from None
    except (ApiIdentityAccessError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code='forbidden', message='Client self-link access is not available.') from None
    except ConstructionClientNotFoundError:
        raise ApiHttpError(status_code=404, code='not_found', message='Construction client is not available.') from None
    except ConstructionClientValidationError:
        raise ApiHttpError(status_code=422, code='validation_error', message='Client self-link request is invalid.') from None
    except Exception:
        raise ApiHttpError(status_code=500, code='internal_error', message='Client self-link failed.') from None
    try:
        data = public_self_link(result, actor=actor, client_id=id, body=body)
    except Exception:
        raise ApiHttpError(status_code=500, code='internal_error', message='Client self-link failed.') from None
    response.headers['Cache-Control'] = 'private, no-store'
    return success_envelope(data=data, request_id=request.state.request_id)
