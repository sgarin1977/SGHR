from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_access_mutations import (
    GrantMutationResponse, GrantReplaceRequest, Role, _error, optional_idempotency_key,
)
from api.settings import ApiIdempotencySettings
from services.construction_access_issuance_workflow import build_construction_access_issuance_workflow
from services.construction_access_workflows import PUBLIC_GRANT_FIELDS
from services.construction_grants import ConstructionGrantOperationError


class GrantCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    scope_type: Literal["tenant", "project"]
    scope_id: UUID
    expires_at: AwareDatetime | None = None

    @field_validator("expires_at", mode="before")
    @classmethod
    def require_iso_datetime(cls, value):
        value = GrantReplaceRequest.require_iso_datetime(value)
        if isinstance(value, str):
            datetime.fromisoformat(value)
        return value


def get_access_issuance_workflow(
    session=Depends(get_construction_scoped_session),
    idempotency_key=Depends(optional_idempotency_key),
):
    key = ApiIdempotencySettings.from_env().encryption_key if idempotency_key is not None else None
    return build_construction_access_issuance_workflow(session=session, idempotency_encryption_key=key)


def _public(result, *, actor, user_id, target):
    if not isinstance(result, dict) or set(result) != PUBLIC_GRANT_FIELDS:
        raise ConstructionGrantOperationError("Issued grant response is invalid.")
    item = GrantMutationResponse.model_validate(result)
    if (
        item.status != "active" or item.user_id != user_id
        or item.role != target.role or item.scope_type != target.scope_type or item.scope_id != target.scope_id
        or item.expires_at != target.expires_at
        or (item.scope_type == "tenant" and (item.scope_id != actor.tenant_id or item.role not in ("OWNER", "ADMIN")))
        or (item.expires_at is not None and item.expires_at <= item.granted_at)
    ):
        raise ConstructionGrantOperationError("Issued grant response is invalid.")
    return item.model_dump(mode="json")


access_permission = require_construction_permission("construction.access.manage")
router = ConstructionAPIRouter(
    prefix="/construction/team",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.post("/members/{user_id}/access-grants", status_code=201, dependencies=[Depends(access_permission)])
async def create_member_access_grant(
    user_id: UUID, request: Request, body: GrantCreateRequest,
    actor=Depends(get_current_actor), workflow=Depends(get_access_issuance_workflow),
    idempotency_key=Depends(optional_idempotency_key),
):
    try:
        result = await workflow.grant_access(
            actor=actor, target_user_id=user_id, role=body.role, scope_type=body.scope_type,
            scope_id=body.scope_id, expires_at=body.expires_at,
            idempotency_key=idempotency_key, trace_id=request.state.request_id,
        )
        data = _public(result, actor=actor, user_id=user_id, target=body)
    except Exception as error:
        raise _error(error) from None
    return success_envelope(data=data, request_id=request.state.request_id)
