from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Request
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator, model_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.settings import ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_lead_create import LEAD_CREATE_PERMISSION, LEAD_INPUT_FIELDS
from services.construction.construction_lead_create_workflow import (
    ConstructionLeadIdempotencyInProgressError, ConstructionLeadReplayUnavailableError,
    build_construction_lead_create_workflow,
)
from services.construction.construction_lead_response import LeadCreatedResponse, PUBLIC_LEAD_CREATED_FIELDS
from services.construction.construction_lead_rules import ConstructionLeadInput, ConstructionLeadValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class LeadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: StrictStr
    name: StrictStr
    work_type_description: StrictStr
    phone: StrictStr | None = None
    email: StrictStr | None = None
    address_raw: StrictStr | None = None
    comment: StrictStr | None = None
    responsible_user_id: UUID | None = None

    @field_validator("source", "name", "work_type_description")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A nonblank value is required.")
        return value

    @model_validator(mode="after")
    def approved_input(self):
        # Validate without changing raw values used by the request hash.
        ConstructionLeadInput(**self.model_dump())
        return self


class LeadCreateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: LeadCreatedResponse
    meta: dict[str, Any]
    request_id: StrictStr


def require_lead_create_key(
    request: Request,
    value: Annotated[str, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Required: same key and command replay the creation result; changed command returns 409 IDEMPOTENCY_KEY_REUSED.",
    )],
):
    if len(request.headers.getlist("Idempotency-Key")) != 1 or not value.strip():
        raise ApiHttpError(status_code=422, code="validation_error", message="Idempotency key is invalid.")
    return value


def get_lead_create_workflow(
    session=Depends(get_construction_scoped_session),
    idempotency_key=Depends(require_lead_create_key),
):
    return build_construction_lead_create_workflow(
        session=session, encryption_key=ApiIdempotencySettings.from_env().encryption_key,
    )


def public_created_lead_result(result, *, actor, body):
    if not isinstance(result, dict) or set(result) != PUBLIC_LEAD_CREATED_FIELDS:
        raise ValueError("Created lead response is invalid.")
    item = LeadCreatedResponse.model_validate(result)
    data = item.model_dump(mode="json")
    normalized = ConstructionLeadInput(**body.model_dump())
    expected = {field: getattr(normalized, field) for field in LEAD_INPUT_FIELDS}
    expected["responsible_user_id"] = str(normalized.responsible_user_id) if normalized.responsible_user_id else None
    if item.tenant_id != actor.tenant_id or any(data[field] != value for field, value in expected.items()):
        raise ValueError("Created lead response context is invalid.")
    return data


create_permission = require_construction_permission(LEAD_CREATE_PERMISSION)
router = ConstructionAPIRouter(
    prefix="/construction/crm/leads",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.post(
    "", status_code=201, response_model=LeadCreateEnvelope,
    dependencies=[Depends(create_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: created lead unavailable on replay"},
        409: {"description": "IDEMPOTENCY_KEY_REUSED or idempotency_in_progress"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def create_lead(
    request: Request, body: LeadCreateRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    workflow=Depends(get_lead_create_workflow),
    idempotency_key=Depends(require_lead_create_key),
):
    try:
        result = await workflow.create_lead(
            actor=actor, access_context=access_context, **body.model_dump(),
            idempotency_key=idempotency_key, trace_id=request.state.request_id,
        )
        data = public_created_lead_result(result, actor=actor, body=body)
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(
            status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with a different request.",
        ) from None
    except ConstructionLeadIdempotencyInProgressError:
        raise ApiHttpError(
            status_code=409, code="idempotency_in_progress", message="Lead creation is processing.",
        ) from None
    except ConstructionLeadReplayUnavailableError:
        raise ApiHttpError(
            status_code=404, code="not_found", message="Created lead is not available.",
        ) from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Construction lead creation is not available.",
        ) from None
    except ConstructionLeadValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Lead creation request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Construction lead creation failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
