"""Reviewed publication and original receipt replay through existing HTTP."""
from copy import deepcopy
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from api.construction_settings_contract import SettingsVersion
from api.routes.construction_template_versions import (
    TemplateDraftVersionRequest, TemplatePublicationData,
)
from services.construction.construction_template_draft_bot_api import PATH
from services.construction.construction_template_draft_check_bot_api import TemplateDraftCheckBotApi
from services.construction.construction_language_bot_api import problem


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: UUID
    version_id: UUID
    version: StrictInt = Field(ge=1, le=2**31 - 1)
    version_row_version: SettingsVersion
    template_row_version: SettingsVersion
    definition_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    active: StrictBool
    source_starter_version_id: UUID | None
    source_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10,
    )
    title: StrictStr = Field(min_length=1, max_length=120)
    description: StrictStr | None = Field(max_length=120)
    section_count: StrictInt = Field(ge=1, le=100)
    question_count: StrictInt = Field(ge=1, le=2000)


def canonical(model, value):
    checked = model.model_validate(value)
    if checked.model_dump(mode="json") != value:
        raise ValueError("Noncanonical publication metadata.")
    return checked


class TemplatePublishBotApi(TemplateDraftCheckBotApi):
    async def prepare_publish(self, *, selector, **binding):
        selected = canonical(Selector, selector)
        result = await self.read_draft(
            template_id=selected.template_id, version_id=selected.version_id,
            include_content=True, **binding,
        )
        item = result["item"]
        if any(item[name] != value for name, value in selector.items()):
            raise problem(409, "ROW_VERSION_CONFLICT")
        readiness = await self.check_draft(selector=selector, **binding)
        if any(
            readiness["item"][name] != item[name]
            for name in ("section_count", "question_count", "active")
        ):
            raise problem()
        summary = Summary.model_validate({
            name: item[name] for name in Summary.model_fields
        }).model_dump(mode="json")
        request = {
            "selector": deepcopy(selector), "summary": summary,
            "body": TemplateDraftVersionRequest(
                expected_row_version=selected.version_row_version,
            ).model_dump(mode="json"),
        }
        return {**readiness, "request": request}

    async def publish(self, *, request, **binding):
        if not isinstance(request, dict) or set(request) != {"selector", "summary", "body", "key"}:
            raise ValueError("Invalid publication request.")
        selected = canonical(Selector, request["selector"])
        summary = canonical(Summary, request["summary"])
        body = canonical(TemplateDraftVersionRequest, request["body"])
        key = request["key"]
        if (
            body.expected_row_version != selected.version_row_version
            or summary.section_count > summary.question_count
            or not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
        ):
            raise ValueError("Invalid publication metadata.")
        session, context = await self.context(**binding)
        response = await self.entry.api.construction._request(
            "POST",
            PATH + "/" + str(selected.template_id) + "/drafts/"
            + str(selected.version_id) + "/publish",
            session=session,
            json_body=deepcopy(request["body"]), idempotency_key=key,
        )
        try:
            counters = (
                "template_row_version", "version", "version_row_version",
                "definition_schema_version", "section_count", "question_count",
            )
            if (
                not isinstance(response.data, dict)
                or any(type(response.data.get(name)) is not int for name in counters)
            ):
                raise ValueError("Publication counters differ.")
            item = TemplatePublicationData.model_validate(response.data)
            values = item.model_dump(mode="json")
            if (
                response.meta != {} or item.tenant_id != session.tenant_id
                or item.template_id != selected.template_id
                or item.version_id != selected.version_id
                or item.version != selected.version
                or item.version_row_version != selected.version_row_version + 1
                or item.template_row_version != selected.template_row_version
                or item.content_hash != selected.definition_hash
                or item.updated_at != item.published_at
                or item.created_at > item.published_at
                or any(values[name] != value for name, value in request["summary"].items())
            ):
                raise ValueError("Publication receipt differs from the reviewed draft.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        # The author can differ from the publisher. Replay needs no draft GET.
        return {**context, "item": values}
