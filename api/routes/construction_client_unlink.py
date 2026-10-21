"""Approved versioned client unlink by its identity owner or a CRM editor.

HTTP uses expected_row_version. Optional receipt workflows remain internal;
this endpoint does not advertise Idempotency-Key replay for unlink. Only the
requests/confirm HTTP operations require that separate protocol.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from api.auth import get_access_token_codec, get_api_identity_service, invalid_token_error, require_bearer_token
from api.dependencies import (get_api_session, get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service)
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.responses import success_envelope
from api.security import AccessTokenClaims, ApiAuthenticationError
from database.construction_client_unlink_context import construction_client_unlink_lookup_transaction
from database.construction_session import ConstructionDatabaseConfigurationError, construction_transaction
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.api_platform_principal import ApiPlatformPrincipal, ApiPlatformPrincipalOperationError
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction_client_unlink import ConstructionClientUnlinkDeniedError, ConstructionClientUnlinkResult
from services.construction_client_unlink_context import build_construction_client_own_link_context_service
from services.construction_client_unlink_workflow import build_construction_client_unlink_workflow
from services.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_grants import build_construction_access_context_resolver
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.module_access import ModuleDisabledError, SuiteDisabledError


class ClientUnlinkRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_row_version: StrictInt = Field(ge=1, le=2**63-1)


class ClientUnlinkResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_id: UUID
    linked_user_id: None
    row_version: StrictInt = Field(ge=1, le=2**63-1)
    changed: StrictBool


class ClientUnlinkEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: ClientUnlinkResponse
    meta: dict
    request_id: StrictStr


@dataclass(frozen=True)
class ClientUnlinkInput:
    principal: ApiPlatformPrincipal
    client_id: UUID
    expected_row_version: int
    claims: AccessTokenClaims


@dataclass(frozen=True)
class ClientUnlinkRoute:
    actor: ApiActorContext
    tenant_id: UUID
    client_id: UUID
    as_editor: bool


def _http(status, code, message):
    return ApiHttpError(status_code=status, code=code, message=message,
        headers={'Cache-Control': 'private, no-store'})


@contextmanager
def unlink_errors():
    try:
        yield
    except ApiHttpError:
        raise
    except ConstructionRowVersionConflictError:
        raise _http(409, 'ROW_VERSION_CONFLICT', 'Construction client has changed.') from None
    except (ConstructionClientUnlinkDeniedError, ConstructionClientNotFoundError):
        raise _http(404, 'not_found', 'Client link is not available.') from None
    except (ApiIdentityAccessError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise _http(403, 'forbidden', 'Client unlink access is not available.') from None
    except (SuiteDisabledError, ModuleDisabledError):
        raise _http(403, 'MODULE_DISABLED', 'Required module is not available.') from None
    except ApiRequestRateLimitExceededError:
        raise _http(429, 'RATE_LIMIT_EXCEEDED', 'Rate limit exceeded.') from None
    except ConstructionClientValidationError:
        raise _http(422, 'validation_error', 'Client unlink request is invalid.') from None
    except (ConstructionDatabaseConfigurationError, ApiPlatformPrincipalOperationError):
        raise _http(503, 'service_unavailable', 'Client unlink is not available.') from None
    except Exception:
        raise _http(500, 'internal_error', 'Client unlink failed.') from None


def get_client_unlink_input(id: UUID, body: ClientUnlinkRequest, request: Request,
    principal=Depends(get_authenticated_platform_principal), token=Depends(require_bearer_token),
    codec=Depends(get_access_token_codec)):
    if request.query_params:
        raise _http(422, 'validation_error', 'Client unlink query parameters are not supported.')
    try:
        claims = codec.decode_access_token(token)
        if (not isinstance(principal, ApiPlatformPrincipal) or not isinstance(claims, AccessTokenClaims)
                or claims.user_id != principal.user_id or not isinstance(claims.tenant_id, UUID)):
            raise ApiAuthenticationError('Invalid unlink principal.')
    except ApiAuthenticationError:
        raise invalid_token_error() from None
    return ClientUnlinkInput(principal, id, body.expected_row_version, claims)


async def get_client_unlink_route(inputs=Depends(get_client_unlink_input), platform_session=Depends(get_api_session),
    identity=Depends(get_api_identity_service), rate=Depends(get_api_request_rate_limit_service),
    suites=Depends(get_tenant_suite_access_service), modules=Depends(get_tenant_module_access_service)):
    with unlink_errors():
        # A verified token tenant is only the existing rate-limit bucket here.
        # It cannot authorize a lookup or a mutation for the linked owner.
        await rate.ensure_authenticated_user_allowed(tenant_id=inputs.claims.tenant_id, user_id=inputs.principal.user_id)
        own = None
        async with construction_client_unlink_lookup_transaction(
            user_id=inputs.principal.user_id, client_id=inputs.client_id) as session:
            service = build_construction_client_own_link_context_service(session=session, platform_session=platform_session)
            try:
                own = await service.resolve_own_link(principal=inputs.principal, client_id=inputs.client_id)
            except ConstructionClientUnlinkDeniedError:
                pass
        # The read-only lookup has ended before the independent write session.
        if own is not None:
            if (own.user_id != inputs.principal.user_id or own.client_id != inputs.client_id
                    or not isinstance(own.tenant_id, UUID)):
                raise ValueError('Invalid own-link routing result.')
            actor = ApiActorContext(inputs.principal.user_id, None, None, (), 'ru', None, 'active')
            return ClientUnlinkRoute(actor, own.tenant_id, own.client_id, False)
        # Only this fallback uses the normal signed tenant claim, membership,
        # and Construction permissions. Ownership never becomes edit access.
        try:
            actor = await identity.require_actor(user_id=inputs.principal.user_id, tenant_id=inputs.claims.tenant_id)
        except ApiIdentityAccessError:
            raise ConstructionClientUnlinkDeniedError() from None
        if (not isinstance(actor, ApiActorContext) or actor.user_id != inputs.principal.user_id
                or actor.tenant_id != inputs.claims.tenant_id or actor.status != 'active'):
            raise ConstructionClientUnlinkDeniedError()
        await suites.require_enabled(tenant_id=actor.tenant_id, suite_code='construction')
        await modules.require_enabled(tenant_id=actor.tenant_id, module_code='construction.core')
        return ClientUnlinkRoute(actor, actor.tenant_id, inputs.client_id, True)


async def get_client_unlink_session(route=Depends(get_client_unlink_route)):
    with unlink_errors():
        async with construction_transaction(tenant_id=route.tenant_id) as session:
            yield session


async def get_client_unlink_execution(route=Depends(get_client_unlink_route),
    session=Depends(get_client_unlink_session), platform_session=Depends(get_api_session)):
    with unlink_errors():
        context = None
        if route.as_editor:
            context = await build_construction_access_context_resolver(session).resolve(actor=route.actor)
        workflow = build_construction_client_unlink_workflow(session=session, platform_session=platform_session)
        return workflow, context


def public_unlink(result, *, route, inputs):
    if (not isinstance(result, ConstructionClientUnlinkResult) or result.client_id != inputs.client_id
            or result.tenant_id != route.tenant_id or result.linked_user_id is not None
            or type(result.changed) is not bool or type(result.row_version) is not int
            or result.row_version != inputs.expected_row_version + int(result.changed)
            or (result.changed and (not isinstance(result.previous_linked_user_id, UUID) or not isinstance(result.audit_id, UUID)))
            or (not result.changed and (result.previous_linked_user_id is not None or result.audit_id is not None))
            or (not route.as_editor and (not result.changed or result.previous_linked_user_id != inputs.principal.user_id))):
        raise ValueError('Client unlink receipt is invalid.')
    return ClientUnlinkResponse.model_validate({name: getattr(result, name)
        for name in ClientUnlinkResponse.model_fields}).model_dump(mode='json')


router = APIRouter(prefix='/construction/clients')


@router.post('/{id}/user-link/unlink', response_model=ClientUnlinkEnvelope,
    responses={401: {'description': 'Platform authentication required'},
        403: {'description': 'Editor access or module unavailable'}, 404: {'description': 'Client link unavailable'},
        409: {'description': 'ROW_VERSION_CONFLICT'}, 422: {'description': 'Invalid versioned unlink request'},
        429: {'description': 'Rate limit exceeded'}, 500: {'description': 'Client unlink failed'},
        503: {'description': 'Construction lookup runtime unavailable'}})
async def unlink_client_user(request: Request, response: Response, inputs=Depends(get_client_unlink_input),
    route=Depends(get_client_unlink_route), execution=Depends(get_client_unlink_execution)):
    with unlink_errors():
        workflow, context = execution
        common = dict(actor=route.actor, client_id=inputs.client_id, expected_row_version=inputs.expected_row_version,
            idempotency_key=None, trace_id=request.state.request_id)
        result = await (workflow.unlink_as_editor(**common, access_context=context) if route.as_editor
            else workflow.unlink_self(**common, tenant_id=route.tenant_id))
        data = public_unlink(result, route=route, inputs=inputs)
    response.headers['Cache-Control'] = 'private, no-store'
    return success_envelope(data=data, request_id=request.state.request_id)
