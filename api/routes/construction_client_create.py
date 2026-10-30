"""Standalone CON-003 creation using the existing client domain service."""
from typing import Annotated, Literal

from fastapi import Depends, Header, Request
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_client_read import ClientReadResponse
from api.settings import ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_clients import (
    ConstructionClientValidationError, ConstructionClientNotFoundError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_client_create_workflow import (
    ClientCreationInProgressError, build_construction_client_create_workflow,
)

class ClientCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: StrictStr
    client_type: Literal["person", "company"]
    phone: StrictStr | None = None
    email: StrictStr | None = None
    notes: StrictStr | None = None

    @field_validator("display_name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Client name is required.")
        return value

class ClientCreateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ClientReadResponse
    meta: dict
    request_id: str

def optional_client_key(
    request: Request,
    value: Annotated[str | None, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Optional: replay the original result; changed request returns 409 IDEMPOTENCY_KEY_REUSED.",
    )] = None,
):
    if len(request.headers.getlist("Idempotency-Key")) > 1 or (
        value is not None and not value.strip()
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Idempotency key is invalid.",
        )
    return value

def get_client_create_workflow(
    session=Depends(get_construction_scoped_session),
    key=Depends(optional_client_key),
):
    encryption_key = ApiIdempotencySettings.from_env().encryption_key if key is not None else None
    return build_construction_client_create_workflow(
        session=session, idempotency_encryption_key=encryption_key,
    )

router = ConstructionAPIRouter(
    prefix="/construction/clients",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)

@router.post(
    "", status_code=201, response_model=ClientCreateEnvelope,
    dependencies=[Depends(require_construction_permission("construction.projects.create"))],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Construction creation permission or scope denied"},
        404: {"description": "Original client unavailable on replay"},
        409: {"description": "IDEMPOTENCY_KEY_REUSED or idempotency_in_progress"},
        422: {"description": "validation_error"},
        500: {"description": "internal_error"},
    },
)
async def create_client(
    request: Request, body: ClientCreateRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    workflow=Depends(get_client_create_workflow),
    key=Depends(optional_client_key),
):
    try:
        result = await workflow.create_client(
            actor=actor, access_context=access_context,
            **body.model_dump(), idempotency_key=key,
            trace_id=request.state.request_id,
        )
        item = ClientReadResponse.model_validate(result)
        if (
            item.tenant_id != actor.tenant_id
            or item.display_name != body.display_name.strip()
            or item.client_type != body.client_type
            or item.phone != body.phone or item.email != body.email
            or item.notes != body.notes
            or item.linked_user_id is not None or item.row_version != 1
        ):
            raise ValueError("Client creation response differs.")
        data = item.model_dump(mode="json")
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(
            status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with another request.",
        ) from None
    except ClientCreationInProgressError:
        raise ApiHttpError(
            status_code=409, code="idempotency_in_progress",
            message="Client creation is processing.",
        ) from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Client creation is unavailable.",
        ) from None
    except ConstructionClientNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found",
            message="Created client is unavailable.",
        ) from None
    except ConstructionClientValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Client creation input is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Client creation failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
