"""CON-019 current-organization calculation configuration."""
import re
from dataclasses import asdict
from datetime import timezone
from decimal import Decimal, DecimalException
from typing import Annotated

from fastapi import Depends, Header, Request, Response
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt,
    StrictStr, field_validator, model_validator,
)
from uuid import UUID

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session,
    require_construction_permission,
)
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_calculation_settings import (
    ConstructionCalculationSettingsService as Service,
    ConstructionCalculationSettingsState as State,
    ConstructionCalculationSettingsResult as Result,
    ConstructionCalculationSettingsValidationError as Validation,
    ConstructionCalculationSettingsNotFoundError as NotFound,
    ConstructionCalculationSettingsConflictError as Conflict,
    validated_fields,
    build_construction_calculation_settings_service,
)
from services.construction.construction_calculation_settings_workflow import (
    ConstructionCalculationSettingsInProgressError as InProgress,
    build_construction_calculation_settings_workflow,
)

DECIMAL_PATTERN = r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
DECIMALS = (
    "minimum_job_amount", "default_technological_waste_percent",
    "default_local_repair_percent",
)
NON_NULLABLE = (
    "money_input_precision", "quantity_precision", "supplier_stale_price_days",
)
Precision = Annotated[StrictInt, Field(ge=0, le=6)]
SupplierDays = Annotated[StrictInt, Field(ge=0, le=3650)]
DecimalText = Annotated[StrictStr, Field(pattern=DECIMAL_PATTERN)]
CurrencyText = Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")]


def domain_fields(values, *, complete=False):
    fields = dict(values)
    for name in DECIMALS:
        if name in fields and fields[name] is not None:
            value = fields[name]
            if not isinstance(value, str) or re.fullmatch(DECIMAL_PATTERN, value) is None:
                raise Validation("An exact decimal string is required.")
            try:
                fields[name] = Decimal(value)
            except DecimalException:
                raise Validation("An exact finite decimal is required.") from None
    return validated_fields(fields, complete=complete)


class CalculationSettingsCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    money_input_precision: Precision
    supplier_stale_price_days: SupplierDays
    quantity_precision: Precision = 4
    minimum_job_amount: DecimalText | None = None
    minimum_job_currency: CurrencyText | None = None
    default_technological_waste_percent: DecimalText | None = None
    default_local_repair_percent: DecimalText | None = None

    @model_validator(mode="after")
    def validate_configuration(self):
        self.domain_fields()
        return self

    def domain_fields(self):
        return domain_fields(self.model_dump(), complete=True)


def sparse_schema(schema):
    for name in NON_NULLABLE:
        field = schema["properties"][name]
        variants = [
            part for part in field.get("anyOf", [])
            if part.get("type") != "null"
        ]
        if len(variants) != 1:
            raise ValueError("Sparse integer field schema differs.")
        metadata = {
            key: value for key, value in field.items()
            if key not in ("anyOf", "default")
        }
        field.clear()
        field.update(variants[0])
        field.update(metadata)


class CalculationSettingsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=sparse_schema)
    expected_row_version: SettingsVersion
    money_input_precision: Precision | None = None
    quantity_precision: Precision | None = None
    supplier_stale_price_days: SupplierDays | None = None
    minimum_job_amount: DecimalText | None = None
    minimum_job_currency: CurrencyText | None = None
    default_technological_waste_percent: DecimalText | None = None
    default_local_repair_percent: DecimalText | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_required_value_clear(cls, values):
        if isinstance(values, dict) and any(
            name in values and values[name] is None for name in NON_NULLABLE
        ):
            raise ValueError("Precision and supplier lifetime cannot be cleared.")
        return values

    @model_validator(mode="after")
    def validate_configuration(self):
        self.domain_fields()
        return self

    def domain_fields(self):
        return domain_fields(self.model_dump(
            exclude_unset=True, exclude={"expected_row_version"}))


class CalculationSettingsReadData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    money_input_precision: Precision
    quantity_precision: Precision
    minimum_job_amount: Decimal | None = Field(ge=0, allow_inf_nan=False)
    minimum_job_currency: CurrencyText | None
    supplier_stale_price_days: SupplierDays
    default_technological_waste_percent: Decimal | None = Field(
        ge=0, le=100, allow_inf_nan=False)
    default_local_repair_percent: Decimal | None = Field(
        ge=0, le=100, allow_inf_nan=False)
    row_version: SettingsVersion
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)


class CalculationSettingsReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: CalculationSettingsReadData | None
    meta: dict
    request_id: StrictStr = Field(min_length=1)


class CalculationSettingsWriteData(CalculationSettingsReadData):
    changed: StrictBool


class CalculationSettingsWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: CalculationSettingsWriteData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def no_query(request: Request):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Calculation settings accept no query selectors.")


def calculation_settings_key(
    request: Request,
    value: Annotated[str | None, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Optional shared Platform idempotency key."
    )] = None,
):
    if (
        len(request.headers.getlist("Idempotency-Key")) > 1
        or (value is not None and not value.strip())
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Calculation settings replay key is invalid.")
    return value


def get_calculation_settings_service(
    session=Depends(get_construction_scoped_session),
):
    return build_construction_calculation_settings_service(session=session)


def get_calculation_write_settings(key=Depends(calculation_settings_key)):
    if key is None:
        return None
    try:
        return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Calculation settings replay is not configured.") from None


def get_calculation_settings_workflow(
    settings=Depends(get_calculation_write_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_calculation_settings_workflow(
            session=session,
            idempotency_encryption_key=(
                None if settings is None else settings.encryption_key
            ))
    except (TypeError, ValueError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Calculation settings runtime is unavailable.") from None


def public_settings(state, *, actor):
    if not isinstance(state, State):
        raise ValueError("Calculation settings response type differs.")
    fields = asdict(state)
    Service._state(fields, tenant_id=actor.tenant_id)
    return CalculationSettingsReadData.model_validate(fields).model_dump(mode="json")


def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def operation_error(error):
    if isinstance(error, ApiIdempotencyKeyReusedError):
        return ApiHttpError(
            status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with a different request.")
    if isinstance(error, InProgress):
        return ApiHttpError(
            status_code=409, code="idempotency_in_progress",
            message="Calculation settings write is processing.")
    if isinstance(error, ConstructionRowVersionConflictError):
        return ApiHttpError(
            status_code=409, code="ROW_VERSION_CONFLICT",
            message="Calculation settings changed. Reload before saving.")
    if isinstance(error, Conflict):
        return ApiHttpError(
            status_code=409, code="CALCULATION_SETTINGS_EXISTS",
            message="Calculation settings already exist.")
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(
            status_code=403, code="forbidden",
            message="Organization calculation settings access is unavailable.")
    if isinstance(error, NotFound):
        return ApiHttpError(
            status_code=404, code="not_found",
            message="Calculation settings are unavailable.")
    if isinstance(error, Validation):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Calculation settings request is invalid.")
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Calculation settings operation failed.")


router = ConstructionAPIRouter(
    prefix="/construction/settings/calculation",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
        Depends(no_query),
    ],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: update target is unavailable"},
        409: {"description": "ROW_VERSION_CONFLICT, CALCULATION_SETTINGS_EXISTS, IDEMPOTENCY_KEY_REUSED or idempotency_in_progress"},
        422: {"description": "validation_error: exact values and a valid version required"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized operation failure"},
        503: {"description": "service_unavailable: keyed replay configuration unavailable"},
    },
)


@router.get(
    "", response_model=CalculationSettingsReadEnvelope,
    dependencies=[Depends(require_construction_permission("construction.settings.manage"))],
)
async def read_calculation_settings(
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_calculation_settings_service),
):
    try:
        state = await service.get_settings(actor=actor)
        data = None if state is None else public_settings(state, actor=actor)
    except Exception as error:
        raise operation_error(error) from None
    private_response(response)
    return success_envelope(data=data, request_id=request.state.request_id)


async def write_response(
    *, actor, body, workflow, key, request, response, create,
):
    try:
        fields = body.domain_fields()
        if create:
            result = await workflow.create_settings(
                actor=actor, fields=fields, idempotency_key=key,
                trace_id=request.state.request_id)
        else:
            result = await workflow.update_settings(
                actor=actor, expected_row_version=body.expected_row_version,
                fields=fields, idempotency_key=key,
                trace_id=request.state.request_id)
        if not isinstance(result, Result) or type(result.changed) is not bool:
            raise ValueError("Calculation write result differs.")
        state = result.settings
        data = public_settings(state, actor=actor)
        if any(getattr(state, name) != value for name, value in fields.items()):
            raise ValueError("Calculation write fields differ.")
        if create:
            if (
                state.created_by != actor.user_id
                or state.row_version != 1 or result.changed is not True
            ):
                raise ValueError("Calculation creation context differs.")
        elif state.row_version != body.expected_row_version + int(result.changed):
            raise ValueError("Calculation update version differs.")
        data = CalculationSettingsWriteData.model_validate(
            data | {"changed": result.changed}
        ).model_dump(mode="json")
    except Exception as error:
        raise operation_error(error) from None
    private_response(response)
    return success_envelope(data=data, request_id=request.state.request_id)


@router.post(
    "", status_code=201, response_model=CalculationSettingsWriteEnvelope,
    dependencies=[Depends(require_construction_permission("construction.settings.manage"))],
)
async def create_calculation_settings(
    body: CalculationSettingsCreateRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(calculation_settings_key),
    workflow=Depends(get_calculation_settings_workflow),
):
    return await write_response(
        actor=actor, body=body, workflow=workflow, key=key,
        request=request, response=response, create=True)


@router.patch(
    "", response_model=CalculationSettingsWriteEnvelope,
    dependencies=[Depends(require_construction_permission("construction.settings.manage"))],
)
async def patch_calculation_settings(
    body: CalculationSettingsPatchRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(calculation_settings_key),
    workflow=Depends(get_calculation_settings_workflow),
):
    return await write_response(
        actor=actor, body=body, workflow=workflow, key=key,
        request=request, response=response, create=False)
