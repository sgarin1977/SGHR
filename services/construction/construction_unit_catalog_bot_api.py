"""Read the existing unit catalog using the canonical bot API session."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, problem,
)


class UnitOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: StrictStr = Field(
        min_length=1, max_length=32, pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    dimension: StrictStr = Field(min_length=1, max_length=32)
    symbol: StrictStr | None
    label_key: StrictStr = Field(min_length=1)
    label: StrictStr = Field(min_length=1, max_length=120)
    label_status: Literal["provided", "cached", "translated"]


class UnitCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    catalog_version: Literal[1]
    language_code: StrictStr = Field(min_length=1, max_length=10)
    label_source_version: StrictInt = Field(ge=1)
    units: list[UnitOption] = Field(min_length=1)


class ConstructionUnitCatalogBotApi(ConstructionLanguageBotApi):
    async def read(
        self, *, telegram_user_id, tenant_id, expected_user_id,
    ):
        session, home = await self._session(
            telegram_user_id=telegram_user_id,
            tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        result = await self.entry.api.construction._request(
            "GET", "construction/settings/units", session=session,
        )
        try:
            item = UnitCatalog.model_validate(result.data)
            codes = [unit.code for unit in item.units]
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or type(result.data["catalog_version"]) is not int
                or item.language_code != home.get("language")
                or len(codes) != len(set(codes))
                or any(
                    not unit.label.strip()
                    or unit.label_key != "construction.unit." + unit.code.lower()
                    for unit in item.units
                )
            ):
                raise ValueError("Unit catalog context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        # No settings-management gate: this endpoint is a catalog read.
        return {
            "item": item.model_dump(mode="json"),
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": item.language_code,
        }
