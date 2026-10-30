from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_invitations import get_invitation_crypto_settings, _acceptance_error
from services.construction.construction_invitation_workflows import build_construction_invitation_workflow
from services.construction.construction_invitations import ConstructionInvitationOperationError, ConstructionInvitationTransitionError

Role = Literal["OWNER", "ADMIN", "SURVEYOR", "ESTIMATOR", "PROJECT_MANAGER", "EXECUTOR", "OBSERVER"]

class InvitationScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_type: Literal["tenant", "project"]
    scope_id: UUID

class InvitationDeadlines(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expires_at: AwareDatetime | None = None

    @field_validator("expires_at", mode="before")
    @classmethod
    def require_datetime_input(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Expiration must be an ISO datetime.")
        return value

class InvitationCreateRequest(InvitationDeadlines):
    email: StrictStr = Field(min_length=1, max_length=320)
    role: Role
    scopes: list[InvitationScopeRequest] = Field(min_length=1)
    grant_expires_at: AwareDatetime | None = None

    @field_validator("grant_expires_at", mode="before")
    @classmethod
    def require_grant_datetime_input(cls, value):
        return cls.require_datetime_input(value)

class InvitationResendRequest(InvitationDeadlines):
    expected_row_version: StrictInt = Field(gt=0)

class InvitationRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt = Field(gt=0)

class InvitationPublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    email: StrictStr
    role: Role
    status: Literal["pending", "revoked"]
    scopes: list[InvitationScopeRequest] = Field(min_length=1)
    expires_at: AwareDatetime
    grant_expires_at: AwareDatetime | None
    row_version: StrictInt = Field(gt=0)

def require_inviter_idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
):
    if not value.strip():
        raise ApiHttpError(status_code=422, code="validation_error", message="Idempotency key is required.")
    return value

def get_inviter_workflow(
    session=Depends(get_construction_scoped_session),
    settings=Depends(get_invitation_crypto_settings),
):
    return build_construction_invitation_workflow(
        session=session, idempotency_encryption_key=settings.encryption_key,
        delivery_encryption_key=settings.encryption_key,
    )

def _error(exc):
    if isinstance(exc, ConstructionInvitationTransitionError):
        return ApiHttpError(status_code=409, code="INVITATION_STATE_CONFLICT", message="Invitation state does not allow this operation.")
    mapped = _acceptance_error(exc)
    return ApiHttpError(
        status_code=mapped.status_code, code=mapped.code,
        message="Invitation management is not available." if mapped.status_code == 403
        else "Invitation operation failed." if mapped.status_code == 500 else mapped.message,
    )

def _public(result, *, status, invitation_id=None, previous_version=None):
    keys = {"id", "email", "role", "status", "scopes", "expires_at", "grant_expires_at", "row_version"}
    if not isinstance(result, dict) or set(result) != keys:
        raise ConstructionInvitationOperationError("Invitation response is invalid.")
    response = InvitationPublicResponse.model_validate(result)
    if response.status != status or (invitation_id is not None and response.id != invitation_id):
        raise ConstructionInvitationOperationError("Invitation response is invalid.")
    if previous_version is not None and response.row_version <= previous_version:
        raise ConstructionInvitationOperationError("Invitation version is invalid.")
    return response.model_dump(mode="json")

manage_permission = require_construction_permission("construction.team.manage")
router = ConstructionAPIRouter(
    prefix="/construction/invitations",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)

@router.post("", status_code=201, dependencies=[Depends(manage_permission)])
async def create_invitation(
    request: Request, body: InvitationCreateRequest,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    workflow=Depends(get_inviter_workflow), idempotency_key=Depends(require_inviter_idempotency_key),
):
    try:
        result = await workflow.create_invitation(
            actor=actor, access_context=access_context, idempotency_key=idempotency_key,
            email=body.email, role=body.role,
            scopes=[(scope.scope_type, scope.scope_id) for scope in body.scopes],
            expires_at=body.expires_at, grant_expires_at=body.grant_expires_at,
            trace_id=request.state.request_id,
        )
        data = _public(result, status="pending")
    except Exception as exc:
        raise _error(exc) from None
    return success_envelope(data=data, request_id=request.state.request_id)

@router.post("/{invitation_id}/resend", dependencies=[Depends(manage_permission)])
async def resend_invitation(
    invitation_id: UUID, request: Request, body: InvitationResendRequest,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    workflow=Depends(get_inviter_workflow), idempotency_key=Depends(require_inviter_idempotency_key),
):
    try:
        result = await workflow.resend_invitation(
            actor=actor, access_context=access_context, invitation_id=invitation_id,
            expected_row_version=body.expected_row_version, idempotency_key=idempotency_key,
            expires_at=body.expires_at, trace_id=request.state.request_id,
        )
        data = _public(result, status="pending", invitation_id=invitation_id, previous_version=body.expected_row_version)
    except Exception as exc:
        raise _error(exc) from None
    return success_envelope(data=data, request_id=request.state.request_id)

@router.post("/{invitation_id}/revoke", dependencies=[Depends(manage_permission)])
async def revoke_invitation(
    invitation_id: UUID, request: Request, body: InvitationRevokeRequest,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    workflow=Depends(get_inviter_workflow), idempotency_key=Depends(require_inviter_idempotency_key),
):
    try:
        result = await workflow.revoke_invitation(
            actor=actor, access_context=access_context, invitation_id=invitation_id,
            expected_row_version=body.expected_row_version, idempotency_key=idempotency_key,
            trace_id=request.state.request_id,
        )
        data = _public(result, status="revoked", invitation_id=invitation_id, previous_version=body.expected_row_version)
    except Exception as exc:
        raise _error(exc) from None
    return success_envelope(data=data, request_id=request.state.request_id)
