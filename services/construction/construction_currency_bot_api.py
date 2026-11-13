"""Organization currency presentation over the existing authenticated API."""
import re
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictStr,
)

from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, Version, problem,
)

CurrencyCode = Annotated[
    StrictStr, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]


class CurrencyOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: CurrencyCode
    name: StrictStr = Field(min_length=1)


class CurrencyState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    organization_default_currency: CurrencyCode
    organization_updated_at: AwareDatetime
    row_version: Version
    currencies: list[CurrencyOption]


class CurrencySaved(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    organization_default_currency: CurrencyCode
    organization_updated_at: AwareDatetime
    row_version: Version
    changed: StrictBool


class ConstructionCurrencyBotApi(ConstructionLanguageBotApi):
    """Reuses session binding and the same existing settings authority."""

    async def read(
        self, *, telegram_user_id, tenant_id, expected_user_id=None,
    ):
        session, home = await self._session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        result = await self.entry.api.construction._request(
            "GET", "construction/settings/currency", session=session,
        )
        try:
            item = CurrencyState.model_validate(result.data)
            codes = [option.code for option in item.currencies]
            language = home["language"]
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or len(codes) != len(set(codes))
                or item.organization_default_currency not in codes
                or any(not option.name.strip() for option in item.currencies)
                or not isinstance(language, str) or not language.strip()
            ):
                raise ValueError("Currency context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        can_manage = await self._capabilities(session)
        return {
            "item": item.model_dump(mode="json"),
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": language,
            "can_manage": can_manage,
        }

    async def write(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        currency_code, expected_row_version,
    ):
        if (
            not isinstance(expected_user_id, UUID)
            or not isinstance(currency_code, str)
            or re.fullmatch(r"[A-Z]{3}", currency_code) is None
            or type(expected_row_version) is not int
            or not 1 <= expected_row_version <= 2**63 - 1
        ):
            raise ValueError("Invalid currency change.")
        session, home = await self._session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        if not await self._capabilities(session):
            raise problem(403, "permission_required")
        language = home.get("language")
        if not isinstance(language, str) or not language.strip():
            raise problem()

        # Keep the reviewed version. No reread/rebase before PATCH.
        result = await self.entry.api.construction._request(
            "PATCH", "construction/settings/currency", session=session,
            json_body={
                "default_currency": currency_code,
                "expected_row_version": expected_row_version,
            },
        )
        try:
            item = CurrencySaved.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or item.organization_default_currency != currency_code
                or item.row_version != (
                    expected_row_version + 1 if item.changed
                    else expected_row_version
                )
            ):
                raise ValueError("Currency write response differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {
            "item": item.model_dump(mode="json"),
            "actor_user_id": str(session.user_id),
            "language": language,
        }
