"""Immutable metadata receipts for versioned draft edits; no current-state lookup."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from uuid import UUID

from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
)
from services.construction.construction_template_draft_content import (
    ConstructionTemplateDraftEditResult,
    ConstructionTemplateDraftState,
)
from services.construction.construction_organization_template_definitions import (
    validate_organization_template_definition,
)


@dataclass(frozen=True)
class ConstructionTemplateDraftWriteResult:
    tenant_id: UUID
    template_id: UUID
    code: str
    active: bool
    template_row_version: int
    version_id: UUID
    version: int
    version_row_version: int
    status: str
    source_starter_version_id: UUID | None
    definition_schema_version: int
    source_language: str
    title: str
    description: str | None
    definition_hash: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    section_count: int
    question_count: int
    changed: bool


UUID_FIELDS = ("tenant_id", "template_id", "version_id", "created_by")
TIME_FIELDS = ("created_at", "updated_at")
INTEGER_FIELDS = (
    "template_row_version", "version", "version_row_version",
    "definition_schema_version", "section_count", "question_count",
)
DERIVED_FIELDS = {
    "definition_hash", "section_count", "question_count", "changed",
}


def _invalid():
    return Operation("Draft edit receipt is invalid.")


def _time(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None or value.utcoffset() is None
    ):
        raise _invalid()
    return value.astimezone(timezone.utc)


def _uuid(value):
    if not isinstance(value, str):
        raise _invalid()
    try:
        parsed = UUID(value)
    except ValueError:
        raise _invalid() from None
    if str(parsed) != value:
        raise _invalid()
    return parsed


def _text(value):
    return (
        isinstance(value, str) and 1 <= len(value) <= 120
        and value == value.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def draft_write_result(result):
    if (
        not isinstance(result, ConstructionTemplateDraftEditResult)
        or type(result.changed) is not bool
        or not isinstance(result.draft, ConstructionTemplateDraftState)
        or not isinstance(result.draft.sections, tuple)
    ):
        raise _invalid()
    state = result.draft
    checked = validate_organization_template_definition(
        state.definition.canonical_json, source_language=state.source_language)
    if checked != state.definition:
        raise _invalid()
    payload = checked.payload()
    section_count = len(state.sections)
    question_count = sum(len(section.questions) for section in state.sections)
    if (
        section_count != len(payload["sections"])
        or question_count != sum(len(section["questions"]) for section in payload["sections"])
    ):
        raise _invalid()
    return ConstructionTemplateDraftWriteResult(
        **{
            name: getattr(state, name)
            for name in ConstructionTemplateDraftWriteResult.__dataclass_fields__
            if name not in DERIVED_FIELDS
        },
        definition_hash=checked.content_hash,
        section_count=section_count,
        question_count=question_count,
        changed=result.changed)


def encode_draft_write(result, *, operation):
    if not isinstance(result, ConstructionTemplateDraftWriteResult):
        raise _invalid()
    values = asdict(result)
    for name in UUID_FIELDS:
        if not isinstance(values[name], UUID):
            raise _invalid()
        values[name] = str(values[name])
    origin = values["source_starter_version_id"]
    if origin is not None:
        if not isinstance(origin, UUID):
            raise _invalid()
        values["source_starter_version_id"] = str(origin)
    for name in TIME_FIELDS:
        values[name] = _time(values[name]).isoformat()
    return {"schema_version": 1, "operation": operation, "result": values}


def decode_draft_write(payload, *, operation, actor, request):
    try:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "operation", "result"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["operation"] != operation
            or not isinstance(payload["result"], dict)
            or set(payload["result"]) != set(
                ConstructionTemplateDraftWriteResult.__dataclass_fields__)
        ):
            raise _invalid()
        values = dict(payload["result"])
        for name in UUID_FIELDS:
            values[name] = _uuid(values[name])
        if values["source_starter_version_id"] is not None:
            values["source_starter_version_id"] = _uuid(
                values["source_starter_version_id"])
        for name in TIME_FIELDS:
            if not isinstance(values[name], str):
                raise _invalid()
            values[name] = _time(datetime.fromisoformat(values[name]))
        if (
            any(type(values[name]) is not int for name in INTEGER_FIELDS)
            or type(values["changed"]) is not bool
            or type(values["active"]) is not bool
        ):
            raise _invalid()
        if (
            values["tenant_id"] != actor.tenant_id
            or values["template_id"] != UUID(request["template_id"])
            or values["version_id"] != UUID(request["version_id"])
            or values["template_id"] == values["version_id"]
            or values["version_row_version"] != (
                request["expected_row_version"] + int(values["changed"]))
            or not 1 <= values["template_row_version"] <= 2**63 - 1
            or not 1 <= values["version_row_version"] <= 2**63 - 1
            or not 1 <= values["version"] <= 2**31 - 1
            or values["definition_schema_version"] != 1
            or values["status"] != "DRAFT"
            or not 1 <= values["section_count"] <= 100
            or not values["section_count"] <= values["question_count"] <= 2000
            or not isinstance(values["code"], str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", values["code"]) is None
            or not isinstance(values["source_language"], str)
            or len(values["source_language"]) > 10
            or re.fullmatch(
                r"[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,6})?",
                values["source_language"]) is None
            or not _text(values["title"])
            or values["description"] is not None and not _text(values["description"])
            or not isinstance(values["definition_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", values["definition_hash"]) is None
        ):
            raise _invalid()
        # The draft author can differ from the editor. The encrypted receipt's
        # principal independently binds and authorizes the current editor.
        result = ConstructionTemplateDraftWriteResult(**values)
        if encode_draft_write(result, operation=operation) != payload:
            raise _invalid()
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _invalid() from None
