"""Exact-version readiness through the existing read-only validation operation."""
import re
from uuid import UUID

from api.routes.construction_template_versions import (
    TemplateDraftVersionRequest, TemplatePublicationReadinessData,
)
from services.construction.construction_template_draft_bot_api import TemplateDraftBotApi, PATH
from services.construction.construction_language_bot_api import problem


class TemplateDraftCheckBotApi(TemplateDraftBotApi):
    async def check_draft(self, *, selector, **binding):
        fields = {
            "template_id", "version_id", "version", "version_row_version",
            "template_row_version", "definition_hash",
        }
        if not isinstance(selector, dict) or set(selector) != fields:
            raise ValueError("An exact draft selector is required.")
        template_id, version_id = UUID(selector["template_id"]), UUID(selector["version_id"])
        if (
            str(template_id) != selector["template_id"]
            or str(version_id) != selector["version_id"]
            or type(selector["version"]) is not int
            or not 1 <= selector["version"] <= 2**31 - 1
            or type(selector["template_row_version"]) is not int
            or not 1 <= selector["template_row_version"] <= 2**63 - 1
            or not isinstance(selector["definition_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", selector["definition_hash"]) is None
        ):
            raise ValueError("Invalid draft selector.")
        body = TemplateDraftVersionRequest(
            expected_row_version=selector["version_row_version"],
        )
        session, context = await self.context(**binding)
        response = await self.entry.api.construction._request(
            "POST",
            PATH + "/" + str(template_id) + "/drafts/" + str(version_id) + "/validate",
            session=session,
            json_body=body.model_dump(mode="json"),
        )
        try:
            counters = (
                "version", "version_row_version", "template_row_version",
                "section_count", "question_count", "binding_count",
            )
            if (
                not isinstance(response.data, dict)
                or any(type(response.data.get(name)) is not int for name in counters)
            ):
                raise ValueError("Readiness counters differ.")
            item = TemplatePublicationReadinessData.model_validate(response.data)
            if (
                response.meta != {} or item.tenant_id != session.tenant_id
                or item.template_id != template_id or item.version_id != version_id
                or item.section_count > item.question_count
                or item.binding_count > item.question_count
            ):
                raise ValueError("Readiness identity or counts differ.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        if any(
            getattr(item, name) != selector[name]
            for name in (
                "version", "version_row_version", "template_row_version", "definition_hash",
            )
        ):
            raise problem(409, "ROW_VERSION_CONFLICT")
        return {**context, "item": item.model_dump(mode="json")}
