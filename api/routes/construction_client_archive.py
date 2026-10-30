"""Client soft archive through the existing versioned domain operation."""
from datetime import timezone
from uuid import UUID

from fastapi import Depends, Request
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, field_validator,
)

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    require_construction_permission,
)
from api.routes.construction_client_patch import get_client_patch_service
from services.construction.construction_clients import (
    ConstructionClientNotFoundError, ConstructionClientValidationError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


class ClientArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt = Field(ge=1, le=2**63-1)


class ClientArchiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    row_version: StrictInt = Field(ge=1, le=2**63-1)
    deleted_at: AwareDatetime

    @field_validator("deleted_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)


class ClientArchiveEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ClientArchiveResponse
    meta: dict
    request_id: str


router = ConstructionAPIRouter(
    prefix="/construction/clients",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.post(
    "/{id}/archive", response_model=ClientArchiveEnvelope,
    dependencies=[
        Depends(require_construction_permission("construction.projects.edit")),
    ],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Module, permission or tenant-wide client scope denied"},
        404: {"description": "Live client unavailable"},
        409: {"description": "ROW_VERSION_CONFLICT"},
        422: {"description": "Invalid expected version"},
        500: {"description": "Client archive failed"},
    },
)
async def archive_client(
    id: UUID, request: Request, body: ClientArchiveRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_client_patch_service),
):
    try:
        result = await service.archive_client(
            actor=actor, access_context=access_context, client_id=id,
            expected_row_version=body.expected_row_version,
            trace_id=request.state.request_id,
        )
        item = ClientArchiveResponse.model_validate({
            name: getattr(result, name)
            for name in ClientArchiveResponse.model_fields
        })
        if (
            item.id != id or item.tenant_id != actor.tenant_id
            or item.row_version != body.expected_row_version + 1
        ):
            raise ValueError("Client archive receipt differs.")
        data = item.model_dump(mode="json")
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Client archiving is unavailable.",
        ) from None
    except ConstructionClientNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found",
            message="Client is unavailable.",
        ) from None
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(
            status_code=409, code="ROW_VERSION_CONFLICT",
            message="Client version has changed. Reload the client.",
        ) from None
    except ConstructionClientValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Client archive request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Client archive failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
