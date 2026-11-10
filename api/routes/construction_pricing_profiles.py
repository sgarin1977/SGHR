"""CON-017 organization pricing reads on the shared Construction API."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import Depends, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission)
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_pricing_profiles import (
    LEVELS, ConstructionPricingProfileState,
    ConstructionPricingProfileNotFoundError, ConstructionPricingProfileValidationError,
    build_construction_pricing_profile_service)


class PricingProfileReadData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    code: Literal["ECONOMY", "STANDARD", "PREMIUM"]
    work_coefficient: Decimal = Field(
        gt=0, allow_inf_nan=False,
        description="Exact positive commercial sale-price coefficient, serialized as a decimal string.")
    material_markup_percent: Decimal = Field(
        ge=0, le=100, allow_inf_nan=False,
        description="Exact percentage, serialized as a decimal string. Applies only in COST_PLUS mode.")
    row_version: SettingsVersion
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("work_coefficient", "material_markup_percent", mode="before")
    @classmethod
    def exact_decimal(cls, value):
        if not isinstance(value, (Decimal, str)):
            raise ValueError("An exact Decimal or decimal string is required.")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)


class PricingProfileReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: PricingProfileReadData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


class PricingProfileListPagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_cursor: None


class PricingProfileListMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagination: PricingProfileListPagination


class PricingProfileListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[PricingProfileReadData] = Field(max_length=3)
    meta: PricingProfileListMeta
    request_id: StrictStr = Field(min_length=1)


def get_pricing_profile_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_pricing_profile_service(session=session)


def public_profile(state, *, actor, profile_id=None):
    if (not isinstance(state, ConstructionPricingProfileState)
            or not all(isinstance(getattr(state, name), UUID) for name in ("id", "tenant_id", "created_by"))
            or state.tenant_id != actor.tenant_id
            or (profile_id is not None and state.id != profile_id)
            or not all(isinstance(getattr(state, name), datetime) for name in ("created_at", "updated_at"))
            or not all(isinstance(getattr(state, name), Decimal)
                       for name in ("work_coefficient", "material_markup_percent"))):
        raise ValueError("Pricing response context differs.")
    return PricingProfileReadData.model_validate(asdict(state)).model_dump(mode="json")


def public_list(states, *, actor):
    if not isinstance(states, tuple) or len(states) > len(LEVELS):
        raise ValueError("Pricing collection differs.")
    data = [public_profile(state, actor=actor) for state in states]
    codes = [state.code for state in states]
    if (len(set(codes)) != len(codes) or codes != sorted(codes, key=LEVELS.index)
            or len({state.id for state in states}) != len(states)):
        raise ValueError("Pricing collection order or identity differs.")
    return data


def read_error(error):
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(status_code=403, code="forbidden", message="Organization pricing access is unavailable.")
    if isinstance(error, ConstructionPricingProfileNotFoundError):
        return ApiHttpError(status_code=404, code="not_found", message="Pricing profile is unavailable.")
    if isinstance(error, ConstructionPricingProfileValidationError):
        return ApiHttpError(status_code=422, code="validation_error", message="Pricing request is invalid.")
    return ApiHttpError(status_code=500, code="internal_error", message="Pricing profile read failed.")


def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


router = ConstructionAPIRouter(
    prefix="/construction/settings/pricing-profiles",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ])
read_permission = require_construction_permission("construction.pricing.read")
READ_RESPONSES = {
    401: {"description": "authentication_required or invalid_token"},
    403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
    422: {"description": "validation_error: unsupported query selectors or invalid UUID"},
    429: {"description": "RATE_LIMIT_EXCEEDED"},
    500: {"description": "internal_error: sanitized operation or response failure"},
}


@router.get("", response_model=PricingProfileListEnvelope,
            dependencies=[Depends(read_permission)], responses=READ_RESPONSES)
async def list_pricing_profiles(
    request: Request, response: Response, actor=Depends(get_current_actor),
    service=Depends(get_pricing_profile_read_service),
):
    if request.query_params:
        raise ApiHttpError(status_code=422, code="validation_error",
                           message="Pricing profile reads accept no query selectors.")
    try:
        states = await service.list_profiles(actor=actor)
        data = public_list(states, actor=actor)
    except Exception as error:
        raise read_error(error) from None
    private_response(response)
    return success_envelope(data=data, meta={"pagination": {"next_cursor": None}},
                            request_id=request.state.request_id)


@router.get("/{profile_id}", response_model=PricingProfileReadEnvelope,
            dependencies=[Depends(read_permission)],
            responses={**READ_RESPONSES, 404: {"description": "not_found: profile unavailable in the current organization"}})
async def read_pricing_profile(
    profile_id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_pricing_profile_read_service),
):
    if request.query_params:
        raise ApiHttpError(status_code=422, code="validation_error",
                           message="Pricing profile reads accept no query selectors.")
    try:
        state = await service.get_profile(actor=actor, profile_id=profile_id)
        data = public_profile(state, actor=actor, profile_id=profile_id)
    except Exception as error:
        raise read_error(error) from None
    private_response(response)
    return success_envelope(data=data, request_id=request.state.request_id)
