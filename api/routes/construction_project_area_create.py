"""Creation of universal project areas through the shared Construction API."""
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.settings import ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_area_read import ProjectAreaReadItem
from services.construction.construction_area_create_workflow import (
    AreaCreationInProgressError, build_construction_area_create_workflow,
)
from services.construction.construction_areas import (
    ConstructionProjectAreaNotFoundError,
    ConstructionProjectAreaProjectNotFoundError,
    ConstructionProjectAreaValidationError,
)
from services.construction.construction_foundation import ConstructionProjectTerminalError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


class ProjectAreaCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    area_type: Literal["ROOM", "PROJECT_GENERAL", "EXTERIOR", "OTHER"]
    name: StrictStr = Field(min_length=1)
    sort_order: StrictInt | None = Field(
        default=None, ge=-2147483648, le=2147483647,
    )

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("An area name is required.")
        return value


class ProjectAreaCreateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ProjectAreaReadItem
    meta: dict[str, Any]
    request_id: str


def require_area_create_key(
    request: Request,
    value: Annotated[str, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Required. Retry the same operation with the same key and body.",
    )],
):
    if (
        len(request.headers.getlist("Idempotency-Key")) != 1
        or not value.strip()
        or any(not 33 <= ord(char) <= 126 for char in value)
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Idempotency key is invalid.",
        )
    return value


def get_area_create_workflow(
    session=Depends(get_construction_scoped_session),
    idempotency_key=Depends(require_area_create_key),
):
    return build_construction_area_create_workflow(
        session=session,
        idempotency_encryption_key=ApiIdempotencySettings.from_env().encryption_key,
    )


create_permission = require_construction_permission("construction.projects.create")
router = ConstructionAPIRouter(
    prefix="/construction/projects",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.post(
    "/{id}/areas", status_code=201,
    response_model=ProjectAreaCreateEnvelope,
    dependencies=[Depends(create_permission)],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Module, permission or scope denied"},
        404: {"description": "Project or original area unavailable"},
        409: {"description": "Idempotency conflict or terminal project"},
        422: {"description": "Invalid request"},
        429: {"description": "Rate limit exceeded"},
        500: {"description": "Internal error"},
    },
)
async def create_project_area(
    id: UUID, request: Request, response: Response,
    body: ProjectAreaCreateRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    workflow=Depends(get_area_create_workflow),
    idempotency_key=Depends(require_area_create_key),
):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Project area query parameters are not supported.",
        )
    try:
        result = await workflow.create_area(
            actor=actor, access_context=access_context, project_id=id,
            area_type=body.area_type, name=body.name,
            sort_order=body.sort_order, idempotency_key=idempotency_key,
            trace_id=request.state.request_id,
        )
        item = ProjectAreaReadItem.model_validate(result)
        if (
            item.tenant_id != actor.tenant_id or item.project_id != id
            or item.area_type != body.area_type or item.name != body.name.strip()
            or item.sort_order != body.sort_order
            or item.status != "draft" or item.row_version != 1
        ):
            raise ValueError("Area receipt context differs.")
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(
            status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with a different request.",
        ) from None
    except AreaCreationInProgressError:
        raise ApiHttpError(
            status_code=409, code="idempotency_in_progress",
            message="Area creation is processing.",
        ) from None
    except ConstructionProjectTerminalError:
        raise ApiHttpError(
            status_code=409, code="conflict",
            message="A terminal project cannot be changed.",
        ) from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Area creation is unavailable.",
        ) from None
    except (
        ConstructionProjectAreaNotFoundError,
        ConstructionProjectAreaProjectNotFoundError,
    ):
        raise ApiHttpError(
            status_code=404, code="not_found",
            message="Project area is unavailable.",
        ) from None
    except ConstructionProjectAreaValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Project area request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Project area creation failed.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=item.model_dump(mode="json"),
        request_id=request.state.request_id,
    )
