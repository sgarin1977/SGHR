"""Read-only boundary for universal project areas, not Measurement areas."""
from typing import Any
from uuid import UUID

from fastapi import Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction.construction_area_read import (
    ProjectAreaReadItem, build_construction_project_area_read_service,
)
from services.construction.construction_areas import (
    ConstructionProjectAreaProjectNotFoundError,
    ConstructionProjectAreaValidationError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


class ProjectAreaListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[ProjectAreaReadItem]
    meta: dict[str, Any]
    request_id: str


def get_project_area_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_project_area_read_service(session=session)


read_permission = require_construction_permission("construction.projects.read")
router = ConstructionAPIRouter(
    prefix="/construction/projects",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.get(
    "/{id}/areas", response_model=ProjectAreaListEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: project unavailable"},
        422: {"description": "validation_error"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error"},
    },
)
async def list_project_areas(
    id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_project_area_read_service),
):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Project area query parameters are not supported.",
        )
    try:
        data = await service.list_areas(
            actor=actor, access_context=access_context, project_id=id,
            include_capabilities=True,
        )
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Project area read is not available.",
        ) from None
    except ConstructionProjectAreaProjectNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found",
            message="Project is not available.",
        ) from None
    except ConstructionProjectAreaValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Project area request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Project area read failed.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=data["items"], request_id=request.state.request_id,
        meta={"capabilities": {
            "can_create_area": data["can_create_area"],
            "can_edit_area": data["can_edit_area"],
        }},
    )

# universal_area_editor_v1
