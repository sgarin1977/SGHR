"""Exact-base draft creation and current draft reads through existing HTTP."""
from hashlib import sha256
import json
import re
from uuid import UUID

from api.routes.construction_template_versions import (
    TemplateDraftCreateRequest, TemplateDraftCreatedData, TemplateDraftData,
)
from services.construction.construction_organization_templates_bot_api import (
    OrganizationTemplatesBotApi,
)
from services.construction.construction_language_bot_api import problem

PATH = "construction/settings/measurement-templates"
REQUEST_FIELDS = {
    "template_id", "base_version_id", "expected_template_row_version",
    "base_version", "source_starter_version_id", "source_language",
    "section_count", "question_count", "key",
}


class TemplateDraftBotApi(OrganizationTemplatesBotApi):
    async def create_draft(self, *, request, **binding):
        if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
            raise ValueError("Invalid draft request.")
        template_id = UUID(request["template_id"])
        base_id = UUID(request["base_version_id"])
        origin = request["source_starter_version_id"]
        if origin is not None and str(UUID(origin)) != origin:
            raise ValueError("Invalid starter reference.")
        key = request["key"]
        if (
            str(template_id) != request["template_id"]
            or str(base_id) != request["base_version_id"]
            or not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
            or type(request["base_version"]) is not int
            or not 1 <= request["base_version"] <= 2**31 - 1
            or type(request["section_count"]) is not int
            or type(request["question_count"]) is not int
            or not 1 <= request["section_count"] <= 100
            or not request["section_count"] <= request["question_count"] <= 2000
            or not isinstance(request["source_language"], str)
            or re.fullmatch(r"[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?", request["source_language"]) is None
        ):
            raise ValueError("Invalid draft metadata.")
        body = TemplateDraftCreateRequest(
            base_version_id=base_id,
            expected_template_row_version=request["expected_template_row_version"],
        )
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "POST", PATH + "/" + str(template_id) + "/drafts",
            session=session, json_body=body.model_dump(mode="json"),
            idempotency_key=key,
        )
        try:
            counters = (
                "template_row_version", "version", "version_row_version",
                "section_count", "question_count",
            )
            if not isinstance(result.data, dict) or any(
                type(result.data.get(name)) is not int for name in counters
            ):
                raise ValueError("Draft counters differ.")
            item = TemplateDraftCreatedData.model_validate(result.data)
            if (
                result.meta != {} or item.tenant_id != session.tenant_id
                or item.created_by != session.user_id
                or item.template_id != template_id or item.base_version_id != base_id
                or item.template_row_version != body.expected_template_row_version
                or item.version <= request["base_version"]
                or item.version_id in (
                    template_id, base_id, session.tenant_id, session.user_id,
                )
                or item.source_starter_version_id != (None if origin is None else UUID(origin))
                or item.source_language != request["source_language"]
                or item.section_count != request["section_count"]
                or item.question_count != request["question_count"]
            ):
                raise ValueError("Draft creation receipt differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}

    async def read_draft(self, *, template_id, version_id, include_content=False, **binding):
        if type(include_content) is not bool:
            raise ValueError("Invalid draft content flag.")
        if not isinstance(template_id, UUID) or not isinstance(version_id, UUID):
            raise ValueError("Exact template and draft UUIDs are required.")
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", PATH + "/" + str(template_id) + "/drafts/" + str(version_id),
            session=session,
        )
        try:
            if (
                not isinstance(result.data, dict)
                or type(result.data.get("definition_schema_version")) is not int
            ):
                raise ValueError("Draft schema version differs.")
            item = TemplateDraftData.model_validate(result.data)
            payload = item.definition.model_dump(mode="json")
            labels = payload["labels"][item.source_language]
            description_key = payload["description_key"]
            if (
                result.meta != {} or item.tenant_id != session.tenant_id
                or item.template_id != template_id or item.version_id != version_id
                or labels[payload["title_key"]] != item.title
                or (None if description_key is None else labels[description_key]) != item.description
                or len(item.section_references) != len(payload["sections"])
            ):
                raise ValueError("Current draft context differs.")
            section_ids, question_ids = set(), set()
            total = 0
            for reference, section in zip(item.section_references, payload["sections"]):
                if (
                    reference.id in section_ids or reference.code != section["code"]
                    or len(reference.questions) != len(section["questions"])
                ):
                    raise ValueError("Draft section references differ.")
                section_ids.add(reference.id)
                for question, body in zip(reference.questions, section["questions"]):
                    if question.id in question_ids or question.code != body["code"]:
                        raise ValueError("Draft question references differ.")
                    question_ids.add(question.id)
                    total += 1
            encoded = json.dumps(
                payload, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        projected = item.model_dump(
            mode="json",
            exclude={"definition", "section_references", "title", "description"},
        ) | {
            "section_count": len(payload["sections"]), "question_count": total,
            "definition_hash": sha256(encoded).hexdigest(),
        }
        if include_content:
            projected.update(
                definition=payload, title=item.title, description=item.description,
                section_references=[ref.model_dump(mode="json") for ref in item.section_references],
            )
        return {**context, "item": projected}
