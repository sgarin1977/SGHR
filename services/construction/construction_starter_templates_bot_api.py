"""Starter catalog, localized preview and exact-version copying over HTTP."""
import asyncio
from hashlib import sha256
import json
import re
from uuid import UUID

from api.routes.construction_starter_templates import (
    StarterTemplateListEnvelope, StarterTemplateVersionData,
)
from api.routes.construction_organization_templates import TemplateTranslatedBatchEnvelope
from api.routes.construction_template_copy import TemplateCopyRequest, TemplateCopyData
from services.construction.construction_onboarding_bot_api import OnboardingBotApi
from services.construction.construction_language_bot_api import problem

PATH = "construction/settings/starter-measurement-templates"
COPY_PATH = "construction/settings/measurement-templates/copy-starter"
COPY_FIELDS = {
    "source_version_id", "source_template_id", "source_code", "content_hash",
    "section_count", "question_count", "key",
}


class StarterTemplatesBotApi(OnboardingBotApi):
    async def _texts(self, item, session, context):
        result = await self.entry.api.construction._request(
            "GET", PATH + "/versions/" + str(item.version_id) + "/translated-texts",
            session=session, params=[("fields", "title"), ("fields", "description")],
        )
        try:
            envelope = TemplateTranslatedBatchEnvelope.model_validate({
                "data": result.data, "meta": result.meta, "request_id": result.request_id,
            })
            fields = envelope.data
            if envelope.meta != {} or [row.field_name for row in fields] != ["title", "description"]:
                raise ValueError("Translated fields differ.")
            for row, original in zip(fields, (item.title, item.description)):
                if row.target_language != context["language"]:
                    raise problem(409, "LANGUAGE_CONTEXT_CHANGED")
                if (
                    row.tenant_id != session.tenant_id or row.user_id != session.user_id
                    or row.template_id != item.template_id or row.version_id != item.version_id
                    or row.original_text != original
                    or original is not None and (
                        row.source_version != item.version
                        or row.source_language != item.source_language
                    )
                ):
                    raise ValueError("Translated source context differs.")
            if not fields[0].display_text or not fields[0].display_text.strip():
                raise ValueError("Translated title is missing.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {
            "display_title": fields[0].display_text,
            "display_description": fields[1].display_text,
            "text_presentations": {
                field.field_name: field.model_dump(mode="json")
                for field in fields
            },
        }

    async def list_starters(self, *, cursor=None, **binding):
        if cursor is not None and (
            not isinstance(cursor, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{68}", cursor) is None
        ):
            raise ValueError("Invalid catalog cursor.")
        session, context = await self.context(**binding)
        params = {"limit": 6}
        if cursor is not None:
            params["cursor"] = cursor
        result = await self.entry.api.construction._request(
            "GET", PATH, session=session, params=params,
        )
        try:
            page = StarterTemplateListEnvelope.model_validate({
                "data": result.data, "meta": result.meta, "request_id": result.request_id,
            })
            ids = [item.template_id for item in page.data]
            codes = [item.code for item in page.data]
            following = page.meta.pagination.next_cursor
            if (
                len(ids) > 6 or ids != sorted(set(ids))
                or len(codes) != len(set(codes))
                or following is not None and len(ids) != 6
            ):
                raise ValueError("Catalog page differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        localized = await asyncio.gather(*(
            self._texts(item, session, context) for item in page.data
        ))
        return {
            **context, "next_cursor": following,
            "items": [
                item.model_dump(mode="json") | labels
                for item, labels in zip(page.data, localized)
            ],
        }

    async def get_starter(self, *, version_id, **binding):
        if not isinstance(version_id, UUID):
            raise ValueError("An exact version UUID is required.")
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", PATH + "/versions/" + str(version_id), session=session,
        )
        try:
            item = StarterTemplateVersionData.model_validate(result.data)
            definition = item.definition
            encoded = json.dumps(
                definition, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            sections = definition["sections"]
            if (
                result.meta != {} or item.version_id != version_id
                or sha256(encoded).hexdigest() != item.content_hash
                or not isinstance(sections, list) or not 1 <= len(sections) <= 100
                or any(
                    not isinstance(section, dict)
                    or not isinstance(section.get("questions"), list)
                    or any(not isinstance(question, dict) for question in section["questions"])
                    for section in sections
                )
            ):
                raise ValueError("Starter definition differs.")
            questions = sum(len(section["questions"]) for section in sections)
            if not len(sections) <= questions <= 2000:
                raise ValueError("Starter counts differ.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        labels = await self._texts(item, session, context)
        return {
            **context,
            "item": item.model_dump(mode="json", exclude={"definition"}) | labels | {
                "section_count": len(sections), "question_count": questions,
            },
        }

    async def copy(self, *, request, **binding):
        if not isinstance(request, dict) or set(request) != COPY_FIELDS:
            raise ValueError("Invalid copy operation.")
        source = UUID(request["source_version_id"])
        root = UUID(request["source_template_id"])
        key = request["key"]
        if (
            str(source) != request["source_version_id"]
            or str(root) != request["source_template_id"]
            or not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
            or not isinstance(request["source_code"], str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", request["source_code"]) is None
            or not isinstance(request["content_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", request["content_hash"]) is None
            or type(request["section_count"]) is not int
            or type(request["question_count"]) is not int
            or not 1 <= request["section_count"] <= 100
            or not request["section_count"] <= request["question_count"] <= 2000
        ):
            raise ValueError("Invalid copy metadata.")
        body = TemplateCopyRequest(source_version_id=source, code=None)
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "POST", COPY_PATH, session=session,
            json_body=body.model_dump(mode="json"), idempotency_key=key,
        )
        try:
            counters = (
                "template_row_version", "version", "version_row_version",
                "definition_schema_version", "section_count", "question_count",
            )
            if not isinstance(result.data, dict) or any(
                type(result.data.get(name)) is not int for name in counters
            ):
                raise ValueError("Copy counters differ.")
            item = TemplateCopyData.model_validate(result.data)
            if (
                result.meta != {} or item.tenant_id != session.tenant_id
                or item.created_by != session.user_id
                or item.source_starter_version_id != source
                or item.code != request["source_code"]
                or item.content_hash != request["content_hash"]
                or item.section_count != request["section_count"]
                or item.question_count != request["question_count"]
                or item.template_id == item.template_version_id
                or item.template_id in (root, source, session.tenant_id, session.user_id)
                or item.template_version_id in (root, source, session.tenant_id, session.user_id)
            ):
                raise ValueError("Copy result context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}
