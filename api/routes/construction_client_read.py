from datetime import timezone
from typing import Literal
from uuid import UUID

from fastapi import Depends, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.construction_client_card_cursors import (
    MAX_CLIENT_CARD_CURSOR_LENGTH, decode_client_card_cursor, encode_client_card_cursor,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction_client_card_read import (
    ConstructionClientCardReadResult, build_construction_client_card_read_service,
)
from services.construction_client_projects_read import ClientProjectReadResponse, ConstructionClientProjectsReadPage
from services.construction_client_leads_read import ClientLeadReadResponse, ConstructionClientLeadsReadPage
from services.construction_client_activities_read import ConstructionClientActivitiesReadPage
from services.construction_activity_read import ActivityPlannedReadResponse
from services.construction_client_history_read import ClientHistoryReadResponse, ConstructionClientHistoryReadPage
from services.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class ClientReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    display_name: StrictStr
    client_type: Literal["person", "company"]
    phone: StrictStr | None
    email: StrictStr | None
    notes: StrictStr | None
    linked_user_id: UUID | None
    row_version: StrictInt = Field(ge=1, le=2**63-1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)


class ClientCardResponse(ClientReadResponse):
    projects: list[ClientProjectReadResponse]
    leads: list[ClientLeadReadResponse]
    activities: list[ActivityPlannedReadResponse]
    history: list[ClientHistoryReadResponse]


class ClientCardPagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projects_next_cursor: StrictStr | None
    leads_next_cursor: StrictStr | None
    activities_next_cursor: StrictStr | None
    history_next_cursor: StrictStr | None


class ClientCardMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagination: ClientCardPagination


class ClientReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ClientCardResponse
    meta: ClientCardMeta
    request_id: str


def get_client_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_client_card_read_service(session=session)


_PAGES = dict(projects=ConstructionClientProjectsReadPage, leads=ConstructionClientLeadsReadPage,
    activities=ConstructionClientActivitiesReadPage, history=ConstructionClientHistoryReadPage)


def public_client_card(result, *, actor, client_id, limits):
    if not isinstance(result, ConstructionClientCardReadResult) or not isinstance(result.client, dict):
        raise ValueError("Client card result is invalid.")
    client = ClientReadResponse.model_validate(result.client)
    if client.tenant_id != actor.tenant_id or client.id != client_id:
        raise ValueError("Client card response context is invalid.")
    sections, positions = {}, {}
    for name, page_type in _PAGES.items():
        page = getattr(result, name)
        if not isinstance(page, page_type) or not isinstance(page.items, tuple) or len(page.items) > limits[name]:
            raise ValueError("Client card response page is invalid.")
        # Serialize only public items. Private legacy attribution evidence stays
        # inside the history service and never becomes part of the HTTP body.
        sections[name] = list(page.items)
        if page.next_cursor is not None and len(page.items) != limits[name]:
            raise ValueError("Client card response cursor is invalid.")
        positions[name+"_next_cursor"] = encode_client_card_cursor(page.next_cursor,
            section=name, tenant_id=actor.tenant_id, user_id=actor.user_id, client_id=client_id)
    data = ClientCardResponse.model_validate({**client.model_dump(), **sections})
    for name in _PAGES:
        if any(item.tenant_id != actor.tenant_id for item in getattr(data, name)):
            raise ValueError("Client card item context is invalid.")
    if (any(item.client_id != client_id for item in data.projects)
        or any(item.converted_client_id != client_id for item in data.leads)):
        raise ValueError("Client card item client is invalid.")
    meta = ClientCardMeta(pagination=ClientCardPagination(**positions))
    return data.model_dump(mode="json"), meta.model_dump(mode="json")


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
    "/{id}", response_model=ClientReadEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: client is absent from the authorized tenant"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def read_client(
    id: UUID, request: Request,
    projects_limit: int = Query(default=20, ge=1, le=100),
    projects_cursor: str | None = Query(default=None, max_length=MAX_CLIENT_CARD_CURSOR_LENGTH),
    leads_limit: int = Query(default=20, ge=1, le=100),
    leads_cursor: str | None = Query(default=None, max_length=MAX_CLIENT_CARD_CURSOR_LENGTH),
    activities_limit: int = Query(default=20, ge=1, le=100),
    activities_cursor: str | None = Query(default=None, max_length=MAX_CLIENT_CARD_CURSOR_LENGTH),
    history_limit: int = Query(default=20, ge=1, le=100),
    history_cursor: str | None = Query(default=None, max_length=MAX_CLIENT_CARD_CURSOR_LENGTH),
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_client_read_service),
):
    limits = dict(projects=projects_limit, leads=leads_limit, activities=activities_limit, history=history_limit)
    try:
        options = {}
        for name, token in dict(projects=projects_cursor, leads=leads_cursor,
            activities=activities_cursor, history=history_cursor).items():
            options[name+"_limit"] = limits[name]
            options[name+"_after"] = decode_client_card_cursor(token, section=name,
                tenant_id=actor.tenant_id, user_id=actor.user_id, client_id=id)
        result = await service.get_client_card(actor=actor, access_context=access_context, client_id=id, **options)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Construction client access is not available.",
        ) from None
    except ConstructionClientNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found", message="Construction client is not available.",
        ) from None
    except ConstructionClientValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Client request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Construction client read failed.",
        ) from None
    try:
        data, meta = public_client_card(result, actor=actor, client_id=id, limits=limits)
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Construction client read failed.",
        ) from None
    return success_envelope(data=data, meta=meta, request_id=request.state.request_id)
