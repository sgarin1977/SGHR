from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_lead_rules import ConstructionLeadTransitionError, ConstructionLeadValidationError
from services.construction.construction_lead_status import LEAD_STATUS_PERMISSION, ConstructionLeadStatusNotFoundError
from services.construction.construction_lead_status_workflow import (
    PUBLIC_LEAD_STATUS_FIELDS, build_construction_lead_status_workflow,
)
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


LeadPipelineStatus = Literal[
    "new", "contacted", "measurement_scheduled", "measurement_completed",
    "estimate_preparation", "proposal_sent", "negotiation", "won", "lost",
]


class LeadStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: LeadPipelineStatus
    expected_row_version: StrictInt = Field(ge=1, le=2**63-1)


class LeadStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    status: LeadPipelineStatus
    row_version: StrictInt = Field(ge=1, le=2**63-1)


class LeadStatusEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: LeadStatusResponse
    meta: dict[str, Any]
    request_id: StrictStr


def get_lead_status_workflow(session=Depends(get_construction_scoped_session)):
    return build_construction_lead_status_workflow(session=session)


def public_status_result(result, *, actor, lead_id, body):
    if not isinstance(result, dict) or set(result) != PUBLIC_LEAD_STATUS_FIELDS:
        raise ValueError("Lead status response fields are invalid.")
    item = LeadStatusResponse.model_validate(result)
    data = item.model_dump(mode="json")
    if (
        item.id != lead_id or item.tenant_id != actor.tenant_id
        or item.status != body.status
        or item.row_version not in (body.expected_row_version, body.expected_row_version+1)
        or any(not isinstance(result[field], str) or result[field] != data[field] for field in ("id", "tenant_id"))
    ):
        raise ValueError("Lead status response binding is invalid.")
    return data


status_permission = require_construction_permission(LEAD_STATUS_PERMISSION)
router = ConstructionAPIRouter(
    prefix="/construction/crm/leads",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.patch(
    "/{id}/status", status_code=200, response_model=LeadStatusEnvelope,
    dependencies=[Depends(status_permission)],
    description="Change the lead status using expected_row_version. Unchanged status is a no-op. Idempotency-Key is not required.",
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: lead unavailable in the current tenant"},
        409: {"description": "ROW_VERSION_CONFLICT or conflict: disallowed lifecycle transition"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def change_lead_status(
    id: UUID, request: Request, body: LeadStatusRequest,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    workflow=Depends(get_lead_status_workflow),
):
    try:
        result = await workflow.transition_status(
            actor=actor, access_context=access_context, lead_id=id,
            expected_row_version=body.expected_row_version, next_status=body.status,
            trace_id=request.state.request_id,
        )
        data = public_status_result(result, actor=actor, lead_id=id, body=body)
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT",
            message="Construction lead has changed.") from None
    except ConstructionLeadTransitionError:
        raise ApiHttpError(status_code=409, code="conflict",
            message="Lead status transition is not allowed.") from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden",
            message="Construction lead status change is not available.") from None
    except ConstructionLeadStatusNotFoundError:
        raise ApiHttpError(status_code=404, code="not_found",
            message="Construction lead is not available.") from None
    except ConstructionLeadValidationError:
        raise ApiHttpError(status_code=422, code="validation_error",
            message="Lead status request is invalid.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error",
            message="Construction lead status change failed.") from None
    return success_envelope(data=data, request_id=request.state.request_id)
