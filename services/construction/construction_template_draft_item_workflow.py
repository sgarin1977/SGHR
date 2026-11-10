"""Keyed section/question edits using canonical JSON and encrypted receipts."""
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
from services.construction.construction_units import UnitCode
from services.construction.construction_starter_template_definitions import (
    _walk, MAX_BYTES,
)
from services.construction.construction_organization_templates import (
    SECTION_FIELDS, QUESTION_FIELDS, UUID_QUESTION_FIELDS,
)
from services.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemService, UNSET,
    build_construction_template_draft_item_service,
)
from services.construction.construction_template_draft_write_receipts import (
    ConstructionTemplateDraftWriteResult,
    draft_write_result, encode_draft_write, decode_draft_write,
)
from services.construction.construction_template_keyed_workflow import (
    ConstructionTemplateKeyedWorkflow,
)

OPERATION = "construction.measurement_template.edit_draft_item"


@dataclass(frozen=True)
class ConstructionTemplateDraftItemWriteResult:
    draft: ConstructionTemplateDraftWriteResult
    kind: str
    item_id: UUID


def _invalid():
    return Operation("Draft item receipt is invalid.")


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


def normalize_item_request(*, kind, fields, labels=UNSET):
    allowed = (
        set(SECTION_FIELDS) if kind == "SECTION"
        else set(QUESTION_FIELDS) if kind == "QUESTION"
        else None
    )
    if (
        allowed is None or not isinstance(fields, dict)
        or not set(fields).issubset(allowed)
    ):
        raise Validation("Invalid draft item fields.")
    requested = deepcopy(fields)
    if "sort_order" in requested and (
        type(requested["sort_order"]) is not int
        or not 0 <= requested["sort_order"] <= 2**31 - 1
    ):
        raise Validation("Item order must fit the storage range.")
    if kind == "QUESTION":
        if isinstance(requested.get("unit_code"), UnitCode):
            requested["unit_code"] = requested["unit_code"].value
        for name in UUID_QUESTION_FIELDS:
            if name in requested and requested[name] is not None:
                value = requested[name]
                if not isinstance(value, (str, UUID)):
                    raise Validation("Work-type identity must be a UUID.")
                try:
                    requested[name] = str(UUID(str(value)))
                except ValueError:
                    raise Validation("Work-type identity must be a UUID.") from None

    normalized_labels = UNSET
    bag = {"fields": requested}
    if labels is not UNSET:
        if not isinstance(labels, dict):
            raise Validation("Labels must be a language dictionary.")
        normalized_labels = deepcopy(labels)
        bag["labels"] = normalized_labels
    try:
        _walk(bag)
        encoded = json.dumps(
            bag, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        raise Validation("Draft item update must contain bounded exact JSON data.") from None
    return requested, normalized_labels


def encode_result(result):
    if (
        not isinstance(result, ConstructionTemplateDraftItemWriteResult)
        or result.kind not in ("SECTION", "QUESTION")
        or not isinstance(result.item_id, UUID)
    ):
        raise _invalid()
    metadata = encode_draft_write(result.draft, operation=OPERATION)
    return {
        "schema_version": 1, "operation": OPERATION,
        "result": {
            "draft": metadata["result"],
            "kind": result.kind,
            "item_id": str(result.item_id),
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
            or set(payload["result"]) != {"draft", "kind", "item_id"}
        ):
            raise _invalid()
        body = payload["result"]
        if body["kind"] not in ("SECTION", "QUESTION") or body["kind"] != request["kind"]:
            raise _invalid()
        item_id = _uuid(body["item_id"])
        if item_id != UUID(request["item_id"]):
            raise _invalid()
        draft = decode_draft_write(
            {"schema_version": 1, "operation": OPERATION, "result": body["draft"]},
            operation=OPERATION, actor=actor, request=request)
        result = ConstructionTemplateDraftItemWriteResult(
            draft=draft, kind=body["kind"], item_id=item_id)
        if encode_result(result) != payload:
            raise _invalid()
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _invalid() from None


class ConstructionTemplateDraftItemWorkflow:
    def __init__(self, *, session, item_service, idempotency_service):
        if (
            not isinstance(item_service, ConstructionTemplateDraftItemService)
            or item_service.session is not session
            or "manage_transaction" not in signature(
                item_service.edit_item).parameters
        ):
            raise ValueError("Item workflow requires its shared-session domain service.")
        self.session, self.service = session, item_service
        self.authorizer = item_service.authorizer
        self.idempotency = idempotency_service
        self.runner = ConstructionTemplateKeyedWorkflow(
            session=session, authorizer=self.authorizer,
            idempotency_service=idempotency_service,
            operation=OPERATION, response_status=200,
            encode_result=encode_result, decode_result=decode_result)

    async def edit_item(
        self, *, actor, template_id, version_id, kind, item_id,
        expected_row_version, fields, idempotency_key, labels=UNSET,
        trace_id=None,
    ):
        if not all(isinstance(value, UUID) for value in (
            template_id, version_id, item_id,
        )):
            raise Validation("Template, draft and item UUIDs are required.")
        expected = _version(expected_row_version)
        requested, normalized_labels = normalize_item_request(
            kind=kind, fields=fields, labels=labels)
        request = {
            "template_id": str(template_id),
            "version_id": str(version_id),
            "kind": kind,
            "item_id": str(item_id),
            "expected_row_version": expected,
            "fields": requested,
        }
        if normalized_labels is not UNSET:
            request["labels"] = normalized_labels

        async def perform():
            optional = (
                {} if normalized_labels is UNSET
                else {"labels": deepcopy(normalized_labels)}
            )
            edited = await self.service.edit_item(
                actor=actor, template_id=template_id, version_id=version_id,
                kind=kind, item_id=item_id, expected_row_version=expected,
                fields=deepcopy(requested), trace_id=trace_id,
                manage_transaction=False, **optional)
            return ConstructionTemplateDraftItemWriteResult(
                draft=draft_write_result(edited), kind=kind, item_id=item_id)

        return await self.runner.execute(
            actor=actor, idempotency_key=idempotency_key,
            request=request, perform=perform)


def build_construction_template_draft_item_workflow(
    *, session, idempotency_encryption_key, now_provider=None,
):
    return ConstructionTemplateDraftItemWorkflow(
        session=session,
        item_service=build_construction_template_draft_item_service(
            session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(
            session, encryption_key=idempotency_encryption_key))
