"""Organization region through existing authenticated HTTP operations."""
import re
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field,
    StrictBool, StrictStr, model_validator,
)

from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, Version, problem,
)


def country_input(value):
    if not isinstance(value, str):
        raise ValueError("Country code required.")
    value = value.strip().upper()
    if re.fullmatch(r"[A-Z]{2}", value) is None:
        raise ValueError("Country code required.")
    return value


def timezone_input(value):
    if not isinstance(value, str):
        raise ValueError("IANA timezone required.")
    value = value.strip()
    if (
        not 1 <= len(value) <= 255
        or re.fullmatch(r"[A-Za-z0-9_+/-]+", value) is None
        or value in {"localtime", "posixrules"}
        or value.startswith(("posix/", "right/"))
    ):
        raise ValueError("IANA timezone required.")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("IANA timezone unavailable.") from None
    return value


class RegionState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    country_code: StrictStr | None = Field(
        min_length=2, max_length=2, pattern=r"^[A-Z]{2}$",
    )
    timezone: StrictStr | None = Field(min_length=1, max_length=255)
    configured: StrictBool
    organization_updated_at: AwareDatetime
    row_version: Version

    @model_validator(mode="after")
    def coherent(self):
        if self.configured:
            if (
                country_input(self.country_code) != self.country_code
                or timezone_input(self.timezone) != self.timezone
            ):
                raise ValueError("Region differs.")
        elif self.country_code is not None or self.timezone is not None:
            raise ValueError("Unconfigured region must contain no defaults.")
        return self


class RegionSaved(RegionState):
    changed: StrictBool


class RegionBotApi(ConstructionLanguageBotApi):
    async def context(
        self, *, telegram_user_id, tenant_id, expected_user_id=None,
    ):
        session, home = await self._session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # Region uses the same two existing policies as organization language.
        if not await self._capabilities(session):
            raise problem(403, "permission_required")
        return session, {
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": home["language"],
            "can_manage": True,
        }

    async def read(self, **binding):
        session, common = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", "construction/settings/region", session=session,
        )
        try:
            item = RegionState.model_validate(result.data)
            if result.meta != {} or item.tenant_id != session.tenant_id:
                raise ValueError("Region context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**common, "item": item.model_dump(mode="json")}

    async def write(
        self, *, telegram_user_id, tenant_id, expected_user_id,
        country_code, timezone_code, expected_row_version,
    ):
        if (
            not isinstance(expected_user_id, UUID)
            or country_input(country_code) != country_code
            or timezone_input(timezone_code) != timezone_code
            or type(expected_row_version) is not int
            or not 1 <= expected_row_version <= 2**63 - 1
        ):
            raise ValueError("Invalid region change.")
        session, common = await self.context(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        # Keep the reviewed version. This HTTP operation has no keyed replay.
        result = await self.entry.api.construction._request(
            "PATCH", "construction/settings/region", session=session,
            json_body={
                "country_code": country_code,
                "timezone": timezone_code,
                "expected_row_version": expected_row_version,
            },
        )
        try:
            item = RegionSaved.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or not item.configured
                or item.country_code != country_code
                or item.timezone != timezone_code
                or item.row_version != expected_row_version + int(item.changed)
            ):
                raise ValueError("Region write response differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**common, "item": item.model_dump(mode="json")}
