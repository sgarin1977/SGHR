from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_project_read import ProjectReadEnvelope, ProjectReadResponse
from api.routes.construction_project_read import public_project
from api.settings import ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction_project_create_workflow import (
    ConstructionProjectIdempotencyInProgressError, PUBLIC_PROJECT_FIELDS,
    build_construction_project_create_workflow,
)
from services.construction_projects import ConstructionProjectNotFoundError, ConstructionProjectValidationError


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr
    address_raw: StrictStr
    client_id: UUID | None = None
    responsible_user_id: UUID | None = None
    comment: StrictStr | None = None

    @field_validator("name", "address_raw")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A nonblank value is required.")
        return value


def optional_project_key(
    request: Request,
    value: Annotated[str | None, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Optional: same key and body replay the original result; changed body returns 409 IDEMPOTENCY_KEY_REUSED.",
    )] = None,
):
    if len(request.headers.getlist("Idempotency-Key")) > 1 or (
        value is not None and not value.strip()
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Idempotency key is invalid.",
        )
    return value


def get_project_create_workflow(
    session=Depends(get_construction_scoped_session),
    idempotency_key=Depends(optional_project_key),
):
    key = ApiIdempotencySettings.from_env().encryption_key if idempotency_key is not None else None
    return build_construction_project_create_workflow(
        session=session, idempotency_encryption_key=key, response_projector=public_project,
    )


def public_created_project(result, *, actor, body):
    if not isinstance(result, dict) or set(result) != PUBLIC_PROJECT_FIELDS:
        raise ValueError("Created project response is invalid.")
    item = ProjectReadResponse.model_validate(result)
    if (
        item.tenant_id != actor.tenant_id or item.created_by != actor.user_id
        or item.name != body.name.strip() or item.address_raw != body.address_raw.strip()
        or item.client_id != body.client_id or item.responsible_user_id != body.responsible_user_id
        or item.comment != body.comment or item.status != "draft" or item.row_version != 1
        or item.address_verification_status != "pending"
    ):
        raise ValueError("Created project response context is invalid.")
    return item.model_dump(mode="json")


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
    "", status_code=201, response_model=ProjectReadEnvelope,
    dependencies=[Depends(create_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: original project unavailable on replay"},
        409: {"description": "IDEMPOTENCY_KEY_REUSED or idempotency_in_progress"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def create_project(
    request: Request, body: ProjectCreateRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    workflow=Depends(get_project_create_workflow),
    idempotency_key=Depends(optional_project_key),
):
    try:
        result = await workflow.create_project(
            actor=actor, access_context=access_context,
            name=body.name, address_raw=body.address_raw,
            client_id=body.client_id, responsible_user_id=body.responsible_user_id,
            comment=body.comment, idempotency_key=idempotency_key,
            trace_id=request.state.request_id,
        )
        data = public_created_project(result, actor=actor, body=body)
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(
            status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with a different request.",
        ) from None
    except ConstructionProjectIdempotencyInProgressError as error:
        raise ApiHttpError(
            status_code=409, code=error.code, message="Project creation is processing.",
        ) from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Construction project creation is not available.",
        ) from None
    except ConstructionProjectNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found", message="Created project is not available.",
        ) from None
    except ConstructionProjectValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Project creation request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Construction project creation failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
