"""Read-only project catalog over the existing Construction access model."""
from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr

from api.auth import get_current_actor, require_module, require_suite
from api.construction_project_catalog_cursors import (
    MAX_PROJECT_CATALOG_CURSOR_LENGTH,
    decode_project_catalog_cursor, encode_project_catalog_cursor,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_project_read import ProjectReadResponse
from services.construction.construction_project_catalog_read import (
    ConstructionProjectCatalogReadCursor, ConstructionProjectCatalogReadPage,
    ConstructionProjectCatalogReadService,
    build_construction_project_catalog_read_service,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access,
)
from services.construction.construction_projects import ConstructionProjectValidationError


class ProjectCatalogPagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_cursor: StrictStr | None


class ProjectCatalogMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagination: ProjectCatalogPagination


class ProjectCatalogEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[ProjectReadResponse]
    meta: ProjectCatalogMeta
    request_id: StrictStr


def get_project_catalog_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_project_catalog_read_service(session=session)


def public_project_catalog(result, *, actor, access_context, limit, after):
    if (
        not isinstance(result, ConstructionProjectCatalogReadPage)
        or not isinstance(result.items, tuple) or len(result.items) > limit
    ):
        raise ValueError("Invalid project catalog page.")
    ConstructionProjectCatalogReadService._scope(actor, access_context)
    data, seen = [], set()
    previous = (after.updated_at, after.project_id) if after is not None else None
    last = None
    for item in result.items:
        model = ProjectReadResponse.model_validate(item)
        if model.tenant_id != actor.tenant_id:
            raise ValueError("Project catalog tenant differs.")
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code="construction.projects.read", project_id=model.id,
        )
        position = model.updated_at, model.id
        if model.id in seen or (previous is not None and position >= previous):
            raise ValueError("Project catalog order differs.")
        seen.add(model.id)
        previous, last = position, model
        data.append(model.model_dump(mode="json"))
    if result.next_cursor is not None:
        if (
            not isinstance(result.next_cursor, ConstructionProjectCatalogReadCursor)
            or len(data) != limit or last is None
            or (result.next_cursor.updated_at, result.next_cursor.project_id)
            != (last.updated_at, last.id)
        ):
            raise ValueError("Project catalog continuation differs.")
    encoded = encode_project_catalog_cursor(
        result.next_cursor, tenant_id=actor.tenant_id, user_id=actor.user_id,
    )
    return data, {"pagination": {"next_cursor": encoded}}


def _failure(error):
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(
            status_code=403, code="forbidden",
            message="Construction project catalog access is not available.",
        )
    if isinstance(error, ConstructionProjectValidationError):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Project catalog query is invalid.",
        )
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Construction project catalog read failed.",
    )


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
    "", response_model=ProjectCatalogEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "Authentication required or invalid token"},
        403: {"description": "Module disabled or project read access unavailable"},
        422: {"description": "Invalid query or cursor"},
        429: {"description": "Shared authenticated rate limit exceeded"},
        500: {"description": "Project catalog read failed"},
    },
)
async def list_project_catalog(
    request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=MAX_PROJECT_CATALOG_CURSOR_LENGTH),
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_project_catalog_read_service),
):
    try:
        if (
            set(request.query_params) - {"limit", "cursor"}
            or any(len(request.query_params.getlist(name)) != 1 for name in request.query_params)
        ):
            raise ConstructionProjectValidationError("Invalid project catalog query.")
        after = decode_project_catalog_cursor(
            cursor, tenant_id=actor.tenant_id, user_id=actor.user_id,
        )
        result = await service.list_projects(
            actor=actor, access_context=access_context, limit=limit, after=after,
        )
    except Exception as error:
        raise _failure(error) from None
    try:
        data, meta = public_project_catalog(
            result, actor=actor, access_context=access_context,
            limit=limit, after=after,
        )
    except Exception:
        raise _failure(None) from None
    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=data, meta=meta, request_id=request.state.request_id,
    )
