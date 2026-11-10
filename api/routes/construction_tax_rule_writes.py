"""CON-016 tax-rule writes through the shared atomic workflow."""
from dataclasses import asdict
from decimal import Decimal, DecimalException
import re
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
from api.routes.construction_tax_rules import TaxRuleReadData
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleResult, ConstructionTaxRuleState, ConstructionTaxRuleValidationError,
    ConstructionTaxRuleNotFoundError, ConstructionTaxRuleConflictError, validated_fields,
)
from services.construction.construction_tax_rule_workflow import (
    ConstructionTaxRuleInProgressError, build_construction_tax_rule_workflow,
)

Category = Literal["WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER"]
RATE_PATTERN = r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
RATE_DESCRIPTION = "Exact decimal string from 0 to 100; JSON numbers and floats are rejected."


def rate_input(value):
    if re.fullmatch(RATE_PATTERN, value) is None:
        raise ValueError("An exact decimal rate string is required.")
    try:
        validated_fields({"rate_percent": Decimal(value)})
    except (DecimalException, ConstructionTaxRuleValidationError):
        raise ValueError("Tax rate must be finite and between 0 and 100.") from None
    return value


def domain_fields(values):
    fields = dict(values)
    if "rate_percent" in fields:
        fields["rate_percent"] = Decimal(fields["rate_percent"])
    return validated_fields(fields)


class TaxRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    rate_percent: StrictStr = Field(pattern=RATE_PATTERN, description=RATE_DESCRIPTION)

    @field_validator("rate_percent")
    @classmethod
    def exact_rate(cls, value):
        return rate_input(value)

    def domain_fields(self):
        return domain_fields(self.model_dump())


def sparse_patch_schema(schema):
    for name in ("category", "rate_percent"):
        field = schema["properties"][name]
        variants = [part for part in field.get("anyOf", []) if part.get("type") != "null"]
        if len(variants) != 1:
            raise ValueError("Tax rule PATCH must expose a non-null optional field.")
        metadata = {key: value for key, value in field.items() if key not in ("anyOf", "default")}
        field.clear()
        field.update(variants[0])
        field.update(metadata)


class TaxRulePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=sparse_patch_schema)
    expected_row_version: SettingsVersion
    category: Category | None = None
    rate_percent: StrictStr | None = Field(default=None, pattern=RATE_PATTERN, description=RATE_DESCRIPTION)

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, values):
        if isinstance(values, dict) and any(
            name in values and values[name] is None for name in ("category", "rate_percent")
        ):
            raise ValueError("Tax rule fields cannot be cleared.")
        return values

    @field_validator("rate_percent")
    @classmethod
    def exact_rate(cls, value):
        return rate_input(value)

    def domain_fields(self):
        return domain_fields(self.model_dump(exclude_unset=True, exclude={"expected_row_version"}))


class TaxRuleWriteData(TaxRuleReadData):
    changed: StrictBool


class TaxRuleWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: TaxRuleWriteData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def require_tax_rule_key(
    request: Request,
    value: Annotated[str, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Required. Same normalized request replays its original result within shared Platform retention; changed request returns IDEMPOTENCY_KEY_REUSED.",
    )],
):
    if request.query_params or len(request.headers.getlist("Idempotency-Key")) != 1 or not value.strip():
        raise ApiHttpError(status_code=422, code="validation_error", message="Tax rule write request is invalid.")
    return value


def get_tax_rule_write_settings(_key=Depends(require_tax_rule_key)):
    try:
        return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(
            status_code=503, code="service_unavailable", message="Tax rule replay is not configured.",
        ) from None


def get_tax_rule_write_workflow(
    settings=Depends(get_tax_rule_write_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_tax_rule_workflow(
            session=session, idempotency_encryption_key=settings.encryption_key,
        )
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable", message="Tax rule write runtime is unavailable.",
        ) from None


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
        404: {"description": "not_found: profile or rule unavailable"},
        409: {"description": "ROW_VERSION_CONFLICT, TAX_RULE_CATEGORY_EXISTS, IDEMPOTENCY_KEY_REUSED or idempotency_in_progress"},
        422: {"description": "validation_error; explicit category/rate, valid key and version required"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error; sanitized operation failure"},
        503: {"description": "service_unavailable; replay configuration unavailable"},
    },
)
edit_permission = require_construction_permission("construction.tax.edit")


async def write_response(*, actor, profile_id, body, workflow, key, request, response, rule_id=None):
    try:
        fields = body.domain_fields()
        if rule_id is None:
            result = await workflow.create_rule(
                actor=actor, profile_id=profile_id, **fields,
                idempotency_key=key, trace_id=request.state.request_id,
            )
        else:
            result = await workflow.update_rule(
                actor=actor, profile_id=profile_id, rule_id=rule_id,
                expected_row_version=body.expected_row_version, fields=fields,
                idempotency_key=key, trace_id=request.state.request_id,
            )
        if (not isinstance(result, ConstructionTaxRuleResult) or type(result.changed) is not bool
                or not isinstance(result.rule, ConstructionTaxRuleState)):
            raise ValueError("Tax rule result type differs.")
        state = result.rule
        if (state.tenant_id != actor.tenant_id or state.tax_profile_id != profile_id
                or not isinstance(state.id, UUID) or not isinstance(state.rate_percent, Decimal)
                or any(getattr(state, name) != value for name, value in fields.items())):
            raise ValueError("Tax rule result context differs.")
        if rule_id is None:
            if state.created_by != actor.user_id or state.row_version != 1 or result.changed is not True:
                raise ValueError("Tax rule create result differs.")
        elif state.id != rule_id or state.row_version != body.expected_row_version + int(result.changed):
            raise ValueError("Tax rule update result differs.")
        data = TaxRuleWriteData.model_validate(asdict(state) | {"changed": result.changed}).model_dump(mode="json")
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(status_code=409, code="IDEMPOTENCY_KEY_REUSED",
                           message="Idempotency key was reused with a different request.") from None
    except ConstructionTaxRuleInProgressError:
        raise ApiHttpError(status_code=409, code="idempotency_in_progress",
                           message="Tax rule write is processing.") from None
    except ConstructionTaxRuleConflictError:
        raise ApiHttpError(
            status_code=409, code="TAX_RULE_CATEGORY_EXISTS",
            message="A rule for this tax category already exists in the selected profile.",
        ) from None
    except ConstructionRowVersionConflictError:
        raise ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT",
                           message="Tax rule changed. Reload before saving.") from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(status_code=403, code="forbidden",
                           message="Organization tax rule editing is unavailable.") from None
    except ConstructionTaxRuleNotFoundError:
        raise ApiHttpError(status_code=404, code="not_found", message="Tax profile or rule is unavailable.") from None
    except ConstructionTaxRuleValidationError:
        raise ApiHttpError(status_code=422, code="validation_error", message="Tax rule request is invalid.") from None
    except Exception:
        raise ApiHttpError(status_code=500, code="internal_error", message="Tax rule write failed.") from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(data=data, request_id=request.state.request_id)


@router.post("/{profile_id}/rules", status_code=201, response_model=TaxRuleWriteEnvelope,
             dependencies=[Depends(edit_permission)])
async def create_tax_rule(
    profile_id: UUID, body: TaxRuleCreateRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(require_tax_rule_key),
    workflow=Depends(get_tax_rule_write_workflow),
):
    return await write_response(actor=actor, profile_id=profile_id, body=body, workflow=workflow,
                                key=key, request=request, response=response)


@router.patch("/{profile_id}/rules/{rule_id}", response_model=TaxRuleWriteEnvelope,
              dependencies=[Depends(edit_permission)])
async def patch_tax_rule(
    profile_id: UUID, rule_id: UUID, body: TaxRulePatchRequest, request: Request, response: Response,
    actor=Depends(get_current_actor), key=Depends(require_tax_rule_key),
    workflow=Depends(get_tax_rule_write_workflow),
):
    return await write_response(actor=actor, profile_id=profile_id, rule_id=rule_id, body=body,
                                workflow=workflow, key=key, request=request, response=response)

# con016_canonical_categories_unique_v1
