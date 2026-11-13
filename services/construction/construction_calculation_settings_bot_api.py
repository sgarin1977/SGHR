"""Calculation settings through existing authenticated HTTP contracts."""
from copy import deepcopy

from api.routes.construction_calculation_settings import (
    CalculationSettingsCreateRequest, CalculationSettingsPatchRequest,
    CalculationSettingsReadData, CalculationSettingsWriteData,
)
from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, problem,
)
from services.construction.construction_currency_bot_api import ConstructionCurrencyBotApi

FIELDS = (
    "money_input_precision", "quantity_precision", "supplier_stale_price_days",
    "minimum_job_amount", "minimum_job_currency",
    "default_technological_waste_percent", "default_local_repair_percent",
)
DECIMALS = (
    "minimum_job_amount", "default_technological_waste_percent",
    "default_local_repair_percent",
)
PATH = "construction/settings/calculation"


class CalculationSettingsBotApi(ConstructionLanguageBotApi):
    async def context(self, **binding):
        session, home = await self._session(**binding)
        result = await self.entry.api.construction.company_profile_capabilities(
            session=session,
        )
        caps = result.data
        if (
            not isinstance(caps, dict)
            or caps.get("tenant_id") != str(session.tenant_id)
            or type(caps.get("can_manage_settings")) is not bool
            or not isinstance(home.get("language"), str)
            or not home["language"].strip()
        ):
            raise problem()
        # Calculation settings require Construction settings authority.
        # Platform company-profile management is not an additional gate.
        return session, {
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_manage": caps["can_manage_settings"],
        }

    @staticmethod
    def parse_item(data, *, session, write=False):
        try:
            if not isinstance(data, dict):
                raise ValueError("Settings data must be an object.")
            for field in DECIMALS:
                value = data[field]
                if value is not None and not isinstance(value, str):
                    raise ValueError("Exact decimal strings are required.")
            schema = CalculationSettingsWriteData if write else CalculationSettingsReadData
            item = schema.model_validate(data)
            if item.tenant_id != session.tenant_id:
                raise ValueError("Settings tenant differs.")
            projected = item.model_dump(mode="json")
            # Reuse the established configuration contract, including
            # minimum amount/currency pairing and supported currency.
            CalculationSettingsCreateRequest.model_validate({
                field: projected[field] for field in FIELDS
            })
            return item
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None

    async def read(self, **binding):
        session, context = await self.context(**binding)
        if context["can_manage"] is not True:
            raise problem(403, "permission_required")
        result = await self.entry.api.construction._request(
            "GET", PATH, session=session, allow_null=True,
        )
        if result.meta != {}:
            raise problem()
        item = (
            None if result.data is None
            else self.parse_item(result.data, session=session).model_dump(mode="json")
        )
        return {**context, "item": item}

    async def currency_codes(self, **binding):
        result = await ConstructionCurrencyBotApi(self.entry).read(**binding)
        return tuple(item["code"] for item in result["item"]["currencies"])

    async def write(self, *, request, **binding):
        if (
            not isinstance(request, dict)
            or set(request) != {"create", "body", "key"}
            or type(request["create"]) is not bool
        ):
            raise ValueError("Invalid calculation settings operation.")
        key = request["key"]
        if (
            not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
        ):
            raise ValueError("Invalid operation key.")
        create = request["create"]
        schema = CalculationSettingsCreateRequest if create else CalculationSettingsPatchRequest
        model = schema.model_validate(request["body"])
        body = model.model_dump(mode="json", exclude_unset=not create)
        fields = model.domain_fields()
        if not fields:
            raise ValueError("A settings change is required.")

        session, context = await self.context(**binding)
        if context["can_manage"] is not True:
            raise problem(403, "permission_required")
        result = await self.entry.api.construction._request(
            "POST" if create else "PATCH", PATH, session=session,
            json_body=deepcopy(body), idempotency_key=key,
        )
        item = self.parse_item(result.data, session=session, write=True)
        if result.meta != {} or any(
            getattr(item, field) != value for field, value in fields.items()
        ):
            raise problem()
        if create:
            if (
                item.created_by != session.user_id or item.row_version != 1
                or item.changed is not True
            ):
                raise problem()
        elif item.row_version != body["expected_row_version"] + int(item.changed):
            raise problem()
        return {**context, "item": item.model_dump(mode="json")}
