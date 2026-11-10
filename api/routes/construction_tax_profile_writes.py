"""CON-015 writes through the shared atomic tax workflow; no tax rates/default assignments."""
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_tax_profiles import TaxProfileReadData
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileResult, ConstructionTaxProfileState, ConstructionTaxProfileValidationError,
    ConstructionTaxProfileNotFoundError, validated_fields,
)
from services.construction.construction_tax_profile_workflow import (
    ConstructionTaxProfileInProgressError, build_construction_tax_profile_workflow,
)

ClientMode = Literal["TAX_EXCLUSIVE", "TAX_INCLUSIVE"]
MaterialBasis = Literal["NET", "GROSS"]


class TaxProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=255)
    client_price_tax_mode: ClientMode
    material_cost_basis: MaterialBasis

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value):
        return validated_fields({"name": value})["name"]


def sparse_patch_schema(schema):
    # Omission preserves a field; explicit null is rejected at runtime.
    for name in ("name", "client_price_tax_mode", "material_cost_basis"):
        field = schema["properties"][name]
        variants = [part for part in field.get("anyOf", []) if part.get("type") != "null"]
        if len(variants) != 1:
            raise ValueError("Tax PATCH schema must expose a non-null optional field.")
        title = field.get("title")
        field.clear()
        field.update(variants[0])
        if title is not None:
            field["title"] = title


class TaxProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=sparse_patch_schema)
    expected_row_version: SettingsVersion
    name: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    client_price_tax_mode: ClientMode | None = None
    material_cost_basis: MaterialBasis | None = None

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, values):
        if isinstance(values, dict) and any(
            name in values and values[name] is None
            for name in ("name", "client_price_tax_mode", "material_cost_basis")
        ):
            raise ValueError("Tax profile fields cannot be cleared.")
        return values

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value):
        return validated_fields({"name": value})["name"]

    def patch_fields(self):
        return self.model_dump(exclude_unset=True, exclude={"expected_row_version"})


class TaxProfileWriteData(TaxProfileReadData):
    changed: StrictBool


class TaxProfileWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: TaxProfileWriteData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def require_tax_profile_key(
    request: Request,
    value: Annotated[str, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Required. Within the shared Platform retention window, the same key and normalized request replay the original result; changed request returns IDEMPOTENCY_KEY_REUSED.",
    )],
):
    if request.query_params or len(request.headers.getlist("Idempotency-Key")) != 1 or not value.strip():
        raise ApiHttpError(status_code=422, code="validation_error", message="Tax profile write request is invalid.")
    return value


def get_tax_profile_write_settings(_key=Depends(require_tax_profile_key)):
    try:
        return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(status_code=503, code="service_unavailable",
                           message="Tax profile replay is not configured.") from None


def get_tax_profile_write_workflow(
    settings=Depends(get_tax_profile_write_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_tax_profile_workflow(
            session=session, idempotency_encryption_key=settings.encryption_key)
    except (ValueError, TypeError):
        raise ApiHttpError(status_code=503, code="service_unavailable",
                           message="Tax profile write runtime is unavailable.") from None


router = ConstructionAPIRouter(
    prefix="/construction/settings/tax-profiles",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden; current tenant tax.edit required"},
        404: {"description": "not_found"},
        409: {"description": "ROW_VERSION_CONFLICT, IDEMPOTENCY_KEY_REUSED or idempotency_in_progress"},
        422: {"description": "validation_error; explicit tax modes, valid key and version required"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error; sanitized operation failure"},
        503: {"description": "service_unavailable; replay configuration unavailable"},
    },
)
edit_permission = require_construction_permission("construction.tax.edit")


async def write_response(*, actor, body, workflow, key, request, response, profile_id=None):
    try:
        fields = body.model_dump() if profile_id is None else body.patch_fields()
        if profile_id is None:
            result = await workflow.create_profile(
                actor=actor, **fields, idempotency_key=key, trace_id=request.state.request_id)
        else:
            result = await workflow.update_profile(
                actor=actor, profile_id=profile_id, expected_row_version=body.expected_row_version,
                fields=fields, idempotency_key=key, trace_id=request.state.request_id)
        if (not isinstance(result, ConstructionTaxProfileResult) or type(result.changed) is not bool
                or not isinstance(result.profile, ConstructionTaxProfileState)
                or result.profile.tenant_id != actor.tenant_id):
            raise ValueError("Tax profile result context differs.")
        state = result.profile
        if any(getattr(state, name) != value for name, value in fields.items()):
            raise ValueError("Tax profile result fields differ.")
        if profile_id is None:
            if state.created_by != actor.user_id or state.row_version != 1 or result.changed is not True:
                raise ValueError("Tax profile create result differs.")
        elif state.id != profile_id or state.row_version != body.expected_row_version + int(result.changed):
            raise ValueError("Tax profile update result differs.")
        data = TaxProfileWriteData.model_validate(asdict(state) | {"changed": result.changed}).model_dump(mode="json")
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code="IDEMPOTENCY_KEY_REUSED",
                           message="Idempotency key was reused with a different request.") from None
    except ConstructionTaxProfileInProgressError:
        raise ApiHttpError(status_code=409, code="idempotency_in_progress",
                           message="Tax profile write is processing.") from None
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT",
                           message="Tax profile changed. Reload before saving.") from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden",
                           message="Organization tax profile editing is unavailable.") from None
    except ConstructionTaxProfileNotFoundError:
        raise ApiHttpError(status_code=404, code="not_found", message="Tax profile is unavailable.") from None
    except ConstructionTaxProfileValidationError:
        raise ApiHttpError(status_code=422, code="validation_error", message="Tax profile request is invalid.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error", message="Tax profile write failed.") from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(data=data, request_id=request.state.request_id)


@router.post("", status_code=201, response_model=TaxProfileWriteEnvelope,
             dependencies=[Depends(edit_permission)])
async def create_tax_profile(
    body: TaxProfileCreateRequest, request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_tax_profile_key),
    workflow=Depends(get_tax_profile_write_workflow),
):
    return await write_response(actor=actor, body=body, workflow=workflow, key=key, request=request, response=response)


@router.patch("/{profile_id}", response_model=TaxProfileWriteEnvelope,
              dependencies=[Depends(edit_permission)])
async def patch_tax_profile(
    profile_id: UUID, body: TaxProfilePatchRequest, request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_tax_profile_key),
    workflow=Depends(get_tax_profile_write_workflow),
):
    return await write_response(actor=actor, body=body, workflow=workflow, key=key, request=request,
                                response=response, profile_id=profile_id)
