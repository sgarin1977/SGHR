"""Organization template reads and localization through existing HTTP APIs."""
import asyncio
from hashlib import sha256
import json
import re
from uuid import UUID

from api.routes.construction_organization_templates import (
    OrganizationTemplateListEnvelope, OrganizationTemplateVersionData,
    TemplateTranslatedBatchEnvelope,
)
from services.construction.construction_onboarding_bot_api import OnboardingBotApi
from services.construction.construction_language_bot_api import problem

PATH = "construction/settings/measurement-templates"


class OrganizationTemplatesBotApi(OnboardingBotApi):
    async def _texts(self, item, session, context):
        result = await self.entry.api.construction._request(
            "GET", PATH + "/versions/" + str(item.version_id) + "/translated-texts",
            session=session,
            params=[("fields", "title"), ("fields", "description")],
        )
        try:
            envelope = TemplateTranslatedBatchEnvelope.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            fields = envelope.data
            if (
                envelope.meta != {}
                or [field.field_name for field in fields] != ["title", "description"]
            ):
                raise ValueError("Translated fields differ.")
            for field, original in zip(fields, (item.title, item.description)):
                if field.target_language != context["language"]:
                    raise problem(409, "LANGUAGE_CONTEXT_CHANGED")
                if (
                    field.tenant_id != session.tenant_id
                    or field.user_id != session.user_id
                    or field.template_id != item.template_id
                    or field.version_id != item.version_id
                    or field.original_text != original
                    or original is not None and (
                        field.source_version != item.version
                        or field.source_language != item.source_language
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

    async def list_templates(self, *, cursor=None, **binding):
        if cursor is not None and (
            not isinstance(cursor, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{68}", cursor) is None
        ):
            raise ValueError("Invalid template cursor.")
        session, context = await self.context(**binding)
        params = {"limit": 6}
        if cursor is not None:
            params["cursor"] = cursor
        result = await self.entry.api.construction._request(
            "GET", PATH, session=session, params=params,
        )
        try:
            page = OrganizationTemplateListEnvelope.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            ids = [item.template_id for item in page.data]
            codes = [item.code for item in page.data]
            following = page.meta.pagination.next_cursor
            if (
                len(ids) > 6 or ids != sorted(set(ids))
                or len(codes) != len(set(codes))
                or any(item.tenant_id != session.tenant_id for item in page.data)
                or following is not None and len(ids) != 6
            ):
                raise ValueError("Template page context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        labels = await asyncio.gather(*(
            self._texts(item, session, context) for item in page.data
        ))
        return {
            **context, "next_cursor": following,
            "items": [
                item.model_dump(mode="json") | localized
                for item, localized in zip(page.data, labels)
            ],
        }

    async def get_template(self, *, version_id, **binding):
        if not isinstance(version_id, UUID):
            raise ValueError("An exact template version UUID is required.")
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", PATH + "/versions/" + str(version_id), session=session,
        )
        try:
            item = OrganizationTemplateVersionData.model_validate(result.data)
            payload = item.definition.model_dump(mode="json")
            encoded = json.dumps(
                payload, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            sections = payload["sections"]
            labels = payload["labels"][item.source_language]
            description_key = payload["description_key"]
            if (
                result.meta != {} or item.tenant_id != session.tenant_id
                or item.version_id != version_id
                or sha256(encoded).hexdigest() != item.content_hash
                or labels[payload["title_key"]] != item.title
                or (None if description_key is None else labels[description_key])
                    != item.description
                or len(item.section_references) != len(sections)
            ):
                raise ValueError("Template version context differs.")
            section_ids, question_ids = set(), set()
            total = 0
            for reference, section in zip(item.section_references, sections):
                if (
                    reference.id in section_ids
                    or reference.code != section["code"]
                    or len(reference.questions) != len(section["questions"])
                ):
                    raise ValueError("Section references differ.")
                section_ids.add(reference.id)
                for question, body in zip(reference.questions, section["questions"]):
                    if question.id in question_ids or question.code != body["code"]:
                        raise ValueError("Question references differ.")
                    question_ids.add(question.id)
                    total += 1
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        localized = await self._texts(item, session, context)
        return {
            **context,
            "item": item.model_dump(
                mode="json", exclude={"definition", "section_references"},
            ) | localized | {
                "section_count": len(sections), "question_count": total,
            },
        }
