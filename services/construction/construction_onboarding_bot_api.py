"""Telegram onboarding uses the existing versioned HTTP contract."""
from copy import deepcopy

from api.routes.construction_onboarding import (
    OnboardingStartRequest, OnboardingVersionRequest,
    OnboardingProgressData, OnboardingProgressWriteData,
)
from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, problem,
)

PATH = "construction/settings/onboarding"
STEPS = (
    "COMPANY", "LOCALIZATION", "TAXES", "PRICING_PROFILES",
    "WORK_PRICES", "MATERIALS_SUPPLIERS", "MEASUREMENT_TEMPLATES",
)
SKIPPABLE = frozenset(("WORK_PRICES", "MATERIALS_SUPPLIERS"))


class OnboardingBotApi(ConstructionLanguageBotApi):
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
        # Onboarding requires Construction settings authority.
        # Platform company-profile authority is not an extra onboarding gate.
        if caps["can_manage_settings"] is not True:
            raise problem(403, "permission_required")
        return session, {
            "actor_user_id": str(session.user_id),
            "language": home["language"], "organization": home["name"],
        }

    @staticmethod
    def parse(result, session, *, write=False):
        try:
            schema = OnboardingProgressWriteData if write else OnboardingProgressData
            item = schema.model_validate(result.data)
            if result.meta != {} or item.tenant_id != session.tenant_id:
                raise ValueError("Onboarding response context differs.")
            return item
        except (ValueError, TypeError, AttributeError):
            raise problem() from None

    async def read(self, **binding):
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", PATH, session=session,
        )
        item = self.parse(result, session)
        return {**context, "item": item.model_dump(mode="json")}

    async def write(self, *, request, **binding):
        if (
            not isinstance(request, dict)
            or set(request) != {"action", "step", "expected_row_version"}
        ):
            raise ValueError("Invalid onboarding operation.")
        request = deepcopy(request)
        action, step = request["action"], request["step"]
        version = request["expected_row_version"]
        if action == "start":
            if step is not None:
                raise ValueError("Start accepts no step.")
            body = OnboardingStartRequest(expected_row_version=version)
            suffix = "/start"
        else:
            if (
                action not in ("open", "complete", "skip")
                or step not in STEPS
                or action == "skip" and step not in SKIPPABLE
            ):
                raise ValueError("Invalid onboarding step operation.")
            body = OnboardingVersionRequest(expected_row_version=version)
            suffix = "/steps/" + step + "/" + action

        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "POST", PATH + suffix, session=session,
            json_body=body.model_dump(mode="json"),
        )
        item = self.parse(result, session, write=True)
        try:
            if item.settings_created:
                if action != "start" or version is not None or not item.changed:
                    raise ValueError("Settings creation differs.")
                expected = 1
            else:
                if version is None:
                    raise ValueError("Existing settings version is missing.")
                expected = version + int(item.changed)
            data = item.model_dump(mode="json")
            if item.row_version != expected or not item.started:
                raise ValueError("Onboarding write version differs.")
            if action == "open" and data["current_step"] != step:
                raise ValueError("Opened step differs.")
            if action in ("complete", "skip"):
                status = "COMPLETED" if action == "complete" else "SKIPPED"
                if data["step_states"][step] != status:
                    raise ValueError("Step result differs.")
        except (ValueError, TypeError, KeyError):
            raise problem() from None
        return {**context, "item": data}
