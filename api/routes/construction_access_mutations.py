from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission,
)
from api.settings import ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction_access_workflows import (
    ConstructionAccessIdempotencyInProgressError, PUBLIC_GRANT_FIELDS,
    build_construction_access_mutation_workflow,
)
from services.construction_grants import ConstructionGrantOperationError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError

Role = Literal["OWNER", "ADMIN", "SURVEYOR", "ESTIMATOR", "PROJECT_MANAGER", "EXECUTOR", "OBSERVER"]


class GrantRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GrantReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    scope_type: Literal["tenant", "project"]
    scope_id: UUID
    expires_at: AwareDatetime | None

    @field_validator("expires_at", mode="before")
    @classmethod
    def require_iso_datetime(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Expiration must be an ISO datetime.")
        return value


class GrantMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    user_id: UUID
    role: Role
    scope_type: Literal["tenant", "project", "document"]
    scope_id: UUID
    status: Literal["active", "revoked"]
    expires_at: AwareDatetime | None
    granted_at: AwareDatetime

    @field_validator("expires_at", "granted_at", mode="before")
    @classmethod
    def require_iso_datetime(cls, value):
        return GrantReplaceRequest.require_iso_datetime(value)


def optional_idempotency_key(
    value: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)] = None,
):
    if value is not None and not value.strip():
        raise ApiHttpError(status_code=422, code="validation_error", message="Idempotency key is invalid.")
    return value


def get_access_mutation_workflow(
    session=Depends(get_construction_scoped_session),
    idempotency_key=Depends(optional_idempotency_key),
):
    key = ApiIdempotencySettings.from_env().encryption_key if idempotency_key is not None else None
    return build_construction_access_mutation_workflow(session=session, idempotency_encryption_key=key)


def _error(error):
    if isinstance(error, ConstructionAccessGrantConflictError):
        return ApiHttpError(status_code=409, code="ACCESS_GRANT_CONFLICT", message="Construction access grant has changed.")
    if isinstance(error, ApiIdempotencyKeyReusedError):
        return ApiHttpError(status_code=409, code="IDEMPOTENCY_KEY_REUSED", message="Idempotency key was reused with a different request.")
    if isinstance(error, ConstructionAccessIdempotencyInProgressError):
        return ApiHttpError(status_code=409, code=error.code, message="Construction access operation is processing.")
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(status_code=403, code="forbidden", message="Construction access is not available.")
    if type(error) is ValueError:
        return ApiHttpError(status_code=422, code="validation_error", message="Construction access request is invalid.")
    return ApiHttpError(status_code=500, code="internal_error", message="Construction access operation failed.")


def _public(result, *, actor, grant_id, target=None):
    if not isinstance(result, dict) or set(result) != PUBLIC_GRANT_FIELDS:
        raise ConstructionGrantOperationError("Grant response is invalid.")
    item = GrantMutationResponse.model_validate(result)
    if (
        item.status != ("active" if target is not None else "revoked")
        or (item.id == grant_id if target is not None else item.id != grant_id)
        or (item.scope_type == "tenant" and (item.scope_id != actor.tenant_id or item.role not in ("OWNER", "ADMIN")))
        or (item.expires_at is not None and item.expires_at <= item.granted_at)
        or (target is not None and (
            item.role != target.role or item.scope_type != target.scope_type
            or item.scope_id != target.scope_id or item.expires_at != target.expires_at
        ))
    ):
        raise ConstructionGrantOperationError("Grant response is invalid.")
    return item.model_dump(mode="json")


access_permission = require_construction_permission("construction.access.manage")
router = ConstructionAPIRouter(
    prefix="/construction/team/access-grants",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.post("/{grant_id}/revoke", dependencies=[Depends(access_permission)])
async def revoke_access_grant(
    grant_id: UUID, request: Request, body: GrantRevokeRequest | None = None,
    actor=Depends(get_current_actor), workflow=Depends(get_access_mutation_workflow),
    idempotency_key=Depends(optional_idempotency_key),
):
    try:
        result = await workflow.revoke_access(
            actor=actor, grant_id=grant_id, idempotency_key=idempotency_key, trace_id=request.state.request_id,
        )
        data = _public(result, actor=actor, grant_id=grant_id)
    except Exception as error:
        raise _error(error) from None
    return success_envelope(data=data, request_id=request.state.request_id)


@router.post("/{grant_id}/replace", dependencies=[Depends(access_permission)])
async def replace_access_grant(
    grant_id: UUID, request: Request, body: GrantReplaceRequest,
    actor=Depends(get_current_actor), workflow=Depends(get_access_mutation_workflow),
    idempotency_key=Depends(optional_idempotency_key),
):
    try:
        result = await workflow.replace_access(
            actor=actor, grant_id=grant_id, role=body.role, scope_type=body.scope_type,
            scope_id=body.scope_id, expires_at=body.expires_at,
            idempotency_key=idempotency_key, trace_id=request.state.request_id,
        )
        data = _public(result, actor=actor, grant_id=grant_id, target=body)
    except Exception as error:
        raise _error(error) from None
    return success_envelope(data=data, request_id=request.state.request_id)
