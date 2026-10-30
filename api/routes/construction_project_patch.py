"""Sparse versioned project text editing using the existing domain operation."""
from services.construction.construction_api_services import (
    build_project_patch_service,
)
# api_service_boundary_v1
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
from api.routes.construction_project_read import ProjectReadResponse, public_project
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_projects import (
    ConstructionProjectService, ConstructionProjectNotFoundError,
    ConstructionProjectValidationError,
)


class ProjectPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt = Field(ge=1, le=2**31-1)
    name: StrictStr = Field(default_factory=str)
    address_raw: StrictStr = Field(default_factory=str)
    comment: StrictStr | None = None

    @field_validator("name", "address_raw")
    @classmethod
    def nonblank_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Project name and address must be non-empty.")
        return value

    def patch_fields(self):
        return self.model_dump(
            include=self.model_fields_set - {"expected_row_version"},
        )


class ProjectPatchEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ProjectReadResponse
    meta: dict
    request_id: str



def get_project_patch_service(session=Depends(get_construction_scoped_session)):
    return build_project_patch_service(session)


router = ConstructionAPIRouter(
    prefix="/construction/projects",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.patch(
    "/{id}", response_model=ProjectPatchEnvelope,
    dependencies=[
        Depends(require_construction_permission("construction.projects.edit")),
    ],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Module, permission or exact project scope denied"},
        404: {"description": "Live project unavailable"},
        409: {"description": "ROW_VERSION_CONFLICT"},
        422: {"description": "Invalid fields or expected version"},
        500: {"description": "Project update failed"},
    },
)
async def patch_project(
    id: UUID, request: Request, body: ProjectPatchRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_project_patch_service),
):
    fields = body.patch_fields()
    try:
        result = await service.patch_project(
            actor=actor, access_context=access_context, project_id=id,
            expected_row_version=body.expected_row_version,
            fields=fields, trace_id=request.state.request_id,
        )
        data = public_project(result, tenant_id=actor.tenant_id, project_id=id)
        if (
            data["row_version"] not in (
                body.expected_row_version, body.expected_row_version + 1,
            )
            or any(data[name] != value for name, value in fields.items())
        ):
            raise ValueError("Project patch receipt differs.")
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Project editing is unavailable.",
        ) from None
    except ConstructionProjectNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found",
            message="Project is unavailable.",
        ) from None
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(
            status_code=409, code="ROW_VERSION_CONFLICT",
            message="Project version has changed. Reload the project.",
        ) from None
    except ConstructionProjectValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Project update fields are invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Project update failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
