from datetime import timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction_project_read import build_construction_project_read_service
from services.construction_projects import ConstructionProjectNotFoundError, ConstructionProjectValidationError


class ProjectReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    client_id: UUID | None
    name: StrictStr
    status: Literal["draft", "planning", "active", "completed", "cancelled"]
    responsible_user_id: UUID | None
    comment: StrictStr | None
    address_raw: StrictStr
    address_formatted: StrictStr | None
    country_code: StrictStr | None
    region: StrictStr | None
    city: StrictStr | None
    postal_code: StrictStr | None
    latitude: Decimal | None = Field(allow_inf_nan=False)
    longitude: Decimal | None = Field(allow_inf_nan=False)
    address_provider: StrictStr | None
    provider_place_id: StrictStr | None
    address_verification_status: Literal["pending", "verified", "failed"]
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: StrictInt = Field(ge=1)

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)


class ProjectReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ProjectReadResponse
    meta: dict[str, Any]
    request_id: str


def get_project_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_project_read_service(session)


def public_project(project, *, tenant_id, project_id):
    if (
        getattr(project, "tenant_id", None) != tenant_id
        or getattr(project, "id", None) != project_id
        or getattr(project, "deleted_at", object()) is not None
    ):
        raise ValueError("Project response context is invalid.")
    fields = ProjectReadResponse.model_fields
    return ProjectReadResponse.model_validate({
        field: getattr(project, field) for field in fields
    }).model_dump(mode="json")


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
    "/{id}", response_model=ProjectReadEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: project is absent from the authorized tenant"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def read_project(
    id: UUID, request: Request,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_project_read_service),
):
    try:
        project = await service.get_project(
            actor=actor, access_context=access_context, project_id=id,
        )
        data = public_project(project, tenant_id=actor.tenant_id, project_id=id)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Construction project access is not available.",
        ) from None
    except ConstructionProjectNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found", message="Construction project is not available.",
        ) from None
    except ConstructionProjectValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Project request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Construction project read failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
