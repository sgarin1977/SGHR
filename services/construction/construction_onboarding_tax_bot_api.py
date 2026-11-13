"""Explicit onboarding tax selection over the existing HTTP contract."""
from uuid import UUID

from api.routes.construction_onboarding import (
    OnboardingTaxSelectionData, OnboardingTaxSelectionWriteData,
    OnboardingTaxSelectionUpdate,
)
from services.construction.construction_onboarding_bot_api import OnboardingBotApi
from services.construction.construction_tax_profile_bot_api import TaxProfileBotApi
from services.construction.construction_language_bot_api import problem

PATH = "construction/settings/onboarding/tax-profile"


class OnboardingTaxBotApi(OnboardingBotApi):
    async def selection(self, **binding):
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", PATH, session=session, allow_null=True,
        )
        try:
            if result.meta != {}:
                raise ValueError("Selection metadata differs.")
            if result.data is None:
                item = None
            else:
                model = OnboardingTaxSelectionData.model_validate(result.data)
                if model.tenant_id != session.tenant_id:
                    raise ValueError("Selection tenant differs.")
                item = model.model_dump(mode="json")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item}

    async def list_profiles(self, *, cursor=None, **binding):
        return await TaxProfileBotApi(self.entry).list_profiles(
            cursor=cursor, **binding,
        )

    async def get_profile(self, *, profile_id, **binding):
        return await TaxProfileBotApi(self.entry).get_profile(
            profile_id=profile_id, **binding,
        )

    async def select(self, *, profile_id, expected_row_version, **binding):
        if not isinstance(profile_id, UUID):
            raise ValueError("An exact profile UUID is required.")
        body = OnboardingTaxSelectionUpdate(
            tax_profile_id=profile_id,
            expected_row_version=expected_row_version,
        )
        session, context = await self.context(**binding)
        # Selection is versioned. This endpoint has no keyed replay contract.
        result = await self.entry.api.construction._request(
            "PATCH", PATH, session=session,
            json_body=body.model_dump(mode="json"),
        )
        try:
            item = OnboardingTaxSelectionWriteData.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or item.onboarding_tax_profile_id != profile_id
                or item.row_version != expected_row_version + int(item.changed)
            ):
                raise ValueError("Selection write response differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}
