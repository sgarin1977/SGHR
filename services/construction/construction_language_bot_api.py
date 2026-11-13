"""Telegram language settings through existing authenticated HTTP APIs."""
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field,
    StrictBool, StrictStr,
)

from services.construction.construction_api_client import (
    ConstructionApiClientError, PlatformApiClientSession,
)

Version = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
Code = Annotated[StrictStr, Field(min_length=1, max_length=10)]


def problem(status=502, code="invalid_api_response"):
    return ConstructionApiClientError(status_code=status, code=code)


class LanguageOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Code
    name: StrictStr = Field(min_length=1)
    native_name: StrictStr | None
    label: StrictStr = Field(min_length=1)
    enabled_for_organization: StrictBool


class LanguageState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    user_id: UUID
    interface_language: Code
    organization_default_language: Code
    organization_updated_at: AwareDatetime
    row_version: Version
    languages: list[LanguageOption]


class LanguageSaved(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    organization_default_language: Code
    organization_updated_at: AwareDatetime
    row_version: Version
    changed: StrictBool


class ConstructionLanguageBotApi:
    """Uses the existing bot session and server-owned capabilities."""

    def __init__(self, entry):
        self.entry = entry

    async def _session(
        self, *, telegram_user_id, tenant_id, expected_user_id=None,
    ):
        session, home = await self.entry._presentation(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
        )
        if (
            not isinstance(session, PlatformApiClientSession)
            or session.tenant_id != tenant_id
            or expected_user_id is not None
            and session.user_id != expected_user_id
        ):
            raise problem(403, "account_unavailable")
        return session, home

    async def _capabilities(self, session):
        result = await self.entry.api.construction.company_profile_capabilities(
            session=session,
        )
        data = result.data
        if (
            not isinstance(data, dict)
            or data.get("tenant_id") != str(session.tenant_id)
            or type(data.get("can_manage_settings")) is not bool
            or type(data.get("can_manage_profile")) is not bool
        ):
            raise problem()
        # Both existing policies are required by the language writer.
        return (
            data["can_manage_settings"] is True
            and data["can_manage_profile"] is True
        )

    async def read(
        self, *, telegram_user_id, tenant_id, expected_user_id=None,
    ):
        session, home = await self._session(
            telegram_user_id=telegram_user_id,
            tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        result = await self.entry.api.construction._request(
            "GET", "construction/settings/languages", session=session,
        )
        try:
            item = LanguageState.model_validate(result.data)
            codes = [option.code for option in item.languages]
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or item.user_id != session.user_id
                or len(codes) != len(set(codes))
                or item.interface_language not in codes
                or any(
                    code != code.strip()
                    or any(char.isspace() for char in code)
                    for code in codes
                )
                or not any(
                    option.code == item.organization_default_language
                    and option.enabled_for_organization is True
                    for option in item.languages
                )
            ):
                raise ValueError("Language context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        can_manage = await self._capabilities(session)
        return {
            "item": item.model_dump(mode="json"),
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": item.interface_language,
            "can_manage": can_manage,
        }

    async def write(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        language_code, expected_row_version,
    ):
        if (
            not isinstance(expected_user_id, UUID)
            or not isinstance(language_code, str)
            or not 1 <= len(language_code) <= 10
            or language_code != language_code.strip()
            or any(char.isspace() for char in language_code)
            or type(expected_row_version) is not int
            or not 1 <= expected_row_version <= 2**63 - 1
        ):
            raise ValueError("Invalid language change.")
        session, home = await self._session(
            telegram_user_id=telegram_user_id,
            tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        if not await self._capabilities(session):
            raise problem(403, "permission_required")

        # Do not reread or rebase expected_row_version before PATCH.
        result = await self.entry.api.construction._request(
            "PATCH", "construction/settings/languages",
            session=session,
            json_body={
                "default_language": language_code,
                "expected_row_version": expected_row_version,
            },
        )
        try:
            item = LanguageSaved.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or item.organization_default_language != language_code
                or item.row_version != (
                    expected_row_version + 1
                    if item.changed else expected_row_version
                )
            ):
                raise ValueError("Language write response differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {
            "item": item.model_dump(mode="json"),
            "actor_user_id": str(session.user_id),
            "language": home["language"],
        }
