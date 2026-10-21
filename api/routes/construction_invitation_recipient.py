from dataclasses import dataclass, field
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from api.auth import get_current_actor
from api.construction_recipient_rate_limit import require_invitation_recipient_rate_limit
from api.dependencies import get_api_session, get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction_invitations import InvitationAcceptRequest, get_recipient_context_codec, require_invitation_module, _acceptance_error
from database.construction_session import construction_transaction
from services.construction_invitation_decline_workflow import build_construction_invitation_decline_workflow
from services.construction_invitation_preview import build_construction_invitation_preview_service
from services.construction_invitations import build_construction_invitation_identity_reader, ConstructionInvitationOperationError, ConstructionInvitationTokenDeniedError

class InvitationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    invitation_context: SecretStr = Field(min_length=1, max_length=4096)
    token: SecretStr = Field(min_length=1, max_length=1024)

@dataclass(frozen=True)
class RecipientInput:
    actor: object
    body: object = field(repr=False)

def _input(body, actor):
    if getattr(actor, 'status', None) != 'active' or not isinstance(getattr(actor, 'user_id', None), UUID):
        raise ApiHttpError(status_code=403, code='forbidden', message='Invitation is not available.')
    return RecipientInput(actor=actor, body=body)

def get_preview_input(body: InvitationPreviewRequest, actor=Depends(get_current_actor)):
    return _input(body, actor)

def get_decline_input(body: InvitationAcceptRequest, actor=Depends(get_current_actor)):
    return _input(body, actor)

def _context_dependency(input_dependency):

    def dependency(inputs=Depends(input_dependency), codec=Depends(get_recipient_context_codec)):
        try:
            return codec.resolve(invitation_context=inputs.body.invitation_context.get_secret_value(), token=inputs.body.token.get_secret_value())
        except ConstructionInvitationTokenDeniedError:
            raise ApiHttpError(status_code=403, code='forbidden', message='Invitation is not available.') from None
    return dependency
get_preview_context = _context_dependency(get_preview_input)
get_decline_context = _context_dependency(get_decline_input)

def _module_dependency(context_dependency):

    async def dependency(context=Depends(context_dependency), suite_service=Depends(get_tenant_suite_access_service), module_service=Depends(get_tenant_module_access_service)):
        return await require_invitation_module(context=context, suite_service=suite_service, module_service=module_service)
    return dependency
require_preview_module = _module_dependency(get_preview_context)
require_decline_module = _module_dependency(get_decline_context)

def _session_dependency(module_dependency):

    async def dependency(context=Depends(module_dependency)):
        async with construction_transaction(tenant_id=context.tenant_id) as session:
            yield session
    return dependency
get_preview_session = _session_dependency(require_preview_module)
get_decline_session = _session_dependency(require_decline_module)

def _identity(session, platform_session):
    if session is platform_session:
        raise ApiHttpError(status_code=500, code='internal_error', message='Invitation operation failed.')
    return build_construction_invitation_identity_reader(session=platform_session)

def get_preview_service(session=Depends(get_preview_session), platform_session=Depends(get_api_session)):
    return build_construction_invitation_preview_service(session=session, identity_reader=_identity(session, platform_session))

def get_decline_workflow(session=Depends(get_decline_session), platform_session=Depends(get_api_session)):
    return build_construction_invitation_decline_workflow(session=session, identity_reader=_identity(session, platform_session))

def _error(exc):
    mapped = _acceptance_error(exc)
    return ApiHttpError(status_code=mapped.status_code, code=mapped.code, message='Invitation is not available.' if mapped.status_code == 403 else 'Invitation operation failed.' if mapped.status_code == 500 else mapped.message)
router = APIRouter(prefix='/construction/invitations', dependencies=[Depends(get_current_actor),
    Depends(require_invitation_recipient_rate_limit)],
    responses={429: {'description': 'RATE_LIMIT_EXCEEDED'}})

@router.post('/preview', dependencies=[Depends(require_preview_module)])
async def preview_invitation(request: Request, inputs=Depends(get_preview_input), context=Depends(get_preview_context), service=Depends(get_preview_service)):
    try:
        result = await service.preview(actor=inputs.actor, tenant_id=context.tenant_id, invitation_id=context.invitation_id, token=inputs.body.token.get_secret_value())
        if not isinstance(result, dict) or set(result) != {'id', 'role', 'status', 'scopes', 'expires_at', 'grant_expires_at', 'row_version'} or result['id'] != str(context.invitation_id) or (result['status'] != 'pending') or (type(result['row_version']) is not int) or (result['row_version'] <= 0):
            raise ConstructionInvitationOperationError('Preview response is invalid.')
    except Exception as exc:
        raise _error(exc) from None
    return success_envelope(data=dict(result), request_id=request.state.request_id)

@router.post('/decline', dependencies=[Depends(require_decline_module)])
async def decline_invitation(request: Request, inputs=Depends(get_decline_input), context=Depends(get_decline_context), workflow=Depends(get_decline_workflow)):
    try:
        result = await workflow.decline_invitation(actor=inputs.actor, tenant_id=context.tenant_id, invitation_id=context.invitation_id, expected_row_version=inputs.body.expected_row_version, token=inputs.body.token.get_secret_value(), trace_id=request.state.request_id)
        if not isinstance(result, dict) or set(result) != {'id', 'status', 'row_version'} or result['id'] != str(context.invitation_id) or (result['status'] != 'declined') or (type(result['row_version']) is not int) or (result['row_version'] != inputs.body.expected_row_version + 1):
            raise ConstructionInvitationOperationError('Decline response is invalid.')
    except Exception as exc:
        raise _error(exc) from None
    return success_envelope(data=dict(result), request_id=request.state.request_id)
