"""Organization region over the shared Platform settings boundary."""
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator,
)
from api.auth import (
    get_current_actor, require_authenticated_user_rate_limit, require_module, require_suite,
)
from api.construction_settings_contract import SettingsVersion
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.responses import success_envelope
from services.construction.construction_organization_region import (
    OrganizationRegionState, OrganizationRegionResult,
    OrganizationRegionDenied, OrganizationRegionValidationError,
    OrganizationRegionConfigurationError, OrganizationRegionConflict,
    OrganizationRegionOperationError, validate_region,
    build_construction_organization_region_service,
)


class RegionSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country_code: StrictStr = Field(pattern=r"^[A-Z]{2}$", min_length=2, max_length=2)
    timezone: StrictStr = Field(min_length=1, max_length=255)
    expected_row_version: SettingsVersion

    @model_validator(mode="after")
    def explicit_region(self):
        validate_region(country_code=self.country_code, timezone_code=self.timezone)
        return self


class RegionSettingsReadData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    country_code: StrictStr | None = Field(pattern=r"^[A-Z]{2}$", min_length=2, max_length=2)
    timezone: StrictStr | None = Field(min_length=1, max_length=255)
    configured: StrictBool
    organization_updated_at: AwareDatetime
    row_version: SettingsVersion

    @model_validator(mode="after")
    def coherent_region(self):
        if self.configured:
            validate_region(country_code=self.country_code, timezone_code=self.timezone)
        elif self.country_code is not None or self.timezone is not None:
            raise ValueError("An unconfigured region must have no inferred values.")
        return self


class RegionSettingsWriteData(RegionSettingsReadData):
    changed: StrictBool


class RegionSettingsReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: RegionSettingsReadData
    meta: dict
    request_id: StrictStr


class RegionSettingsWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: RegionSettingsWriteData
    meta: dict
    request_id: StrictStr


def get_region_settings_service(platform_session=Depends(get_api_session)):
    return build_construction_organization_region_service(platform_session=platform_session)


def region_error(error):
    if isinstance(error, OrganizationRegionDenied):
        return ApiHttpError(status_code=403, code="forbidden",
                            message="Organization region settings are unavailable.")
    if isinstance(error, OrganizationRegionValidationError):
        return ApiHttpError(status_code=422, code="validation_error",
                            message="Region settings request is invalid.")
    if isinstance(error, OrganizationRegionConfigurationError):
        return ApiHttpError(status_code=409, code="REGION_CONFIGURATION_REQUIRED",
                            message="Organization region configuration requires attention.")
    if isinstance(error, OrganizationRegionConflict):
        return ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT",
                            message="Organization settings changed. Refresh before saving.")
    return ApiHttpError(status_code=500, code="internal_error",
                        message="Region settings operation failed.")


router = APIRouter(
    prefix="/construction/settings",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_authenticated_user_rate_limit()),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={
        401: {"description": "Platform authentication required"},
        403: {"description": "Membership, module or settings authority unavailable"},
        409: {"description": "ROW_VERSION_CONFLICT or REGION_CONFIGURATION_REQUIRED"},
        422: {"description": "Invalid region, expected version or request selectors"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "Sanitized operation failure"},
    },
)


def reject_selectors(request):
    if request.query_params:
        raise ApiHttpError(status_code=422, code="validation_error",
                           message="Region settings accept no query selectors.")


def state_data(state, actor):
    if not isinstance(state, OrganizationRegionState) or state.tenant_id != actor.tenant_id:
        raise OrganizationRegionOperationError("Region response context differs.")
    return RegionSettingsReadData(
        tenant_id=state.tenant_id, country_code=state.country_code, timezone=state.timezone,
        configured=state.configured, organization_updated_at=state.updated_at,
        row_version=state.row_version)


def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


@router.get("/region", response_model=RegionSettingsReadEnvelope)
async def read_region_settings(
    request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_region_settings_service),
):
    reject_selectors(request)
    try:
        data = state_data(await service.read_for_actor(actor=actor), actor)
    except Exception as error:
        raise region_error(error) from None
    no_store(response)
    return success_envelope(data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.patch("/region", response_model=RegionSettingsWriteEnvelope)
async def update_region_settings(
    payload: RegionSettingsUpdate, request: Request, response: Response,
    actor=Depends(get_current_actor), service=Depends(get_region_settings_service),
):
    reject_selectors(request)
    try:
        result = await service.update_for_actor(
            actor=actor, country_code=payload.country_code, timezone_code=payload.timezone,
            expected_row_version=payload.expected_row_version, trace_id=request.state.request_id)
        if not isinstance(result, OrganizationRegionResult) or type(result.changed) is not bool:
            raise OrganizationRegionOperationError("Region write response is invalid.")
        state = state_data(result.region, actor)
        if (not state.configured or state.country_code != payload.country_code
                or state.timezone != payload.timezone
                or state.row_version != payload.expected_row_version + int(result.changed)):
            raise OrganizationRegionOperationError("Region write response differs.")
        data = RegionSettingsWriteData(**state.model_dump(), changed=result.changed)
    except Exception as error:
        raise region_error(error) from None
    no_store(response)
    return success_envelope(data=data.model_dump(mode="json"), request_id=request.state.request_id)
