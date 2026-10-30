from typing import Any
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction.construction_area_patch_workflow import (
    PUBLIC_AREA_PATCH_FIELDS, build_construction_area_patch_workflow,
)
from services.construction.construction_areas import (
    ConstructionProjectAreaNotFoundError, ConstructionProjectAreaProjectNotFoundError,
    ConstructionProjectAreaValidationError,
)
from services.construction.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class AreaPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt = Field(ge=1)
    # Only explicitly supplied fields are passed to the workflow.
    name: StrictStr = Field(default_factory=str, min_length=1)
    sort_order: StrictInt | None = Field(default=None, ge=-2147483648, le=2147483647)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Area name is required.")
        return value

    def patch_fields(self):
        return self.model_dump(
            include=self.model_fields_set - {"expected_row_version"},
        )


class AreaPatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    project_id: UUID
    name: StrictStr = Field(min_length=1)
    sort_order: StrictInt | None
    row_version: StrictInt = Field(ge=1)


class AreaPatchEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: AreaPatchResponse
    meta: dict[str, Any]
    request_id: str


def get_area_patch_workflow(session=Depends(get_construction_scoped_session)):
    return build_construction_area_patch_workflow(session=session)


def public_patch(result, *, actor, area_id, expected_row_version, fields):
    if not isinstance(result, dict) or set(result) != PUBLIC_AREA_PATCH_FIELDS:
        raise ValueError("Area patch response is invalid.")
    item = AreaPatchResponse.model_validate(result)
    if (
        item.id != area_id or item.tenant_id != actor.tenant_id
        or item.row_version not in (expected_row_version, expected_row_version + 1)
        or any(getattr(item, field) != value for field, value in fields.items())
    ):
        raise ValueError("Area patch response binding is invalid.")
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


@router.patch(
    "/{id}", response_model=AreaPatchEnvelope,
    dependencies=[Depends(edit_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: area or project unavailable in authorized scope"},
        409: {"description": "ROW_VERSION_CONFLICT or conflict: terminal project"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def patch_area(
    id: UUID, request: Request, body: AreaPatchRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    workflow=Depends(get_area_patch_workflow),
):
    fields = body.patch_fields()
    try:
        result = await workflow.patch_area(
            actor=actor, access_context=access_context, area_id=id,
            expected_row_version=body.expected_row_version, fields=fields,
            trace_id=request.state.request_id,
        )
        data = public_patch(
            result, actor=actor, area_id=id,
            expected_row_version=body.expected_row_version, fields=fields,
        )
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(
            status_code=409, code="ROW_VERSION_CONFLICT", message="Construction area has changed.",
        ) from None
    except ConstructionProjectTerminalError:
        raise ApiHttpError(
            status_code=409, code="conflict", message="Construction project is terminal.",
        ) from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Construction area edit is not available.",
        ) from None
    except (ConstructionProjectAreaNotFoundError, ConstructionProjectAreaProjectNotFoundError):
        raise ApiHttpError(
            status_code=404, code="not_found", message="Construction area is not available.",
        ) from None
    except ConstructionProjectAreaValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Area patch request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Construction area patch failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
