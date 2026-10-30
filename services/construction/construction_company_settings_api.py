"""Typed client for the existing Construction company settings resource."""
import re
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from services.construction.construction_api_client import ConstructionApiClientError

TEXT_FIELDS = (
    "display_name_override", "phone_override", "email_override", "website_override",
    "document_header_text", "document_footer_text", "accent_color",
)
LOGO_FIELD = "logo_file_id_override"
FIELDS = TEXT_FIELDS + (LOGO_FIELD,)


class SettingsData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    display_name_override: str | None = Field(max_length=255)
    phone_override: str | None = Field(max_length=50)
    email_override: str | None = Field(max_length=320)
    website_override: str | None = Field(max_length=2048)
    document_header_text: str | None
    document_footer_text: str | None
    accent_color: str | None
    logo_file_id_override: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: int = Field(gt=0, strict=True)


def problem(status=502, code="invalid_api_response"):
    return ConstructionApiClientError(status_code=status, code=code)


class ConstructionCompanySettingsApi:
    def __init__(self, api):
        self.api = api

    @staticmethod
    def parse(result, *, session, nullable):
        try:
            if result.meta != {}:
                raise ValueError("Settings metadata differs.")
            if result.data is None and nullable:
                return None
            item = SettingsData.model_validate(result.data)
            if (
                item.tenant_id != session.tenant_id
                or item.accent_color is not None
                and re.fullmatch(r"#[0-9A-Fa-f]{6}", item.accent_color) is None
            ):
                raise ValueError("Settings context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return item.model_dump(mode="json")

    @staticmethod
    def validate_changes(changes):
        if (
            not isinstance(changes, dict) or not changes
            or not set(changes) <= set(FIELDS)
        ):
            raise ValueError("Unsupported Construction settings fields.")
        body = dict(changes)
        for field, value in body.items():
            if field == LOGO_FIELD:
                if value is not None:
                    if not isinstance(value, (str, UUID)):
                        raise ValueError("Logo must be a file UUID.")
                    try:
                        body[field] = str(UUID(str(value)))
                    except ValueError:
                        raise ValueError("Logo must be a file UUID.") from None
            elif value is not None and not isinstance(value, str):
                raise ValueError("Settings text must be text or null.")
        return body

    async def read(self, *, session):
        result = await self.api._request(
            "GET", "construction/settings/company",
            session=session, allow_null=True,
        )
        return self.parse(result, session=session, nullable=True)

    async def write(self, *, session, changes, expected_row_version):
        body = self.validate_changes(changes)
        if expected_row_version is None:
            method = "POST"
        else:
            if type(expected_row_version) is not int or expected_row_version <= 0:
                raise ValueError("Expected settings version differs.")
            body["expected_row_version"] = expected_row_version
            method = "PATCH"
        result = await self.api._request(
            method, "construction/settings/company",
            session=session, json_body=body,
        )
        return self.parse(result, session=session, nullable=False)
