"""Project planned activities using the existing authorized CON-C011 read service."""
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr

from api.auth import get_current_actor, require_module, require_suite
from api.construction_project_activity_cursors import (
    MAX_CURSOR_LENGTH, decode_cursor, encode_cursor,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction.construction_activity_read import (
    ActivityPlannedReadResponse, ConstructionActivityReadPage,
    ConstructionActivityReadNotFoundError, build_construction_activity_read_service,
)
from services.construction.construction_activity_rules import ConstructionActivityValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_cursor: StrictStr | None


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagination: Pagination


class ProjectActivitiesEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[ActivityPlannedReadResponse]
    meta: Meta
    request_id: StrictStr


def get_project_activity_read(session=Depends(get_construction_scoped_session)):
    return build_construction_activity_read_service(session=session)


def public_page(page, *, actor, project_id, limit, after):
    if (
        not isinstance(page, ConstructionActivityReadPage)
        or not isinstance(page.items, tuple) or len(page.items) > limit
    ):
        raise ValueError("Project activity page is invalid.")
    data, seen = [], set()
    previous = (after.due_at, after.activity_id) if after is not None else None
    last = None
    for raw in page.items:
        item = ActivityPlannedReadResponse.model_validate(raw)
        position = item.due_at, item.id
        if (
            item.tenant_id != actor.tenant_id or item.project_id != project_id
            or item.id in seen or (previous is not None and position <= previous)
        ):
            raise ValueError("Project activity projection differs.")
        seen.add(item.id)
        previous, last = position, item
        data.append(item.model_dump(mode="json"))
    if page.next_cursor is not None:
        if (
            len(data) != limit or last is None
            or (page.next_cursor.due_at, page.next_cursor.activity_id) != (last.due_at, last.id)
        ):
            raise ValueError("Project activity next cursor differs.")
    cursor = encode_cursor(
        page.next_cursor, tenant_id=actor.tenant_id,
        user_id=actor.user_id, project_id=project_id,
    )
    return data, {"pagination": {"next_cursor": cursor}}


def failure(error):
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        status, code = 403, "forbidden"
    elif isinstance(error, ConstructionActivityReadNotFoundError):
        status, code = 404, "not_found"
    elif isinstance(error, ConstructionActivityValidationError):
        status, code = 422, "validation_error"
    else:
        status, code = 500, "internal_error"
    return ApiHttpError(
        status_code=status, code=code, message="Project activities are unavailable.",
        headers={"Cache-Control": "private, no-store"},
    )


router = ConstructionAPIRouter(
    prefix="/construction/projects",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)
read_permission = require_construction_permission("construction.projects.read")


@router.get(
    "/{id}/activities", response_model=ProjectActivitiesEnvelope,
    dependencies=[Depends(read_permission)],
)
async def list_project_activities(
    id: UUID, request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=MAX_CURSOR_LENGTH),
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_project_activity_read),
):
    try:
        if (
            set(request.query_params) - {"limit", "cursor"}
            or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)
        ):
            raise ConstructionActivityValidationError("Project activity query is invalid.")
        after = decode_cursor(
            cursor, tenant_id=actor.tenant_id, user_id=actor.user_id, project_id=id,
        )
        page = await service.list_planned_activities(
            actor=actor, access_context=access_context, project_id=id,
            limit=limit, after=after,
        )
    except Exception as error:
        raise failure(error) from None
    try:
        data, meta = public_page(page, actor=actor, project_id=id, limit=limit, after=after)
    except Exception:
        raise failure(RuntimeError()) from None
    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(data=data, meta=meta, request_id=request.state.request_id)
