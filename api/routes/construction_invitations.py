from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt
from api.auth import get_current_actor
from api.construction_recipient_rate_limit import require_invitation_recipient_rate_limit
from api.dependencies import get_api_session, get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.settings import ApiIdempotencySettings
from database.construction_session import construction_transaction
from services.construction.construction_invitation_acceptance_workflow import PlatformInvitationMembershipConflictError, PlatformInvitationMembershipUnavailableError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_invitation_acceptance_workflow import build_construction_invitation_acceptance_workflow
from services.construction.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec
from services.construction.construction_invitation_workflows import ConstructionInvitationIdempotencyInProgressError
from services.construction.construction_invitations import build_construction_invitation_identity_reader, ConstructionInvitationOperationError, ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, ConstructionInvitationValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.module_access import ModuleDisabledError, SuiteDisabledError

class InvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    invitation_context: SecretStr = Field(min_length=1, max_length=4096)
    token: SecretStr = Field(min_length=1, max_length=1024)
    expected_row_version: StrictInt = Field(gt=0)

@dataclass(frozen=True)
class InvitationAcceptInput:
    actor: object
    body: InvitationAcceptRequest = field(repr=False)
    idempotency_key: str = field(repr=False)

def get_invitation_crypto_settings():
    return ApiIdempotencySettings.from_env()

def get_recipient_context_codec(settings=Depends(get_invitation_crypto_settings)):
    return ConstructionInvitationRecipientContextCodec(encryption_key=settings.encryption_key)

def get_acceptance_input(body: InvitationAcceptRequest, idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=255)], actor=Depends(get_current_actor)):
    if getattr(actor, 'status', None) != 'active' or not isinstance(getattr(actor, 'user_id', None), UUID):
        raise ApiHttpError(status_code=403, code='forbidden', message='Invitation acceptance is not available.')
    if not idempotency_key.strip():
        raise ApiHttpError(status_code=422, code='validation_error', message='Idempotency key is required.')
    return InvitationAcceptInput(actor=actor, body=body, idempotency_key=idempotency_key)

def get_recipient_context(inputs=Depends(get_acceptance_input), codec=Depends(get_recipient_context_codec)):
    try:
        return codec.resolve(invitation_context=inputs.body.invitation_context.get_secret_value(), token=inputs.body.token.get_secret_value())
    except ConstructionInvitationTokenDeniedError:
        raise ApiHttpError(status_code=403, code='forbidden', message='Invitation acceptance is not available.') from None

async def require_invitation_module(context=Depends(get_recipient_context), suite_service=Depends(get_tenant_suite_access_service), module_service=Depends(get_tenant_module_access_service)):
    try:
        await suite_service.require_enabled(tenant_id=context.tenant_id, suite_code='construction')
        await module_service.require_enabled(tenant_id=context.tenant_id, module_code='construction.core')
    except (SuiteDisabledError, ModuleDisabledError):
        raise ApiHttpError(status_code=403, code='MODULE_DISABLED', message='Required module is not available.') from None
    return context

async def get_acceptance_scoped_session(context=Depends(require_invitation_module)):
    async with construction_transaction(tenant_id=context.tenant_id) as session:
        yield session

def get_acceptance_workflow(construction_session=Depends(get_acceptance_scoped_session), platform_session=Depends(get_api_session), settings=Depends(get_invitation_crypto_settings)):
    if construction_session is platform_session:
        raise ApiHttpError(status_code=500, code='internal_error', message='Invitation acceptance is not available.')
    return build_construction_invitation_acceptance_workflow(session=construction_session, identity_reader=build_construction_invitation_identity_reader(session=platform_session), idempotency_encryption_key=settings.encryption_key)

def _acceptance_error(exc):
    if isinstance(exc, ConstructionRowVersionConflictError):
        status, code, message = (409, 'ROW_VERSION_CONFLICT', 'Invitation has changed.')
    elif isinstance(exc, PlatformInvitationMembershipConflictError):
        status, code, message = (409, 'PLATFORM_MEMBERSHIP_CONFLICT', 'Platform membership requires an authorized Platform operation.')
    elif isinstance(exc, ApiIdempotencyKeyReusedError):
        status, code, message = (409, 'IDEMPOTENCY_KEY_REUSED', 'Idempotency key was reused with a different request.')
    elif isinstance(exc, ConstructionInvitationIdempotencyInProgressError):
        status, code, message = (409, exc.code, 'Invitation operation is still processing.')
    elif isinstance(exc, (ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, PlatformInvitationMembershipUnavailableError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        status, code, message = (403, 'forbidden', 'Invitation acceptance is not available.')
    elif isinstance(exc, ConstructionInvitationValidationError):
        status, code, message = (422, 'validation_error', 'Invitation request is invalid.')
    else:
        status, code, message = (500, 'internal_error', 'Invitation acceptance failed.')
    return ApiHttpError(status_code=status, code=code, message=message)

router = APIRouter(prefix='/construction/invitations', dependencies=[Depends(get_current_actor),
    Depends(require_invitation_recipient_rate_limit)],
    responses={429: {'description': 'RATE_LIMIT_EXCEEDED'}})

@router.post('/accept', dependencies=[Depends(require_invitation_module)])
async def accept_invitation(request: Request, inputs=Depends(get_acceptance_input), context=Depends(get_recipient_context), workflow=Depends(get_acceptance_workflow)):
    try:
        result = await workflow.accept_invitation(actor=inputs.actor, tenant_id=context.tenant_id, invitation_id=context.invitation_id, expected_row_version=inputs.body.expected_row_version, token=inputs.body.token.get_secret_value(), idempotency_key=inputs.idempotency_key, trace_id=request.state.request_id)
        if not isinstance(result, dict) or set(result) != {'id', 'status', 'row_version'} or result['id'] != str(context.invitation_id) or (result['status'] != 'accepted') or (type(result['row_version']) is not int) or (result['row_version'] != inputs.body.expected_row_version + 1):
            raise ConstructionInvitationOperationError('Accepted invitation response is invalid.')
    except Exception as exc:
        raise _acceptance_error(exc) from None
    return success_envelope(data=dict(result), request_id=request.state.request_id)
