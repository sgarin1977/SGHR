"""CON-017 explicit pricing writes through the shared atomic workflow."""
import re
from decimal import Decimal, DecimalException
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission)
from api.routes.construction_pricing_profiles import PricingProfileReadData, public_profile, private_response
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_pricing_profiles import (
    ConstructionPricingProfileResult, ConstructionPricingProfileValidationError,
    ConstructionPricingProfileNotFoundError, ConstructionPricingProfileConflictError,
    EDITABLE_FIELDS, validated_fields)
from services.construction.construction_pricing_profile_workflow import (
    ConstructionPricingProfileInProgressError, build_construction_pricing_profile_workflow)

DECIMAL_PATTERN = r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"


def exact_input(name, value):
    if re.fullmatch(DECIMAL_PATTERN, value) is None:
        raise ValueError("An exact decimal string is required.")
    try:
        validated_fields({name: Decimal(value)})
    except (DecimalException, ConstructionPricingProfileValidationError):
        raise ValueError("Pricing value must be finite and within its bounds.") from None
    return value


def domain_fields(values, *, complete=False):
    result = dict(values)
    for name in EDITABLE_FIELDS:
        if name in result:
            result[name] = Decimal(result[name])
    return validated_fields(result, complete=complete)


class PricingProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["ECONOMY", "STANDARD", "PREMIUM"]
    work_coefficient: StrictStr = Field(pattern=DECIMAL_PATTERN, description="Exact decimal string greater than zero.")
    material_markup_percent: StrictStr = Field(pattern=DECIMAL_PATTERN, description="Exact decimal string from 0 to 100; COST_PLUS only.")

    @field_validator("work_coefficient", "material_markup_percent")
    @classmethod
    def exact_values(cls, value, info):
        return exact_input(info.field_name, value)

    def domain_fields(self):
        return domain_fields(self.model_dump(), complete=True)


def sparse_patch_schema(schema):
    for name in EDITABLE_FIELDS:
        field = schema["properties"][name]
        variants = [part for part in field.get("anyOf", []) if part.get("type") != "null"]
        if len(variants) != 1:
            raise ValueError("Pricing PATCH must expose a non-null optional field.")
        metadata = {key: value for key, value in field.items() if key not in ("anyOf", "default")}
        field.clear()
        field.update(variants[0])
        field.update(metadata)


class PricingProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=sparse_patch_schema)
    expected_row_version: SettingsVersion
    work_coefficient: StrictStr | None = Field(default=None, pattern=DECIMAL_PATTERN)
    material_markup_percent: StrictStr | None = Field(default=None, pattern=DECIMAL_PATTERN)

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, values):
        if isinstance(values, dict) and any(name in values and values[name] is None for name in EDITABLE_FIELDS):
            raise ValueError("Commercial pricing values cannot be cleared.")
        return values

    @field_validator("work_coefficient", "material_markup_percent")
    @classmethod
    def exact_values(cls, value, info):
        return exact_input(info.field_name, value)

    def domain_fields(self):
        return domain_fields(self.model_dump(exclude_unset=True, exclude={"expected_row_version"}))


class PricingProfileWriteData(PricingProfileReadData):
    changed: StrictBool


class PricingProfileWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: PricingProfileWriteData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def pricing_profile_key(
    request: Request,
    value: Annotated[str | None, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Optional shared Platform replay key. Changed normalized payload with the same active key returns IDEMPOTENCY_KEY_REUSED."
    )] = None,
):
    if request.query_params or len(request.headers.getlist("Idempotency-Key")) > 1 or (value is not None and not value.strip()):
        raise ApiHttpError(status_code=422, code="validation_error", message="Pricing write request is invalid.")
    return value


def get_pricing_profile_write_settings(key=Depends(pricing_profile_key)):
    if key is None:
        return None
    try:
        return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(status_code=503, code="service_unavailable",
                           message="Pricing replay is not configured.") from None


def get_pricing_profile_write_workflow(
    settings=Depends(get_pricing_profile_write_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_pricing_profile_workflow(
            session=session, idempotency_encryption_key=None if settings is None else settings.encryption_key)
    except (TypeError, ValueError):
        raise ApiHttpError(status_code=503, code="service_unavailable",
                           message="Pricing write runtime is unavailable.") from None


router = ConstructionAPIRouter(
    prefix="/construction/settings/pricing-profiles",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden; current organization pricing.edit required"},
        404: {"description": "not_found: profile unavailable in the current organization"},
        409: {"description": "ROW_VERSION_CONFLICT, PRICING_PROFILE_EXISTS, IDEMPOTENCY_KEY_REUSED or idempotency_in_progress"},
        422: {"description": "validation_error: exact explicit values and valid version required"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized operation failure"},
        503: {"description": "service_unavailable: keyed replay configuration unavailable"},
    })
edit_permission = require_construction_permission("construction.pricing.edit")


async def write_response(*, actor, body, workflow, key, request, response, profile_id=None):
    try:
        fields = body.domain_fields()
        if profile_id is None:
            result = await workflow.create_profile(
                actor=actor, **fields, idempotency_key=key, trace_id=request.state.request_id)
        else:
            result = await workflow.update_profile(
                actor=actor, profile_id=profile_id, expected_row_version=body.expected_row_version,
                fields=fields, idempotency_key=key, trace_id=request.state.request_id)
        if not isinstance(result, ConstructionPricingProfileResult) or type(result.changed) is not bool:
            raise ValueError("Pricing result type differs.")
        state = result.profile
        projected = public_profile(state, actor=actor, profile_id=profile_id)
        if any(getattr(state, name) != value for name, value in fields.items()):
            raise ValueError("Pricing result fields differ.")
        if profile_id is None:
            if state.created_by != actor.user_id or state.row_version != 1 or result.changed is not True:
                raise ValueError("Pricing creation result differs.")
        elif state.row_version != body.expected_row_version + int(result.changed):
            raise ValueError("Pricing update version differs.")
        data = PricingProfileWriteData.model_validate(projected | {"changed": result.changed}).model_dump(mode="json")
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code="IDEMPOTENCY_KEY_REUSED",
                           message="Idempotency key was reused with a different request.") from None
    except ConstructionPricingProfileInProgressError:
        raise ApiHttpError(status_code=409, code="idempotency_in_progress", message="Pricing write is processing.") from None
    except ConstructionPricingProfileConflictError:
        raise ApiHttpError(status_code=409, code="PRICING_PROFILE_EXISTS",
                           message="This organization price level already exists.") from None
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT",
                           message="Pricing profile changed. Reload before saving.") from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden", message="Organization pricing editing is unavailable.") from None
    except ConstructionPricingProfileNotFoundError:
        raise ApiHttpError(status_code=404, code="not_found", message="Pricing profile is unavailable.") from None
    except ConstructionPricingProfileValidationError:
        raise ApiHttpError(status_code=422, code="validation_error", message="Pricing request is invalid.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error", message="Pricing profile write failed.") from None
    private_response(response)
    return success_envelope(data=data, request_id=request.state.request_id)


@router.post("", status_code=201, response_model=PricingProfileWriteEnvelope,
             dependencies=[Depends(edit_permission)])
async def create_pricing_profile(
    body: PricingProfileCreateRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(pricing_profile_key),
    workflow=Depends(get_pricing_profile_write_workflow),
):
    return await write_response(actor=actor, body=body, workflow=workflow, key=key, request=request, response=response)


@router.patch("/{profile_id}", response_model=PricingProfileWriteEnvelope,
              dependencies=[Depends(edit_permission)])
async def patch_pricing_profile(
    profile_id: UUID, body: PricingProfilePatchRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(pricing_profile_key),
    workflow=Depends(get_pricing_profile_write_workflow),
):
    return await write_response(actor=actor, profile_id=profile_id, body=body, workflow=workflow,
                                key=key, request=request, response=response)
