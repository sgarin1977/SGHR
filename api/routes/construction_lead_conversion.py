from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.settings import ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_lead_conversion import ConstructionLeadConversionCommand, ConstructionLeadConversionNotFoundError
from services.construction.construction_lead_conversion_rules import ConstructionLeadConversionSelection
from services.construction.construction_lead_conversion_workflow import (
    CONVERTED_RESPONSE_FIELDS, ConstructionLeadConversionInProgressError,
    build_construction_lead_conversion_workflow,
)
from services.construction.construction_lead_rules import ConstructionLeadTransitionError, ConstructionLeadValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class LeadConversionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt = Field(ge=1, le=2**63 - 1)
    client_id: UUID | None = None
    project_id: UUID | None = None
    client_type: StrictStr | None = None
    project_name: StrictStr | None = None
    project_address_raw: StrictStr | None = None

    @model_validator(mode="after")
    def approved_selection(self):
        # Keep raw text for workflow hashing; normalization remains in the command/service.
        ConstructionLeadConversionCommand(
            expected_row_version=self.expected_row_version,
            selection=ConstructionLeadConversionSelection(client_id=self.client_id, project_id=self.project_id),
            client_type=self.client_type, project_name=self.project_name,
            project_address_raw=self.project_address_raw,
        )
        return self


class LeadConversionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lead_id: UUID
    tenant_id: UUID
    client_id: UUID
    project_id: UUID
    status: Literal["won"]
    row_version: StrictInt = Field(ge=1, le=2**63 - 1)


class LeadConversionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: LeadConversionResponse
    meta: dict[str, Any]
    request_id: StrictStr


def require_lead_conversion_key(
    request: Request,
    value: Annotated[str, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Required: the same key and command return the original conversion result; changed command returns 409 IDEMPOTENCY_KEY_REUSED.",
    )],
):
    if len(request.headers.getlist("Idempotency-Key")) != 1 or not value.strip():
        raise ApiHttpError(status_code=422, code="validation_error", message="Idempotency key is invalid.")
    return value


def get_lead_conversion_workflow(
    session=Depends(get_construction_scoped_session),
    idempotency_key=Depends(require_lead_conversion_key),
):
    return build_construction_lead_conversion_workflow(
        session=session, encryption_key=ApiIdempotencySettings.from_env().encryption_key,
    )


def public_conversion_result(result, *, actor, lead_id, body):
    if not isinstance(result, dict) or set(result) != CONVERTED_RESPONSE_FIELDS:
        raise ValueError("Conversion response fields are invalid.")
    item = LeadConversionResponse.model_validate(result)
    data = item.model_dump(mode="json")
    if (
        item.tenant_id != actor.tenant_id or item.lead_id != lead_id
        or any(not isinstance(result[field], str) or result[field] != data[field]
               for field in ("lead_id", "tenant_id", "client_id", "project_id"))
        or (body.client_id is not None and item.client_id != body.client_id)
        or (body.project_id is not None and item.project_id != body.project_id)
    ):
        raise ValueError("Conversion response binding is invalid.")
    return data


convert_permission = require_construction_permission("construction.projects.edit")
router = ConstructionAPIRouter(
    prefix="/construction/crm/leads",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.post(
    "/{id}/convert", status_code=200, response_model=LeadConversionEnvelope,
    dependencies=[Depends(convert_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: lead or target resources unavailable"},
        409: {"description": "ROW_VERSION_CONFLICT, IDEMPOTENCY_KEY_REUSED, idempotency_in_progress or conflict"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def convert_lead(
    id: UUID, request: Request, body: LeadConversionRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    workflow=Depends(get_lead_conversion_workflow),
    idempotency_key=Depends(require_lead_conversion_key),
):
    try:
        result = await workflow.convert_lead(
            actor=actor, access_context=access_context, lead_id=id, **body.model_dump(),
            idempotency_key=idempotency_key, trace_id=request.state.request_id,
        )
        data = public_conversion_result(result, actor=actor, lead_id=id, body=body)
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with a different request.") from None
    except ConstructionLeadConversionInProgressError:
        raise ApiHttpError(status_code=409, code="idempotency_in_progress",
            message="Lead conversion is processing.") from None
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT",
            message="Construction lead has changed.") from None
    except ConstructionLeadTransitionError:
        raise ApiHttpError(status_code=409, code="conflict",
            message="Lead cannot be converted in its current state.") from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden",
            message="Construction lead conversion is not available.") from None
    except ConstructionLeadConversionNotFoundError:
        raise ApiHttpError(status_code=404, code="not_found",
            message="Conversion resources are not available.") from None
    except ConstructionLeadValidationError:
        raise ApiHttpError(status_code=422, code="validation_error",
            message="Lead conversion request is invalid.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error",
            message="Construction lead conversion failed.") from None
    return success_envelope(data=data, request_id=request.state.request_id)
