"""CON-015 authenticated organization tax-profile read boundary."""
from dataclasses import asdict
from datetime import timezone
from typing import Literal
from uuid import UUID

from fastapi import Depends, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileNotFoundError, ConstructionTaxProfileState,
    ConstructionTaxProfileValidationError, build_construction_tax_profile_service,
)


class TaxProfileReadData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    name: StrictStr = Field(min_length=1, max_length=255)
    client_price_tax_mode: Literal["TAX_EXCLUSIVE", "TAX_INCLUSIVE"]
    material_cost_basis: Literal["NET", "GROSS"]
    row_version: SettingsVersion
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)


class TaxProfileReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: TaxProfileReadData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def get_tax_profile_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_tax_profile_service(session=session)


router = ConstructionAPIRouter(
    prefix="/construction/settings/tax-profiles",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)

read_permission = require_construction_permission("construction.tax.read")


@router.get(
    "/{profile_id}", response_model=TaxProfileReadEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden: organization tax scope required"},
        404: {"description": "not_found: profile unavailable in the authenticated organization"},
        422: {"description": "validation_error: invalid UUID or unsupported query selectors"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized operation or response failure"},
    },
)
async def read_tax_profile(
    profile_id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_tax_profile_read_service),
):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Tax profile reads accept no query selectors.",
        )
    try:
        result = await service.get_profile(actor=actor, profile_id=profile_id)
        if (not isinstance(result, ConstructionTaxProfileState)
                or result.tenant_id != actor.tenant_id or result.id != profile_id):
            raise ValueError("Tax profile response context differs.")
        data = TaxProfileReadData.model_validate(asdict(result)).model_dump(mode="json")
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Organization tax profile access is unavailable.",
        ) from None
    except ConstructionTaxProfileNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found", message="Tax profile is unavailable.",
        ) from None
    except ConstructionTaxProfileValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Tax profile request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Tax profile read failed.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(data=data, request_id=request.state.request_id)
