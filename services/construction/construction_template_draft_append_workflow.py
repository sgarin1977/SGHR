"""Keyed draft additions with stable server IDs and encrypted receipts."""
from copy import deepcopy
from dataclasses import dataclass
from inspect import signature
import json
from uuid import UUID

from services.api_idempotency import build_api_idempotency_service
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    _version,
)
from services.construction.construction_starter_template_definitions import (
    _walk, MAX_BYTES,
)
from services.construction.construction_organization_templates import SECTION_FIELDS
from services.construction.construction_template_draft_append import (
    ConstructionTemplateDraftAppendService,
    ConstructionTemplateDraftAppendResult,
    UNSET,
    build_construction_template_draft_append_service,
)
from services.construction.construction_template_draft_content import (
    ConstructionTemplateDraftEditResult,
)
from services.construction.construction_template_draft_item_workflow import (
    normalize_item_request,
)
from services.construction.construction_template_draft_write_receipts import (
    ConstructionTemplateDraftWriteResult,
    draft_write_result, encode_draft_write, decode_draft_write,
)
from services.construction.construction_template_keyed_workflow import (
    ConstructionTemplateKeyedWorkflow,
)

OPERATION = "construction.measurement_template.append_draft_item"


@dataclass(frozen=True)
class ConstructionTemplateDraftAppendWriteResult:
    draft: ConstructionTemplateDraftWriteResult
    kind: str
    parent_section_id: UUID | None
    section_ids: tuple[UUID, ...]
    question_ids: tuple[UUID, ...]


def _invalid():
    return Operation("Draft addition receipt is invalid.")


def normalize_append_request(*, kind, body, labels=UNSET):
    try:
        if not isinstance(body, dict):
            raise ValueError()
        if kind == "QUESTION":
            normalized, _ = normalize_item_request(
                kind="QUESTION", fields=body)
        elif kind == "SECTION":
            if not set(body).issubset(set(SECTION_FIELDS) | {"questions"}):
                raise ValueError()
            questions = body.get("questions")
            if not isinstance(questions, list) or not 1 <= len(questions) <= 2000:
                raise ValueError()
            normalized, _ = normalize_item_request(
                kind="SECTION",
                fields={name: value for name, value in body.items()
                        if name != "questions"})
            normalized["questions"] = [
                normalize_item_request(kind="QUESTION", fields=question)[0]
                for question in questions
            ]
        else:
            raise ValueError()

        normalized_labels = UNSET
        bag = {"body": normalized}
        if labels is not UNSET:
            if not isinstance(labels, dict):
                raise ValueError()
            normalized_labels = deepcopy(labels)
            bag["labels"] = normalized_labels
        _walk(bag)
        encoded = json.dumps(
            bag, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        raise Validation(
            "Draft addition must contain bounded canonical JSON data."
        ) from None
    return normalized, normalized_labels


def _uuid(value):
    if not isinstance(value, str):
        raise _invalid()
    try:
        result = UUID(value)
    except ValueError:
        raise _invalid() from None
    if str(result) != value:
        raise _invalid()
    return result


def _encode_ids(values):
    if not isinstance(values, tuple) or any(
        not isinstance(value, UUID) for value in values
    ):
        raise _invalid()
    return [str(value) for value in values]


def _decode_ids(values, *, maximum):
    if not isinstance(values, list) or len(values) > maximum:
        raise _invalid()
    result = tuple(_uuid(value) for value in values)
    if len(set(result)) != len(result):
        raise _invalid()
    return result


def encode_result(result):
    if (
        not isinstance(result, ConstructionTemplateDraftAppendWriteResult)
        or result.kind not in ("SECTION", "QUESTION")
        or result.parent_section_id is not None
        and not isinstance(result.parent_section_id, UUID)
    ):
        raise _invalid()
    metadata = encode_draft_write(result.draft, operation=OPERATION)
    return {
        "schema_version": 1,
        "operation": OPERATION,
        "result": {
            "draft": metadata["result"],
            "kind": result.kind,
            "parent_section_id": (
                None if result.parent_section_id is None
                else str(result.parent_section_id)),
            "section_ids": _encode_ids(result.section_ids),
            "question_ids": _encode_ids(result.question_ids),
        },
    }


def decode_result(payload, *, actor, request):
    try:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "operation", "result"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["operation"] != OPERATION
            or not isinstance(payload["result"], dict)
            or set(payload["result"]) != {
                "draft", "kind", "parent_section_id",
                "section_ids", "question_ids",
            }
        ):
            raise _invalid()
        values = payload["result"]
        kind = values["kind"]
        if kind not in ("SECTION", "QUESTION") or kind != request["kind"]:
            raise _invalid()

        parent = (
            None if values["parent_section_id"] is None
            else _uuid(values["parent_section_id"]))
        expected_parent = (
            None if request["section_id"] is None
            else _uuid(request["section_id"]))
        if parent != expected_parent:
            raise _invalid()

        sections = _decode_ids(values["section_ids"], maximum=1)
        questions = _decode_ids(values["question_ids"], maximum=2000)
        if kind == "SECTION":
            expected_questions = len(request["body"]["questions"])
            if parent is not None or len(sections) != 1:
                raise _invalid()
        else:
            expected_questions = 1
            if parent is None or sections:
                raise _invalid()
        if not 1 <= expected_questions <= 2000 or len(questions) != expected_questions:
            raise _invalid()

        draft = decode_draft_write(
            {"schema_version": 1, "operation": OPERATION,
             "result": values["draft"]},
            operation=OPERATION, actor=actor, request=request)
        if (
            draft.changed is not True
            or draft.section_count < 1 + len(sections)
            or draft.question_count < 1 + len(questions)
        ):
            raise _invalid()

        generated = sections + questions
        reserved = {
            actor.user_id, draft.tenant_id, draft.template_id,
            draft.version_id, draft.created_by,
        }
        if draft.source_starter_version_id is not None:
            reserved.add(draft.source_starter_version_id)
        if parent is not None:
            reserved.add(parent)
        if (
            len(set(generated)) != len(generated)
            or set(generated).intersection(reserved)
        ):
            raise _invalid()

        result = ConstructionTemplateDraftAppendWriteResult(
            draft=draft, kind=kind, parent_section_id=parent,
            section_ids=sections, question_ids=questions)
        if encode_result(result) != payload:
            raise _invalid()
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _invalid() from None


class ConstructionTemplateDraftAppendWorkflow:
    def __init__(self, *, session, append_service, idempotency_service):
        if (
            not isinstance(append_service, ConstructionTemplateDraftAppendService)
            or append_service.session is not session
            or "manage_transaction" not in signature(
                append_service.append_item).parameters
        ):
            raise ValueError(
                "Append workflow requires its shared-session domain service.")
        self.session, self.service = session, append_service
        self.authorizer = append_service.authorizer
        self.idempotency = idempotency_service
        self.runner = ConstructionTemplateKeyedWorkflow(
            session=session, authorizer=self.authorizer,
            idempotency_service=idempotency_service,
            operation=OPERATION, response_status=201,
            encode_result=encode_result, decode_result=decode_result)

    async def append_item(
        self, *, actor, template_id, version_id, expected_row_version,
        kind, body, idempotency_key, section_id=None, labels=UNSET,
        trace_id=None,
    ):
        if (
            not isinstance(template_id, UUID)
            or not isinstance(version_id, UUID)
            or kind not in ("SECTION", "QUESTION")
            or kind == "SECTION" and section_id is not None
            or kind == "QUESTION" and not isinstance(section_id, UUID)
        ):
            raise Validation("Exact template, draft and parent context is required.")
        expected = _version(expected_row_version)
        normalized, normalized_labels = normalize_append_request(
            kind=kind, body=body, labels=labels)
        request = {
            "template_id": str(template_id),
            "version_id": str(version_id),
            "expected_row_version": expected,
            "kind": kind,
            "section_id": None if section_id is None else str(section_id),
            "body": normalized,
        }
        if normalized_labels is not UNSET:
            request["labels"] = normalized_labels

        async def perform():
            optional = (
                {} if normalized_labels is UNSET
                else {"labels": deepcopy(normalized_labels)})
            added = await self.service.append_item(
                actor=actor, template_id=template_id, version_id=version_id,
                expected_row_version=expected, kind=kind,
                body=deepcopy(normalized), section_id=section_id,
                trace_id=trace_id, manage_transaction=False,
                **optional)
            if not isinstance(added, ConstructionTemplateDraftAppendResult):
                raise _invalid()
            metadata = draft_write_result(
                ConstructionTemplateDraftEditResult(added.draft, True))
            return ConstructionTemplateDraftAppendWriteResult(
                draft=metadata, kind=kind, parent_section_id=section_id,
                section_ids=added.section_ids, question_ids=added.question_ids)

        return await self.runner.execute(
            actor=actor, idempotency_key=idempotency_key,
            request=request, perform=perform)


def build_construction_template_draft_append_workflow(
    *, session, idempotency_encryption_key, now_provider=None,
):
    return ConstructionTemplateDraftAppendWorkflow(
        session=session,
        append_service=build_construction_template_draft_append_service(
            session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(
            session, encryption_key=idempotency_encryption_key))
