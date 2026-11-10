"""CON-016 organization tax-rule read boundary on the shared Construction API."""
from dataclasses import asdict
from datetime import timezone
from decimal import Decimal
from uuid import UUID
from typing import Literal

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
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleNotFoundError, ConstructionTaxRuleState,
    ConstructionTaxRuleValidationError, build_construction_tax_rule_service,
)


class TaxRuleReadData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    tax_profile_id: UUID
    category: Literal["WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER"]
    rate_percent: Decimal = Field(
        ge=0, le=100, allow_inf_nan=False,
        description="Exact percentage from 0 to 100; serialized as a decimal string, never a JSON float.",
    )
    row_version: SettingsVersion
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("rate_percent", mode="before")
    @classmethod
    def exact_rate(cls, value):
        if not isinstance(value, (Decimal, str)):
            raise ValueError("A Decimal or exact decimal string is required.")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)


class TaxRuleReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: TaxRuleReadData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def get_tax_rule_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_tax_rule_service(session=session)


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
    "/{profile_id}/rules/{rule_id}",
    response_model=TaxRuleReadEnvelope,
    dependencies=[Depends(read_permission)],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: profile or rule unavailable in the authenticated organization"},
        422: {"description": "validation_error: invalid UUID or unsupported query selectors"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized operation or response failure"},
    },
)
async def read_tax_rule(
    profile_id: UUID, rule_id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_tax_rule_read_service),
):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Tax rule reads accept no query selectors.",
        )
    try:
        result = await service.get_rule(actor=actor, profile_id=profile_id, rule_id=rule_id)
        if (not isinstance(result, ConstructionTaxRuleState)
                or result.tenant_id != actor.tenant_id or result.tax_profile_id != profile_id
                or result.id != rule_id or not isinstance(result.rate_percent, Decimal)):
            raise ValueError("Tax rule response context differs.")
        data = TaxRuleReadData.model_validate(asdict(result)).model_dump(mode="json")
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden", message="Organization tax rule access is unavailable.",
        ) from None
    except ConstructionTaxRuleNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found", message="Tax profile or rule is unavailable.",
        ) from None
    except ConstructionTaxRuleValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error", message="Tax rule request is invalid.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error", message="Tax rule read failed.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(data=data, request_id=request.state.request_id)

# con016_canonical_categories_unique_v1
