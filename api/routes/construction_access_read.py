from typing import Literal
from uuid import UUID

from fastapi import Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict
from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction_grants import build_construction_access_grant_service, ConstructionGrantOperationError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError

class AccessGrantReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    user_id: UUID
    role: Literal["OWNER", "ADMIN", "SURVEYOR", "ESTIMATOR", "PROJECT_MANAGER", "EXECUTOR", "OBSERVER"]
    scope_type: Literal["tenant", "project", "document"]
    scope_id: UUID
    status: Literal["active"]
    expires_at: AwareDatetime | None
    granted_at: AwareDatetime

def get_access_grant_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_access_grant_service(session=session)

def _public_grants(grants, *, tenant_id, target_user_id, project_id):
    if not isinstance(grants, (list, tuple)):
        raise ConstructionGrantOperationError("Grant list is invalid.")
    output, seen = [], set()
    fields = ("id", "user_id", "role", "scope_type", "scope_id", "status", "expires_at", "granted_at")
    for grant in grants:
        if (
            getattr(grant, "tenant_id", None) != tenant_id
            or getattr(grant, "user_id", None) != target_user_id
            or getattr(grant, "status", None) != "active"
        ):
            raise ConstructionGrantOperationError("Grant scope is invalid.")
        item = AccessGrantReadResponse.model_validate({field: getattr(grant, field) for field in fields})
        if item.id in seen or (
            item.scope_type == "tenant" and (item.scope_id != tenant_id or item.role not in ("OWNER", "ADMIN"))
        ) or (
            project_id is not None and (item.scope_type != "project" or item.scope_id != project_id)
        ):
            raise ConstructionGrantOperationError("Grant scope is invalid.")
        seen.add(item.id)
        output.append(item.model_dump(mode="json"))
    return output

access_permission = require_construction_permission("construction.access.manage")
router = ConstructionAPIRouter(
    prefix="/construction/team",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)

@router.get("/members/{user_id}/access-grants", dependencies=[Depends(access_permission)])
async def list_member_access_grants(
    user_id: UUID, request: Request, project_id: UUID | None = None,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    service=Depends(get_access_grant_read_service),
):
    try:
        grants = await service.list_access(
            actor=actor, access_context=access_context,
            target_user_id=user_id, project_id=project_id,
        )
        data = _public_grants(
            grants, tenant_id=actor.tenant_id, target_user_id=user_id, project_id=project_id,
        )
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Construction access is not available.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Construction access read failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
 