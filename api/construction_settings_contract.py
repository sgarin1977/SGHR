"""Typed CON-010 base settings API; existing storage and permissions."""
from datetime import timezone
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr,
    field_validator, model_validator,
)
from services.construction.construction_tenant_settings import (
    CONSTRUCTION_TEXT_OVERRIDE_LIMITS as LIMITS,
    normalize_construction_text_overrides,
)

SettingsVersion = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]
_TEXT_FIELDS = tuple(LIMITS)


class ConstructionCompanySettingsCreate(BaseModel):
    """Nullable presentation overrides; null restores inheritance."""
    model_config = ConfigDict(extra="forbid")
    display_name_override: StrictStr | None = Field(default=None, max_length=LIMITS["display_name_override"])
    phone_override: StrictStr | None = Field(default=None, max_length=LIMITS["phone_override"])
    email_override: StrictStr | None = Field(default=None, max_length=LIMITS["email_override"])
    website_override: StrictStr | None = Field(default=None, max_length=LIMITS["website_override"])
    logo_file_id_override: UUID | None = None
    document_header_text: StrictStr | None = None
    document_footer_text: StrictStr | None = None
    accent_color: StrictStr | None = Field(default=None, max_length=LIMITS["accent_color"])

    @field_validator(*_TEXT_FIELDS, mode="before")
    @classmethod
    def canonical_text(cls, value, info):
        return normalize_construction_text_overrides(
            **{info.field_name: value}
        )[info.field_name]


class ConstructionCompanySettingsUpdate(ConstructionCompanySettingsCreate):
    """Sparse PATCH: omitted fields stay unchanged; a version is required."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 2})
    expected_row_version: SettingsVersion

    @model_validator(mode="after")
    def explicit_change(self):
        if not self.model_fields_set.difference({"expected_row_version"}):
            raise ValueError("At least one settings field is required.")
        return self


class ConstructionCompanySettingsData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    display_name_override: StrictStr | None = Field(max_length=LIMITS["display_name_override"])
    phone_override: StrictStr | None = Field(max_length=LIMITS["phone_override"])
    email_override: StrictStr | None = Field(max_length=LIMITS["email_override"])
    website_override: StrictStr | None = Field(max_length=LIMITS["website_override"])
    logo_file_id_override: UUID | None
    document_header_text: StrictStr | None
    document_footer_text: StrictStr | None
    accent_color: StrictStr | None = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: SettingsVersion

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)


class ConstructionCompanySettingsEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ConstructionCompanySettingsData | None
    meta: dict
    request_id: StrictStr = Field(min_length=1)


class ConstructionCompanySettingsWriteEnvelope(ConstructionCompanySettingsEnvelope):
    data: ConstructionCompanySettingsData

def install_settings_openapi_precision(application):
    """Keep settings BIGINT bounds exact after FastAPI's float schema model."""
    state = application.state
    if getattr(state, "_con010_openapi_precision_installed", False):
        return
    if getattr(application.openapi, "_con010_exact_integer_bounds", False):
        state._con010_openapi_precision_installed = True
        return
    from pydantic import TypeAdapter

    declared = TypeAdapter(SettingsVersion).json_schema()
    bounds = {key: declared[key] for key in ("minimum", "maximum")}
    if any(type(value) is not int for value in bounds.values()):
        raise ValueError("Settings version bounds must be exact integers.")
    targets = {
        ConstructionCompanySettingsUpdate.__name__: "expected_row_version",
        ConstructionCompanySettingsData.__name__: "row_version",
        "LanguageContextResponse": "row_version",
        "DocumentLanguagePlanData": "organization_row_version",
        "OrganizationLanguageUpdate": "expected_row_version",
        "OrganizationLanguageWriteData": "row_version",
        "CurrencySettingsReadData": "row_version",
        "CurrencySettingsWriteData": "row_version",
        "CurrencySettingsUpdate": "expected_row_version",
        "RegionSettingsUpdate": "expected_row_version",
        "RegionSettingsReadData": "row_version",
        "RegionSettingsWriteData": "row_version",
        "TaxProfileReadData": "row_version",
        "TaxProfileWriteData": "row_version",
        "TaxProfilePatchRequest": "expected_row_version",
        "TaxRuleReadData": "row_version",
        "TaxRuleWriteData": "row_version",
        "TaxRulePatchRequest": "expected_row_version",
        "PricingProfileReadData": "row_version",
        "PricingProfileWriteData": "row_version",
        "PricingProfilePatchRequest": "expected_row_version",
        "CalculationSettingsReadData": "row_version",
        "CalculationSettingsWriteData": "row_version",
        "CalculationSettingsPatchRequest": "expected_row_version",
        "OnboardingProgressData": "row_version",
        "OnboardingProgressWriteData": "row_version",
        "OnboardingStartRequest": "expected_row_version",
        "OnboardingVersionRequest": "expected_row_version",
        "OnboardingTaxSelectionData": "row_version",
        "OnboardingTaxSelectionWriteData": "row_version",
        "OnboardingTaxSelectionUpdate": "expected_row_version",
        "ProjectPricingAdjustmentReadData": "row_version",
        "ProjectPricingAdjustmentWriteData": "row_version",
        "ProjectPricingAdjustmentPatchRequest": "expected_row_version",
    }
    previous_openapi = application.openapi

    def precise_openapi():
        # Preserve FastAPI's own cache and route-version invalidation.
        document = previous_openapi()
        schemas = document.get("components", {}).get("schemas", {})
        for schema in schemas.values():
            if not isinstance(schema, dict):
                continue
            field_name = targets.get(schema.get("title"))
            if field_name is None:
                continue
            field = schema.get("properties", {}).get(field_name)
            if not isinstance(field, dict):
                raise ValueError("Settings version OpenAPI field is invalid.")
            if field.get("type") != "integer":
                if schema.get("title") not in (
                    "OnboardingProgressData", "OnboardingStartRequest",
                ):
                    raise ValueError("Settings version OpenAPI field is invalid.")
                variants = field.get("anyOf")
                if (not isinstance(variants, list) or len(variants) != 2
                        or any(not isinstance(part, dict) for part in variants)
                        or {part.get("type") for part in variants} != {"integer", "null"}):
                    raise ValueError("Nullable settings version OpenAPI field is invalid.")
                field = next(part for part in variants if part.get("type") == "integer")
            field.update(bounds)
            field["format"] = "int64"
        return document

    precise_openapi._con010_exact_integer_bounds = True
    application.openapi = precise_openapi
    state._con010_openapi_precision_installed = True

# con_010_openapi_bigint_precision_v1

# con_011_row_version_v1

# con_012_currency_settings_v1
