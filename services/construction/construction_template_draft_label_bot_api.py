"""Original section/question labels through the existing atomic item PATCH."""
from copy import deepcopy
import re
from uuid import UUID, uuid4

from api.routes.construction_organization_templates import OrganizationDefinition
from api.routes.construction_template_versions import (
    TemplateDraftSectionPatchRequest, TemplateDraftQuestionPatchRequest,
    TemplateDraftItemWriteData,
)
from services.construction.construction_template_draft_bot_api import PATH
from services.construction.construction_template_draft_header_bot_api import (
    TemplateDraftHeaderBotApi, digest, used_keys, text_value,
)
from services.construction.construction_language_bot_api import problem


def locate(item, kind, item_id):
    if kind not in ("SECTION", "QUESTION"):
        raise ValueError("Unknown draft item kind.")
    identity = str(UUID(str(item_id)))
    if isinstance(item_id, str) and identity != item_id:
        raise ValueError("A canonical item UUID is required.")
    for section, reference in zip(
        item["definition"]["sections"], item["section_references"],
    ):
        if kind == "SECTION" and reference["id"] == identity:
            return section
        if kind == "QUESTION":
            for question, child in zip(section["questions"], reference["questions"]):
                if child["id"] == identity:
                    return question
    raise problem(404, "not_found")


def label_fields(item, kind, item_id, value):
    value = text_value("title", value)
    candidate = deepcopy(item["definition"])
    target = locate({**item, "definition": candidate}, kind, item_id)
    language = item["source_language"]
    labels = candidate["labels"]
    old_key = target["label_key"]
    original = labels[language][old_key]
    if original == value:
        return {"fields": {"label_key": old_key}}, candidate, original

    # Temporarily detach this reference to detect every other use of the key.
    existing = set().union(*(set(values) for values in labels.values()))
    fresh_key = "item_label_" + uuid4().hex
    while fresh_key in existing:
        fresh_key = "item_label_" + uuid4().hex
    target["label_key"] = fresh_key
    key = fresh_key if old_key in used_keys(candidate) else old_key
    target["label_key"] = key
    labels[language][key] = value
    for code, values in labels.items():
        if code != language:
            values.pop(key, None)

    candidate = OrganizationDefinition.model_validate(candidate).model_dump(mode="json")
    if set(candidate["labels"][language]) != used_keys(candidate):
        raise ValueError("Original labels are incomplete.")
    return {
        "fields": {"label_key": key},
        "labels": deepcopy(candidate["labels"]),
    }, candidate, original


def body_model(kind):
    if kind == "SECTION":
        return TemplateDraftSectionPatchRequest
    if kind == "QUESTION":
        return TemplateDraftQuestionPatchRequest
    raise ValueError("Unknown draft item kind.")


def editable_fields(kind, body):
    fields = body["fields"]
    keys = set(fields)
    if keys == {"label_key"}:
        return True
    if keys == {"sort_order"}:
        value = fields["sort_order"]
        return "labels" not in body and type(value) is int and 0 <= value <= 2147483647
    allowed = {"active", "required"} if kind == "QUESTION" else {"active"}
    return (
        len(keys) == 1 and keys.issubset(allowed)
        and "labels" not in body
        and all(type(value) is bool for value in fields.values())
    )


def flag_fields(item, kind, item_id, field, value):
    allowed = {"active", "required"} if kind == "QUESTION" else {"active"}
    if kind not in ("SECTION", "QUESTION") or field not in allowed or type(value) is not bool:
        raise ValueError("An explicit supported Boolean field is required.")
    candidate = deepcopy(item["definition"])
    target = locate({**item, "definition": candidate}, kind, item_id)
    original = target[field]
    if type(original) is not bool:
        raise problem()
    target[field] = value
    try:
        candidate = OrganizationDefinition.model_validate(candidate).model_dump(mode="json")
    except ValueError:
        raise problem(422, "TEMPLATE_STRUCTURE_INVALID") from None
    return {"fields": {field: value}}, candidate, original


def order_value(value):
    if isinstance(value, str):
        value = value.strip()
        if not value or len(value) > 10 or not value.isascii() or not value.isdecimal():
            raise ValueError("A nonnegative integer is required.")
        value = int(value)
    if type(value) is not int or not 0 <= value <= 2147483647:
        raise ValueError("Order must fit the existing PostgreSQL integer column.")
    return value


def order_fields(item, kind, item_id, value):
    value = order_value(value)
    candidate = deepcopy(item["definition"])
    target = locate({**item, "definition": candidate}, kind, item_id)
    original = target["sort_order"]
    target["sort_order"] = value
    # Match the existing server normalization before checking the whole draft.
    for section in candidate["sections"]:
        section["questions"].sort(key=lambda row: row["sort_order"])
    candidate["sections"].sort(key=lambda row: row["sort_order"])
    try:
        candidate = OrganizationDefinition.model_validate(candidate).model_dump(mode="json")
    except ValueError:
        raise problem(422, "TEMPLATE_ORDER_INVALID") from None
    return {"fields": {"sort_order": value}}, candidate, original


class TemplateDraftLabelBotApi(TemplateDraftHeaderBotApi):
    async def prepare_label(
        self, *, template_id, version_id, expected_row_version,
        expected_definition_hash, kind, item_id, value, **binding,
    ):
        value = text_value("title", value)
        model = body_model(kind)
        model(expected_row_version=expected_row_version)
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
        fields, candidate, original = label_fields(item, kind, item_id, value)
        body = model.model_validate({
            "expected_row_version": expected_row_version, **fields,
        })
        body.normalized_values(kind)
        request = {
            "template_id": str(template_id), "version_id": str(version_id),
            "kind": kind, "item_id": str(UUID(str(item_id))),
            "body": body.model_dump(mode="json", exclude_unset=True),
            "version": item["version"], "source_language": item["source_language"],
            "expected_title": item["title"],
            "expected_description": item["description"],
            "definition_hash": digest(candidate),
            "section_count": item["section_count"],
            "question_count": item["question_count"],
        }
        return {
            **result, "request": request, "field": "label",
            "original": original, "value": value,
        }

    async def prepare_flag(
        self, *, template_id, version_id, expected_row_version,
        expected_definition_hash, kind, item_id, field, **binding,
    ):
        allowed = {"active", "required"} if kind == "QUESTION" else {"active"}
        if kind not in ("SECTION", "QUESTION") or field not in allowed:
            raise ValueError("Unsupported Boolean field.")
        model = body_model(kind)
        model(expected_row_version=expected_row_version)
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
        original = locate(item, kind, item_id)[field]
        if type(original) is not bool:
            raise problem()
        value = not original
        fields, candidate, original = flag_fields(item, kind, item_id, field, value)
        body = model.model_validate({
            "expected_row_version": expected_row_version, **fields,
        })
        body.normalized_values(kind)
        target = locate(item, kind, item_id)
        request = {
            "template_id": str(template_id), "version_id": str(version_id),
            "kind": kind, "item_id": str(UUID(str(item_id))),
            "body": body.model_dump(mode="json", exclude_unset=True),
            "version": item["version"], "source_language": item["source_language"],
            "expected_title": item["title"],
            "expected_description": item["description"],
            "definition_hash": digest(candidate),
            "section_count": item["section_count"],
            "question_count": item["question_count"],
        }
        return {
            **result, "request": request, "field": field,
            "original": original, "value": value,
            "label": item["definition"]["labels"][item["source_language"]][target["label_key"]],
        }

    async def prepare_order(
        self, *, template_id, version_id, expected_row_version,
        expected_definition_hash, kind, item_id, value, **binding,
    ):
        value = order_value(value)
        model = body_model(kind)
        model(expected_row_version=expected_row_version)
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
        fields, candidate, original = order_fields(item, kind, item_id, value)
        body = model.model_validate({
            "expected_row_version": expected_row_version, **fields,
        })
        body.normalized_values(kind)
        request = {
            "template_id": str(template_id), "version_id": str(version_id),
            "kind": kind, "item_id": str(UUID(str(item_id))),
            "body": body.model_dump(mode="json", exclude_unset=True),
            "version": item["version"], "source_language": item["source_language"],
            "expected_title": item["title"],
            "expected_description": item["description"],
            "definition_hash": digest(candidate),
            "section_count": item["section_count"],
            "question_count": item["question_count"],
        }
        return {
            **result, "request": request, "field": "sort_order",
            "original": original, "value": value,
        }

    async def write_label(self, *, request, **binding):
        names = {
            "template_id", "version_id", "kind", "item_id", "body",
            "version", "source_language", "expected_title",
            "expected_description", "definition_hash",
            "section_count", "question_count", "key",
        }
        if not isinstance(request, dict) or set(request) != names:
            raise ValueError("Invalid label operation.")
        identities = {}
        for name in ("template_id", "version_id", "item_id"):
            identities[name] = UUID(request[name])
            if str(identities[name]) != request[name]:
                raise ValueError("Noncanonical label operation identity.")
        kind, key = request["kind"], request["key"]
        model = body_model(kind)
        raw = request["body"]
        if (
            not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
            or not isinstance(raw, dict)
            or not {"expected_row_version", "fields"}.issubset(raw)
            or not set(raw).issubset({"expected_row_version", "fields", "labels"})
            or not isinstance(raw["fields"], dict)
            or not editable_fields(kind, raw)
            or any(
                type(request[name]) is not int or request[name] < 1
                for name in ("version", "section_count", "question_count")
            )
            or not isinstance(request["definition_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", request["definition_hash"]) is None
        ):
            raise ValueError("Invalid label operation metadata.")
        body = model.model_validate(raw)
        body.normalized_values(kind)
        session, context = await self.context(**binding)
        segment = "sections" if kind == "SECTION" else "questions"
        result = await self.entry.api.construction._request(
            "PATCH",
            PATH + "/" + request["template_id"] + "/drafts/"
            + request["version_id"] + "/" + segment + "/" + request["item_id"],
            session=session,
            json_body=body.model_dump(mode="json", exclude_unset=True),
            idempotency_key=key,
        )
        try:
            counters = (
                "template_row_version", "version", "version_row_version",
                "definition_schema_version", "section_count", "question_count",
            )
            if (
                not isinstance(result.data, dict)
                or not isinstance(result.data.get("draft"), dict)
                or any(type(result.data["draft"].get(name)) is not int for name in counters)
            ):
                raise ValueError("Label response counters differ.")
            receipt = TemplateDraftItemWriteData.model_validate(result.data)
            item = receipt.draft
            if (
                result.meta != {} or receipt.kind != kind
                or receipt.item_id != identities["item_id"]
                or item.tenant_id != session.tenant_id
                or item.template_id != identities["template_id"]
                or item.version_id != identities["version_id"]
                or item.version != request["version"]
                or item.version_row_version != body.expected_row_version + int(item.changed)
                or item.source_language != request["source_language"]
                or item.title != request["expected_title"]
                or item.description != request["expected_description"]
                or item.definition_hash != request["definition_hash"]
                or item.section_count != request["section_count"]
                or item.question_count != request["question_count"]
            ):
                raise ValueError("Label write receipt differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}
