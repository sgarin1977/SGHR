from typing import Literal
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, StrictStr
from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context, get_construction_scoped_session,
)
from api.routes.construction_access_read import access_permission
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_role_options import build_construction_role_options_service, ConstructionRoleOptionsError

class RoleOptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["OWNER", "ADMIN", "SURVEYOR", "ESTIMATOR", "PROJECT_MANAGER", "EXECUTOR", "OBSERVER"]
    permissions: list[StrictStr]

class RoleOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_type: Literal["tenant", "project"]
    scope_id: UUID
    roles: list[RoleOptionResponse]
    permissions_read_only: Literal[True]

def get_role_options_service(session=Depends(get_construction_scoped_session)):
    return build_construction_role_options_service(session=session)

router = ConstructionAPIRouter(
    prefix="/construction/team",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)

@router.get("/role-options", dependencies=[Depends(access_permission)])
async def get_role_options(
    request: Request, scope_type: Literal["tenant", "project", "document"], scope_id: UUID,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    service=Depends(get_role_options_service),
):
    try:
        result = await service.list_options(
            actor=actor, access_context=access_context, scope_type=scope_type, scope_id=scope_id,
        )
        if not isinstance(result, dict) or set(result) != {"scope_type", "scope_id", "roles", "permissions_read_only"} or result["permissions_read_only"] is not True:
            raise ConstructionRoleOptionsError("Role options response is invalid.")
        response = RoleOptionsResponse.model_validate(result)
        if response.scope_type != scope_type or response.scope_id != scope_id:
            raise ConstructionRoleOptionsError("Role options scope is invalid.")
        seen = set()
        for option in response.roles:
            if option.role in seen or option.permissions != sorted(CONSTRUCTION_ROLE_PERMISSIONS[option.role]):
                raise ConstructionRoleOptionsError("Role permissions are invalid.")
            if response.scope_type == "tenant" and option.role not in ("OWNER", "ADMIN"):
                raise ConstructionRoleOptionsError("Role scope is invalid.")
            seen.add(option.role)
        data = response.model_dump(mode="json")
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden", message="Construction role options are not available.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error", message="Construction role options read failed.") from None
    return success_envelope(data=data, request_id=request.state.request_id)
