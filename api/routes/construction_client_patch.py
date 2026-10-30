"""Versioned client editing through the existing Construction domain service."""
from services.construction.construction_api_services import (
    build_client_patch_service,
)
# api_service_boundary_v1
from typing import Literal
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_client_read import ClientReadResponse
from services.construction.construction_clients import (
    ConstructionClientService, ConstructionClientNotFoundError,
    ConstructionClientValidationError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


class ClientPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt = Field(ge=1, le=2**63-1)
    # Defaults are excluded unless the caller explicitly supplies these fields.
    display_name: StrictStr = Field(default_factory=str)
    client_type: Literal["person", "company"] = "person"
    phone: StrictStr | None = None
    email: StrictStr | None = None
    notes: StrictStr | None = None

    @field_validator("display_name")
    @classmethod
    def nonblank_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Client name is required.")
        return value

    def patch_fields(self):
        return self.model_dump(
            include=self.model_fields_set - {"expected_row_version"},
        )


class ClientPatchEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ClientReadResponse
    meta: dict
    request_id: str



def get_client_patch_service(session=Depends(get_construction_scoped_session)):
    return build_client_patch_service(session)


router = ConstructionAPIRouter(
    prefix="/construction/clients",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.patch(
    "/{id}", response_model=ClientPatchEnvelope,
    dependencies=[
        Depends(require_construction_permission("construction.projects.edit")),
    ],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Module, permission or tenant-wide client scope denied"},
        404: {"description": "Live client unavailable"},
        409: {"description": "ROW_VERSION_CONFLICT"},
        422: {"description": "Invalid client fields or expected version"},
        500: {"description": "Client update failed"},
    },
)
async def patch_client(
    id: UUID, request: Request, body: ClientPatchRequest,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_client_patch_service),
):
    fields = body.patch_fields()
    try:
        result = await service.patch_client(
            actor=actor, access_context=access_context, client_id=id,
            expected_row_version=body.expected_row_version,
            fields=fields, trace_id=request.state.request_id,
        )
        item = ClientReadResponse.model_validate({
            name: getattr(result, name)
            for name in ClientReadResponse.model_fields
        })
        if (
            item.id != id or item.tenant_id != actor.tenant_id
            or item.row_version not in (
                body.expected_row_version, body.expected_row_version + 1,
            )
            or any(getattr(item, name) != value for name, value in fields.items())
        ):
            raise ValueError("Client patch response context differs.")
        data = item.model_dump(mode="json")
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Client editing is unavailable.",
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
            message="Client update fields are invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Client update failed.",
        ) from None
    return success_envelope(data=data, request_id=request.state.request_id)
