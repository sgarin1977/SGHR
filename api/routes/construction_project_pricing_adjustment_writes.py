"""CON-018 explicit project adjustments through the shared atomic workflow."""
import re
from decimal import Decimal, DecimalException
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission
from api.routes.construction_project_pricing_adjustments import (
    ProjectPricingAdjustmentReadData, public_adjustment, private_response)
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_project_pricing_adjustments import (
    ConstructionProjectPricingAdjustmentResult as Result,
    ConstructionProjectPricingAdjustmentValidationError as ValidationError,
    ConstructionProjectPricingAdjustmentNotFoundError as NotFoundError,
    EDITABLE_FIELDS, validated_fields)
from services.construction.construction_project_pricing_adjustment_workflow import (
    ConstructionProjectPricingAdjustmentInProgressError,
    build_construction_project_pricing_adjustment_workflow)

DECIMAL_PATTERN = r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"


def exact_coefficient(value):
    if re.fullmatch(DECIMAL_PATTERN, value) is None:
        raise ValueError("An exact decimal string is required.")
    try:
        validated_fields({"coefficient": Decimal(value)})
    except (DecimalException, ValidationError):
        raise ValueError("A finite positive coefficient is required.") from None
    return value


def domain_fields(values, *, complete=False):
    result = dict(values)
    if "coefficient" in result:
        result["coefficient"] = Decimal(result["coefficient"])
    return validated_fields(result, complete=complete)


class ProjectPricingAdjustmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=255)
    coefficient: StrictStr = Field(pattern=DECIMAL_PATTERN, description="Exact positive decimal string; no default coefficient.")
    enabled: StrictBool

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value):
        try:
            return validated_fields({"name": value})["name"]
        except ValidationError:
            raise ValueError("Adjustment name is invalid.") from None

    @field_validator("coefficient")
    @classmethod
    def exact_value(cls, value):
        return exact_coefficient(value)

    def domain_fields(self):
        return domain_fields(self.model_dump(), complete=True)


def sparse_patch_schema(schema):
    for name in EDITABLE_FIELDS:
        field = schema["properties"][name]
        variants = [part for part in field.get("anyOf", []) if part.get("type") != "null"]
        if len(variants) != 1:
            raise ValueError("Adjustment PATCH must expose non-null optional fields.")
        metadata = {key: value for key, value in field.items() if key not in ("anyOf", "default")}
        field.clear()
        field.update(variants[0])
        field.update(metadata)


class ProjectPricingAdjustmentPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=sparse_patch_schema)
    expected_row_version: SettingsVersion
    name: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    coefficient: StrictStr | None = Field(default=None, pattern=DECIMAL_PATTERN)
    enabled: StrictBool | None = None

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, values):
        if isinstance(values, dict) and any(name in values and values[name] is None for name in EDITABLE_FIELDS):
            raise ValueError("Adjustment fields cannot be cleared.")
        return values

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value):
        try:
            return validated_fields({"name": value})["name"]
        except ValidationError:
            raise ValueError("Adjustment name is invalid.") from None

    @field_validator("coefficient")
    @classmethod
    def exact_value(cls, value):
        return exact_coefficient(value)

    def domain_fields(self):
        return domain_fields(self.model_dump(exclude_unset=True, exclude={"expected_row_version"}))


class ProjectPricingAdjustmentWriteData(ProjectPricingAdjustmentReadData):
    changed: StrictBool


class ProjectPricingAdjustmentWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ProjectPricingAdjustmentWriteData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def adjustment_key(
    request: Request,
    value: Annotated[str | None, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Optional shared Platform replay key; changed normalized payload with an active key returns IDEMPOTENCY_KEY_REUSED."
    )] = None,
):
    if request.query_params or len(request.headers.getlist("Idempotency-Key")) > 1 or (value is not None and not value.strip()):
        raise ApiHttpError(status_code=422, code="validation_error", message="Adjustment write request is invalid.")
    return value


def get_adjustment_write_settings(key=Depends(adjustment_key)):
    if key is None:
        return None
    try:
        return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(status_code=503, code="service_unavailable",
                           message="Adjustment replay is not configured.") from None


def get_adjustment_write_workflow(
    settings=Depends(get_adjustment_write_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_project_pricing_adjustment_workflow(
            session=session, idempotency_encryption_key=None if settings is None else settings.encryption_key)
    except (TypeError, ValueError):
        raise ApiHttpError(status_code=503, code="service_unavailable",
                           message="Adjustment write runtime is unavailable.") from None


router = ConstructionAPIRouter(
    prefix="/construction/projects/{project_id}/pricing-adjustments",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden; current project pricing.edit required"},
        404: {"description": "not_found: project or adjustment unavailable in the current scope"},
        409: {"description": "ROW_VERSION_CONFLICT, conflict for terminal project, IDEMPOTENCY_KEY_REUSED or idempotency_in_progress"},
        422: {"description": "validation_error: explicit exact values and positive BIGINT version required"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized operation failure"},
        503: {"description": "service_unavailable: keyed replay configuration unavailable"},
    })
edit_permission = require_construction_permission("construction.pricing.edit")


async def write_response(*, actor, project_id, body, workflow, key, request, response, adjustment_id=None):
    try:
        fields = body.domain_fields()
        if adjustment_id is None:
            result = await workflow.create_adjustment(
                actor=actor, project_id=project_id, **fields, idempotency_key=key, trace_id=request.state.request_id)
        else:
            result = await workflow.update_adjustment(
                actor=actor, project_id=project_id, adjustment_id=adjustment_id,
                expected_row_version=body.expected_row_version, fields=fields,
                idempotency_key=key, trace_id=request.state.request_id)
        if not isinstance(result, Result) or type(result.changed) is not bool:
            raise ValueError("Adjustment result type differs.")
        state = result.adjustment
        projected = public_adjustment(state, actor=actor, project_id=project_id, adjustment_id=adjustment_id)
        if any(getattr(state, name) != value for name, value in fields.items()):
            raise ValueError("Adjustment result fields differ.")
        if adjustment_id is None:
            if state.created_by != actor.user_id or state.row_version != 1 or result.changed is not True:
                raise ValueError("Adjustment creation result differs.")
        elif state.row_version != body.expected_row_version + int(result.changed):
            raise ValueError("Adjustment update version differs.")
        data = ProjectPricingAdjustmentWriteData.model_validate(
            projected | {"changed": result.changed}).model_dump(mode="json")
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code="IDEMPOTENCY_KEY_REUSED",
                           message="Idempotency key was reused with a different request.") from None
    except ConstructionProjectPricingAdjustmentInProgressError:
        raise ApiHttpError(status_code=409, code="idempotency_in_progress", message="Adjustment write is processing.") from None
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT",
                           message="Pricing adjustment changed. Reload before saving.") from None
    except ConstructionProjectTerminalError:
        raise ApiHttpError(status_code=409, code="conflict", message="A terminal project cannot be changed.") from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden", message="Project pricing editing is unavailable.") from None
    except NotFoundError:
        raise ApiHttpError(status_code=404, code="not_found", message="Project or pricing adjustment is unavailable.") from None
    except ValidationError:
        raise ApiHttpError(status_code=422, code="validation_error", message="Adjustment write request is invalid.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error", message="Pricing adjustment write failed.") from None
    private_response(response)
    return success_envelope(data=data, request_id=request.state.request_id)


@router.post("", status_code=201, response_model=ProjectPricingAdjustmentWriteEnvelope,
             dependencies=[Depends(edit_permission)])
async def create_project_pricing_adjustment(
    project_id: UUID, body: ProjectPricingAdjustmentCreateRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(adjustment_key),
    workflow=Depends(get_adjustment_write_workflow),
):
    return await write_response(actor=actor, project_id=project_id, body=body,
                                workflow=workflow, key=key, request=request, response=response)


@router.patch("/{adjustment_id}", response_model=ProjectPricingAdjustmentWriteEnvelope,
              dependencies=[Depends(edit_permission)])
async def patch_project_pricing_adjustment(
    project_id: UUID, adjustment_id: UUID, body: ProjectPricingAdjustmentPatchRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(adjustment_key),
    workflow=Depends(get_adjustment_write_workflow),
):
    return await write_response(actor=actor, project_id=project_id, adjustment_id=adjustment_id,
                                body=body, workflow=workflow, key=key, request=request, response=response)
