"""Original draft-title/description edits over the existing keyed PATCH."""
from copy import deepcopy
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

from api.routes.construction_template_versions import (
    TemplateDraftHeaderPatchRequest, TemplateDraftWriteData,
)
from api.routes.construction_organization_templates import OrganizationDefinition
from services.construction.construction_template_draft_bot_api import TemplateDraftBotApi, PATH
from services.construction.construction_language_bot_api import problem


def text_value(field, value):
    if field not in ("title", "description"):
        raise ValueError("Unknown header field.")
    if value is None:
        if field == "description":
            return None
        raise ValueError("A title is required.")
    if not isinstance(value, str):
        raise ValueError("Text is required.")
    value = value.strip()
    if not 1 <= len(value) <= 120 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("A single-line label of 1 to 120 characters is required.")
    return value


def digest(payload):
    return sha256(json.dumps(
        payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def used_keys(payload, exclude=None):
    result = set()
    if exclude != "title":
        result.add(payload["title_key"])
    if exclude != "description" and payload["description_key"] is not None:
        result.add(payload["description_key"])
    for section in payload["sections"]:
        result.add(section["label_key"])
        for question in section["questions"]:
            result.add(question["label_key"])
            if question["help_text_key"] is not None:
                result.add(question["help_text_key"])
            result.update(option["label_key"] for option in question["options"])
    return result


def build_fields(payload, source_language, field, value):
    value = text_value(field, value)
    candidate = deepcopy(payload)
    labels = candidate["labels"]
    key_field = field + "_key"
    old_key = candidate[key_field]
    old_value = None if old_key is None else labels[source_language][old_key]
    if old_value == value:
        fields = {"description_key": None} if value is None else {"labels": deepcopy(labels)}
        return fields, candidate

    fields = {}
    if value is None:
        candidate[key_field] = None
        fields[key_field] = None
    else:
        # A shared label must not change an unrelated question, section or field.
        others = used_keys(candidate, exclude=field)
        key = old_key
        if key is None or key in others:
            existing = set().union(*(set(values) for values in labels.values()))
            key = "header_" + field + "_" + uuid4().hex
            while key in existing:
                key = "header_" + field + "_" + uuid4().hex
        candidate[key_field] = key
        if key != old_key:
            fields[key_field] = key
        labels[source_language][key] = value
        # Supplied translations for the changed original cannot remain current.
        for language, values in labels.items():
            if language != source_language:
                values.pop(key, None)

    if old_key is not None and old_key not in used_keys(candidate):
        for values in labels.values():
            values.pop(old_key, None)
    if labels != payload["labels"]:
        fields["labels"] = deepcopy(labels)
    candidate = OrganizationDefinition.model_validate(candidate).model_dump(mode="json")
    if set(candidate["labels"][source_language]) != used_keys(candidate):
        raise ValueError("Original labels are incomplete.")
    return fields, candidate


class TemplateDraftHeaderBotApi(TemplateDraftBotApi):
    async def prepare_header(
        self, *, template_id, version_id, expected_row_version,
        expected_definition_hash, field, value, kind=None, item_id=None, **binding,
    ):
        if kind is not None:
            from services.construction.construction_template_draft_label_bot_api import TemplateDraftLabelBotApi
            if field == "sort_order":
                return await TemplateDraftLabelBotApi.prepare_order(
                    self, template_id=template_id, version_id=version_id,
                    expected_row_version=expected_row_version,
                    expected_definition_hash=expected_definition_hash,
                    kind=kind, item_id=item_id, value=value, **binding,
                )
            if field != "label":
                raise ValueError("An item label is required.")
            return await TemplateDraftLabelBotApi.prepare_label(
                self, template_id=template_id, version_id=version_id,
                expected_row_version=expected_row_version,
                expected_definition_hash=expected_definition_hash,
                kind=kind, item_id=item_id, value=value, **binding,
            )
        if item_id is not None:
            raise ValueError("An item kind is required.")
        value = text_value(field, value)
        TemplateDraftHeaderPatchRequest(expected_row_version=expected_row_version)
        result = await self.read_draft(
            template_id=template_id, version_id=version_id,
            include_content=True, **binding,
        )
        item = result["item"]
        if (
            item["version_row_version"] != expected_row_version
            or item["definition_hash"] != expected_definition_hash
        ):
            raise problem(409, "ROW_VERSION_CONFLICT")
        fields, candidate = build_fields(
            item["definition"], item["source_language"], field, value,
        )
        body = TemplateDraftHeaderPatchRequest.model_validate({
            "expected_row_version": expected_row_version, **fields,
        }).model_dump(mode="json", exclude_unset=True)
        labels = candidate["labels"][item["source_language"]]
        description_key = candidate["description_key"]
        request = {
            "template_id": str(template_id), "version_id": str(version_id),
            "body": body, "version": item["version"],
            "source_language": item["source_language"],
            "expected_title": labels[candidate["title_key"]],
            "expected_description": None if description_key is None else labels[description_key],
            "definition_hash": digest(candidate),
            "section_count": item["section_count"], "question_count": item["question_count"],
        }
        return {
            **result, "request": request, "field": field,
            "original": item[field], "value": value,
        }

    async def write_header(self, *, request, **binding):
        if isinstance(request, dict) and "kind" in request:
            from services.construction.construction_template_draft_label_bot_api import TemplateDraftLabelBotApi
            return await TemplateDraftLabelBotApi.write_label(self, request=request, **binding)
        expected_fields = {
            "template_id", "version_id", "body", "version", "source_language",
            "expected_title", "expected_description", "definition_hash",
            "section_count", "question_count", "key",
        }
        if not isinstance(request, dict) or set(request) != expected_fields:
            raise ValueError("Invalid header operation.")
        template_id, version_id = UUID(request["template_id"]), UUID(request["version_id"])
        key = request["key"]
        if (
            str(template_id) != request["template_id"]
            or str(version_id) != request["version_id"]
            or not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
            or not isinstance(request["body"], dict)
            or not set(request["body"]).issubset({
                "expected_row_version", "labels", "title_key", "description_key",
            })
            or type(request["version"]) is not int
            or type(request["section_count"]) is not int
            or type(request["question_count"]) is not int
            or not isinstance(request["definition_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", request["definition_hash"]) is None
        ):
            raise ValueError("Invalid header operation metadata.")
        body = TemplateDraftHeaderPatchRequest.model_validate(request["body"])
        if not body.patch_fields():
            raise ValueError("An explicit header field is required.")
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "PATCH", PATH + "/" + str(template_id) + "/drafts/" + str(version_id),
            session=session,
            json_body=body.model_dump(mode="json", exclude_unset=True),
            idempotency_key=key,
        )
        try:
            counters = (
                "template_row_version", "version", "version_row_version",
                "definition_schema_version", "section_count", "question_count",
            )
            if not isinstance(result.data, dict) or any(
                type(result.data.get(name)) is not int for name in counters
            ):
                raise ValueError("Header response counters differ.")
            item = TemplateDraftWriteData.model_validate(result.data)
            if (
                result.meta != {} or item.tenant_id != session.tenant_id
                or item.template_id != template_id or item.version_id != version_id
                or item.version != request["version"]
                or item.version_row_version != body.expected_row_version + int(item.changed)
                or item.source_language != request["source_language"]
                or item.title != request["expected_title"]
                or item.description != request["expected_description"]
                or item.definition_hash != request["definition_hash"]
                or item.section_count != request["section_count"]
                or item.question_count != request["question_count"]
            ):
                raise ValueError("Header write result differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}
