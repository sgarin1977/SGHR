from datetime import timezone
from typing import Any
from uuid import UUID

from fastapi import Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction.construction_area_archive_workflow import (
    PUBLIC_AREA_ARCHIVE_FIELDS, build_construction_area_archive_workflow,
)
from services.construction.construction_areas import (
    ConstructionProjectAreaNotFoundError, ConstructionProjectAreaProjectNotFoundError,
    ConstructionProjectAreaTransitionError, ConstructionProjectAreaHasActiveElementsError,
    ConstructionProjectAreaValidationError,
)
from services.construction.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class AreaArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt = Field(ge=1)


class AreaArchiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    project_id: UUID
    deleted_at: AwareDatetime
    row_version: StrictInt = Field(ge=1)

    @field_validator("deleted_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)


class AreaArchiveEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: AreaArchiveResponse
    meta: dict[str, Any]
    request_id: str


def get_area_archive_workflow(session=Depends(get_construction_scoped_session)):
    return build_construction_area_archive_workflow(session=session)


def public_archive(result, *, actor, area_id, expected_row_version):
    if not isinstance(result, dict) or set(result) != PUBLIC_AREA_ARCHIVE_FIELDS:
        raise ValueError("Archived area response is invalid.")
    item = AreaArchiveResponse.model_validate(result)
    if item.id != area_id or item.tenant_id != actor.tenant_id or item.row_version != expected_row_version + 1:
        raise ValueError("Archived area response binding is invalid.")
    return item.model_dump(mode="json")


edit_permission = require_construction_permission("construction.projects.edit")
router = ConstructionAPIRouter(
    prefix="/construction/areas",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.post(
    "/{id}/archive", response_model=AreaArchiveEnvelope,
    dependencies=[Depends(edit_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: area or project unavailable in authorized scope"},
        409: {"description": "ROW_VERSION_CONFLICT or conflict: lifecycle/archive precondition"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def archive_area(
    id: UUID, request: Request, body: AreaArchiveRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    workflow=Depends(get_area_archive_workflow),
):
    try:
        result = await workflow.archive_area(
            actor=actor, access_context=access_context, area_id=id,
            expected_row_version=body.expected_row_version, trace_id=request.state.request_id,
        )
        data = public_archive(
            result, actor=actor, area_id=id, expected_row_version=body.expected_row_version,
        )
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(
            status_code=409, code="ROW_VERSION_CONFLICT", message="Construction area has changed.",
        ) from None
    except (ConstructionProjectTerminalError, ConstructionProjectAreaTransitionError, ConstructionProjectAreaHasActiveElementsError):
        raise ApiHttpError(
            status_code=409, code="conflict", message="Construction area cannot be archived in its current state.",
        ) from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Construction area archive is not available.",
        ) from None
    except (ConstructionProjectAreaNotFoundError, ConstructionProjectAreaProjectNotFoundError):
        raise ApiHttpError(
            status_code=404, code="not_found", message="Construction area is not available.",
        ) from None
    except ConstructionProjectAreaValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Area archive request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Construction area archive failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
