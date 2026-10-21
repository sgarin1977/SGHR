from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.settings import ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_activity_create import ACTIVITY_CREATE_PERMISSION, ConstructionActivityCreateCommand, ConstructionActivityCreateNotFoundError
from services.construction_activity_create_workflow import ConstructionActivityIdempotencyInProgressError, build_construction_activity_create_workflow
from services.construction_activity_response import ActivityCreatedResponse, PUBLIC_ACTIVITY_CREATED_FIELDS
from services.construction_activity_rules import ConstructionActivityLinks, ConstructionActivityValidationError, ConstructionActivityTransitionError, normalize_construction_activity_schedule
from services.construction_activity_terminal import ACTIVITY_EDIT_PERMISSION, ConstructionActivityTerminalCommand, ConstructionActivityTerminalNotFoundError
from services.construction_activity_terminal_workflow import (
    ActivityTerminalResponse, PUBLIC_ACTIVITY_TERMINAL_FIELDS,
    ConstructionActivityTerminalInProgressError, build_construction_activity_terminal_workflow,
)
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class ActivityCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: StrictStr
    due_at: AwareDatetime
    timezone: StrictStr
    lead_id: UUID | None = None
    client_id: UUID | None = None
    project_id: UUID | None = None
    responsible_user_id: UUID | None = None
    comment: StrictStr | None = None

    @field_validator("due_at", mode="before")
    @classmethod
    def deadline_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("An aware ISO date-time string is required.")
        return value

    @model_validator(mode="after")
    def approved_input(self):
        self.command()
        return self

    def command(self):
        return ConstructionActivityCreateCommand(
            activity_type=self.type, links=ConstructionActivityLinks(self.lead_id, self.client_id, self.project_id),
            schedule=normalize_construction_activity_schedule(due_at=self.due_at, timezone_code=self.timezone),
            responsible_user_id=self.responsible_user_id, comment=self.comment,
        )


class ActivityTerminalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt = Field(ge=1, le=2**63-1)
    result: StrictStr | None = None


class ActivityCreateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ActivityCreatedResponse
    meta: dict[str, Any]
    request_id: StrictStr


class ActivityTerminalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ActivityTerminalResponse
    meta: dict[str, Any]
    request_id: StrictStr


def require_activity_key(request: Request, value: Annotated[str, Header(
    alias="Idempotency-Key", min_length=1, max_length=255,
    description="Required for create, complete and cancel. Same key and command replay the original response.",
)]):
    if len(request.headers.getlist("Idempotency-Key")) != 1 or not value.strip():
        raise ApiHttpError(status_code=422, code="validation_error", message="Idempotency key is invalid.")
    return value


def get_activity_create_workflow(session=Depends(get_construction_scoped_session), key=Depends(require_activity_key)):
    return build_construction_activity_create_workflow(
        session=session, encryption_key=ApiIdempotencySettings.from_env().encryption_key,
    )


def get_activity_terminal_workflow(session=Depends(get_construction_scoped_session), key=Depends(require_activity_key)):
    return build_construction_activity_terminal_workflow(
        session=session, encryption_key=ApiIdempotencySettings.from_env().encryption_key,
    )


def _public(result, model, fields, *, actor):
    if not isinstance(result, dict) or set(result) != fields:
        raise ValueError("Activity response fields are invalid.")
    item = model.model_validate(result)
    data = item.model_dump(mode="json")
    if item.tenant_id != actor.tenant_id:
        raise ValueError("Activity response tenant is invalid.")
    for field in ("id", "tenant_id", "lead_id", "client_id", "project_id", "responsible_user_id", "created_by_user_id"):
        if result[field] is not None and (not isinstance(result[field], str) or result[field] != data[field]):
            raise ValueError("Activity response identifier is not canonical.")
    return item, data


def public_created_activity_result(result, *, actor, body):
    item, data = _public(result, ActivityCreatedResponse, PUBLIC_ACTIVITY_CREATED_FIELDS, actor=actor)
    command = body.command()
    if (
        item.created_by_user_id != actor.user_id or item.created_at != item.updated_at
        or item.type != command.activity_type or item.comment != command.comment
        or item.due_at != command.schedule.due_at or item.timezone != command.schedule.timezone
        or item.responsible_user_id != command.responsible_user_id
        or any(getattr(item, field) != getattr(command.links, field) for field in ("lead_id", "client_id", "project_id"))
    ):
        raise ValueError("Activity creation response binding is invalid.")
    return data


def public_terminal_activity_result(result, *, actor, activity_id, body, status):
    item, data = _public(result, ActivityTerminalResponse, PUBLIC_ACTIVITY_TERMINAL_FIELDS, actor=actor)
    supplied = body.model_dump(exclude_unset=True)
    command = ConstructionActivityTerminalCommand(**supplied)
    if (
        item.id != activity_id or item.status != status or item.row_version != body.expected_row_version+1
        or (command.result_provided and item.result != command.result)
    ):
        raise ValueError("Activity terminal response binding is invalid.")
    return data


def _http_error(error):
    if isinstance(error, ApiIdempotencyKeyReusedError):
        return ApiHttpError(status_code=409, code="IDEMPOTENCY_KEY_REUSED", message="Idempotency key was reused with a different request.")
    if isinstance(error, (ConstructionActivityIdempotencyInProgressError, ConstructionActivityTerminalInProgressError)):
        return ApiHttpError(status_code=409, code="idempotency_in_progress", message="Activity operation is processing.")
    if isinstance(error, ConstructionRowVersionConflictError):
        return ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT", message="Construction activity has changed.")
    if isinstance(error, ConstructionActivityTransitionError):
        return ApiHttpError(status_code=409, code="conflict", message="Activity transition is not allowed.")
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(status_code=403, code="forbidden", message="Construction activity operation is not available.")
    if isinstance(error, (ConstructionActivityCreateNotFoundError, ConstructionActivityTerminalNotFoundError)):
        return ApiHttpError(status_code=404, code="not_found", message="Construction activity or linked resource is not available.")
    if isinstance(error, ConstructionActivityValidationError):
        return ApiHttpError(status_code=422, code="validation_error", message="Activity request is invalid.")
    return ApiHttpError(status_code=500, code="internal_error", message="Construction activity operation failed.")


create_permission = require_construction_permission(ACTIVITY_CREATE_PERMISSION)
edit_permission = require_construction_permission(ACTIVITY_EDIT_PERMISSION)
router = ConstructionAPIRouter(prefix="/construction/crm/activities", dependencies=[
    Depends(get_current_actor), Depends(require_suite("construction", error_code="MODULE_DISABLED")),
    Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
])
ERROR_RESPONSES = {
    401: {"description": "authentication_required or invalid_token"},
    403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
    404: {"description": "not_found: activity or linked resource unavailable"},
    409: {"description": "IDEMPOTENCY_KEY_REUSED, idempotency_in_progress, ROW_VERSION_CONFLICT or conflict"},
    422: {"description": "validation_error"}, 500: {"description": "internal_error"},
}


@router.post("", status_code=201, response_model=ActivityCreateEnvelope,
             dependencies=[Depends(create_permission)], responses=ERROR_RESPONSES)
async def create_activity(request: Request, body: ActivityCreateRequest,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    workflow=Depends(get_activity_create_workflow), idempotency_key=Depends(require_activity_key)):
    try:
        result = await workflow.create_activity(actor=actor, access_context=access_context,
            activity_type=body.type, due_at=body.due_at, timezone_code=body.timezone,
            lead_id=body.lead_id, client_id=body.client_id, project_id=body.project_id,
            responsible_user_id=body.responsible_user_id, comment=body.comment,
            idempotency_key=idempotency_key, trace_id=request.state.request_id)
        data = public_created_activity_result(result, actor=actor, body=body)
    except Exception as error:
        raise _http_error(error) from None
    return success_envelope(data=data, request_id=request.state.request_id)


async def _terminal(*, workflow, actor, access_context, activity_id, body, status, key, trace_id):
    try:
        method = workflow.complete_activity if status == "completed" else workflow.cancel_activity
        # exclude_unset keeps omitted result distinct from explicit null in the request hash.
        result = await method(actor=actor, access_context=access_context, activity_id=activity_id,
            **body.model_dump(exclude_unset=True), idempotency_key=key, trace_id=trace_id)
        return public_terminal_activity_result(result, actor=actor, activity_id=activity_id, body=body, status=status)
    except Exception as error:
        raise _http_error(error) from None


@router.post("/{id}/complete", status_code=200, response_model=ActivityTerminalEnvelope,
             dependencies=[Depends(edit_permission)], responses=ERROR_RESPONSES)
async def complete_activity(id: UUID, request: Request, body: ActivityTerminalRequest,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    workflow=Depends(get_activity_terminal_workflow), idempotency_key=Depends(require_activity_key)):
    data = await _terminal(workflow=workflow, actor=actor, access_context=access_context, activity_id=id,
        body=body, status="completed", key=idempotency_key, trace_id=request.state.request_id)
    return success_envelope(data=data, request_id=request.state.request_id)


@router.post("/{id}/cancel", status_code=200, response_model=ActivityTerminalEnvelope,
             dependencies=[Depends(edit_permission)], responses=ERROR_RESPONSES)
async def cancel_activity(id: UUID, request: Request, body: ActivityTerminalRequest,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    workflow=Depends(get_activity_terminal_workflow), idempotency_key=Depends(require_activity_key)):
    data = await _terminal(workflow=workflow, actor=actor, access_context=access_context, activity_id=id,
        body=body, status="cancelled", key=idempotency_key, trace_id=request.state.request_id)
    return success_envelope(data=data, request_id=request.state.request_id)
