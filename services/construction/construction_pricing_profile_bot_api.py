"""Organization pricing profiles over the existing exact-decimal API."""
from copy import deepcopy
from uuid import UUID

from api.routes.construction_pricing_profiles import (
    PricingProfileReadData, PricingProfileListEnvelope,
)
from api.routes.construction_pricing_profile_writes import (
    PricingProfileCreateRequest, PricingProfilePatchRequest, PricingProfileWriteData,
)
from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, problem,
)

LEVELS = ("ECONOMY", "STANDARD", "PREMIUM")


class PricingProfileBotApi(ConstructionLanguageBotApi):
    async def context(self, **binding):
        session, home = await self._session(**binding)
        if (
            type(home.get("can_edit_pricing_profiles")) is not bool
            or not isinstance(home.get("language"), str)
            or not home["language"].strip()
        ):
            raise problem()
        return session, {
            "actor_user_id": str(session.user_id),
            "organization": home["name"], "language": home["language"],
            "can_manage": home["can_edit_pricing_profiles"],
        }

    async def list_profiles(self, **binding):
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", "construction/settings/pricing-profiles", session=session,
        )
        try:
            envelope = PricingProfileListEnvelope.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            items = envelope.data
            codes = [item.code for item in items]
            if (
                codes != sorted(set(codes), key=LEVELS.index)
                or len({item.id for item in items}) != len(items)
                or any(item.tenant_id != session.tenant_id for item in items)
            ):
                raise ValueError("Pricing collection context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "items": [item.model_dump(mode="json") for item in items]}

    async def get_profile(self, *, profile_id, **binding):
        if not isinstance(profile_id, UUID):
            raise ValueError("Pricing profile UUID is required.")
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", "construction/settings/pricing-profiles/" + str(profile_id),
            session=session,
        )
        try:
            item = PricingProfileReadData.model_validate(result.data)
            if (
                result.meta != {} or item.tenant_id != session.tenant_id
                or item.id != profile_id
            ):
                raise ValueError("Pricing profile context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}

    async def write_profile(self, *, request, **binding):
        if not isinstance(request, dict) or set(request) != {
            "operation", "profile_id", "body", "key",
        }:
            raise ValueError("Invalid pricing operation.")
        key, profile_id = request["key"], request["profile_id"]
        if (
            not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
        ):
            raise ValueError("Invalid operation key.")
        if request["operation"] == "create" and profile_id is None:
            model = PricingProfileCreateRequest.model_validate(request["body"])
            body = model.model_dump(mode="json")
            method, path = "POST", "construction/settings/pricing-profiles"
        elif request["operation"] == "patch" and isinstance(profile_id, str):
            profile_id = UUID(profile_id)
            model = PricingProfilePatchRequest.model_validate(request["body"])
            body = model.model_dump(mode="json", exclude_unset=True)
            method = "PATCH"
            path = "construction/settings/pricing-profiles/" + str(profile_id)
        else:
            raise ValueError("Unsupported pricing operation.")
        fields = model.domain_fields()
        if not fields:
            raise ValueError("An edited pricing field is required.")

        session, context = await self.context(**binding)
        if context["can_manage"] is not True:
            raise problem(403, "permission_required")
        result = await self.entry.api.construction._request(
            method, path, session=session,
            json_body=deepcopy(body), idempotency_key=key,
        )
        try:
            item = PricingProfileWriteData.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or any(getattr(item, name) != value for name, value in fields.items())
            ):
                raise ValueError("Pricing response fields differ.")
            if request["operation"] == "create":
                if (
                    item.created_by != session.user_id or item.row_version != 1
                    or item.changed is not True
                ):
                    raise ValueError("Pricing create response differs.")
            elif (
                item.id != profile_id
                or item.row_version != body["expected_row_version"] + int(item.changed)
            ):
                raise ValueError("Pricing response version differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}
