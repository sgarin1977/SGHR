"""Read-only client catalog under the existing tenant-wide CRM policy."""
from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr

from api.auth import get_current_actor, require_module, require_suite
from api.construction_client_catalog_cursors import (
    MAX_CLIENT_CATALOG_CURSOR_LENGTH,
    decode_client_catalog_cursor, encode_client_catalog_cursor,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_client_read import ClientReadResponse
from services.construction.construction_client_catalog_read import (
    ConstructionClientCatalogReadCursor, ConstructionClientCatalogReadPage,
    ConstructionClientCatalogReadService, build_construction_client_catalog_read_service,
)
from services.construction.construction_clients import ConstructionClientValidationError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


class ClientCatalogPagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_cursor: StrictStr | None


class ClientCatalogMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagination: ClientCatalogPagination


class ClientCatalogEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[ClientReadResponse]
    meta: ClientCatalogMeta
    request_id: StrictStr


def get_client_catalog_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_client_catalog_read_service(session=session)


def public_client_catalog(result, *, actor, access_context, limit, after):
    if (
        not isinstance(result, ConstructionClientCatalogReadPage)
        or not isinstance(result.items, tuple) or len(result.items) > limit
    ):
        raise ValueError("Invalid client catalog page.")
    ConstructionClientCatalogReadService._authorize(actor, access_context)
    data, seen = [], set()
    previous = (after.updated_at, after.client_id) if after is not None else None
    last = None
    for value in result.items:
        model = ClientReadResponse.model_validate(value)
        key = model.updated_at, model.id
        if (
            model.tenant_id != actor.tenant_id or model.id in seen
            or (previous is not None and key >= previous)
        ):
            raise ValueError("Client catalog context or order differs.")
        seen.add(model.id)
        previous, last = key, model
        data.append(model.model_dump(mode="json"))
    if result.next_cursor is not None and (
        not isinstance(result.next_cursor, ConstructionClientCatalogReadCursor)
        or len(data) != limit or last is None
        or (result.next_cursor.updated_at, result.next_cursor.client_id)
        != (last.updated_at, last.id)
    ):
        raise ValueError("Client catalog continuation differs.")
    encoded = encode_client_catalog_cursor(
        result.next_cursor, tenant_id=actor.tenant_id, user_id=actor.user_id,
    )
    return data, {"pagination": {"next_cursor": encoded}}


def _failure(error):
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(
            status_code=403, code="forbidden",
            message="Construction client catalog access is not available.",
        )
    if isinstance(error, ConstructionClientValidationError):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Client catalog query is invalid.",
        )
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Construction client catalog read failed.",
    )


read_permission = require_construction_permission("construction.projects.read")
router = ConstructionAPIRouter(
    prefix="/construction/clients",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.get(
    "", response_model=ClientCatalogEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "Authentication required or invalid token"},
        403: {"description": "Module disabled or tenant CRM read access unavailable"},
        422: {"description": "Invalid query or cursor"},
        429: {"description": "Shared authenticated rate limit exceeded"},
        500: {"description": "Client catalog read failed"},
    },
)
async def list_client_catalog(
    request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=MAX_CLIENT_CATALOG_CURSOR_LENGTH),
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_client_catalog_read_service),
):
    try:
        if (
            set(request.query_params) - {"limit", "cursor"}
            or any(len(request.query_params.getlist(name)) != 1 for name in request.query_params)
        ):
            raise ConstructionClientValidationError("Invalid client catalog query.")
        after = decode_client_catalog_cursor(
            cursor, tenant_id=actor.tenant_id, user_id=actor.user_id,
        )
        result = await service.list_clients(
            actor=actor, access_context=access_context, limit=limit, after=after,
        )
    except Exception as error:
        raise _failure(error) from None
    try:
        data, meta = public_client_catalog(
            result, actor=actor, access_context=access_context, limit=limit, after=after,
        )
    except Exception:
        raise _failure(None) from None
    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(data=data, meta=meta, request_id=request.state.request_id)
