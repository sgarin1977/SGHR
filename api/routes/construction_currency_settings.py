"""CON-012 organization default currency using the shared settings boundary."""
from dataclasses import asdict
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Response
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictStr,
)

from api.auth import (
    get_current_actor, require_authenticated_user_rate_limit,
    require_module, require_suite,
)
from api.construction_settings_contract import SettingsVersion
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.responses import success_envelope
from services.construction.construction_currency_settings import (
    OrganizationCurrencyContext,
    OrganizationCurrencyChange,
    CurrencySettingsDenied,
    CurrencySettingsValidationError,
    CurrencySettingsConfigurationError,
    CurrencySettingsConflict,
    CurrencySettingsOperationError,
    build_construction_currency_settings_service,
)


class CurrencyOptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    name: StrictStr = Field(min_length=1)


class CurrencySettingsStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    organization_default_currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    organization_updated_at: AwareDatetime
    row_version: SettingsVersion


class CurrencySettingsReadData(CurrencySettingsStateResponse):
    currencies: list[CurrencyOptionResponse]


class CurrencySettingsWriteData(CurrencySettingsStateResponse):
    changed: StrictBool


class CurrencySettingsReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: CurrencySettingsReadData
    meta: dict
    request_id: StrictStr


class CurrencySettingsWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: CurrencySettingsWriteData
    meta: dict
    request_id: StrictStr


class CurrencySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    expected_row_version: SettingsVersion


def get_currency_settings_service(platform_session=Depends(get_api_session)):
    return build_construction_currency_settings_service(
        platform_session=platform_session
    )


ERRORS = (
    CurrencySettingsDenied,
    CurrencySettingsValidationError,
    CurrencySettingsConfigurationError,
    CurrencySettingsConflict,
    CurrencySettingsOperationError,
)


def currency_error(error):
    if isinstance(error, CurrencySettingsDenied):
        return ApiHttpError(
            status_code=403, code="forbidden",
            message="Organization currency settings are unavailable.",
        )
    if isinstance(error, CurrencySettingsValidationError):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Currency settings request is invalid.",
        )
    if isinstance(error, CurrencySettingsConfigurationError):
        return ApiHttpError(
            status_code=409, code="CURRENCY_CONFIGURATION_REQUIRED",
            message="Organization currency configuration is required.",
        )
    if isinstance(error, CurrencySettingsConflict):
        return ApiHttpError(
            status_code=409, code="ORGANIZATION_SETTINGS_CONFLICT",
            message="Organization settings changed. Refresh before saving.",
        )
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Currency settings operation failed.",
    )


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
        409: {"description": "CURRENCY_CONFIGURATION_REQUIRED or ORGANIZATION_SETTINGS_CONFLICT"},
        422: {"description": "Invalid currency, expected row version or request selectors"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "Sanitized operation failure"},
    },
)


def reject_selectors(request):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Currency settings accept no query selectors.",
        )


def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


@router.get("/currency", response_model=CurrencySettingsReadEnvelope)
async def read_currency_settings(
    request: Request,
    response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_currency_settings_service),
):
    reject_selectors(request)
    try:
        result = await service.read_for_actor(actor=actor)
        if (
            not isinstance(result, OrganizationCurrencyContext)
            or result.tenant_id != actor.tenant_id
        ):
            raise CurrencySettingsOperationError(
                "Currency response context differs."
            )

        data = CurrencySettingsReadData(
            tenant_id=result.tenant_id,
            organization_default_currency=result.default_currency,
            organization_updated_at=result.updated_at,
            row_version=result.row_version,
            currencies=[asdict(value) for value in result.currencies],
        )
    except ERRORS as error:
        raise currency_error(error) from None

    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"),
        request_id=request.state.request_id,
    )


@router.patch("/currency", response_model=CurrencySettingsWriteEnvelope)
async def update_currency_settings(
    payload: CurrencySettingsUpdate,
    request: Request,
    response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_currency_settings_service),
):
    reject_selectors(request)
    try:
        result = await service.update_for_actor(
            actor=actor,
            currency_code=payload.default_currency,
            expected_row_version=payload.expected_row_version,
            trace_id=request.state.request_id,
        )
        if (
            not isinstance(result, OrganizationCurrencyChange)
            or result.tenant_id != actor.tenant_id
        ):
            raise CurrencySettingsOperationError(
                "Currency response context differs."
            )

        data = CurrencySettingsWriteData(
            tenant_id=result.tenant_id,
            organization_default_currency=result.default_currency,
            organization_updated_at=result.updated_at,
            row_version=result.row_version,
            changed=result.changed,
        )
    except ERRORS as error:
        raise currency_error(error) from None

    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"),
        request_id=request.state.request_id,
    )

# con_012_currency_settings_v1
