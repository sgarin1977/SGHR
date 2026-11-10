"""Idempotent draft creation; original receipt remains historical metadata."""
from dataclasses import asdict
from datetime import datetime, timezone
from inspect import signature
import re
from uuid import UUID

from services.api_idempotency import build_api_idempotency_service
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    _version,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftService,
    ConstructionTemplateDraftResult,
    build_construction_template_draft_service,
)
from services.construction.construction_template_keyed_workflow import (
    ConstructionTemplateKeyedWorkflow,
    ConstructionTemplateOperationInProgressError,
)

OPERATION = "construction.measurement_template.create_draft"
UUID_FIELDS = (
    "tenant_id", "template_id", "base_version_id", "version_id", "created_by",
)
INTEGER_FIELDS = (
    "template_row_version", "version", "version_row_version",
    "section_count", "question_count",
)


def _invalid():
    return Operation("Template draft creation receipt is invalid.")


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


def _time(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None or value.utcoffset() is None
    ):
        raise _invalid()
    return value.astimezone(timezone.utc)


def encode_result(result):
    if not isinstance(result, ConstructionTemplateDraftResult):
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
    values["created_at"] = _time(values["created_at"]).isoformat()
    return {"schema_version": 1, "operation": OPERATION, "result": values}


def decode_result(payload, *, actor, request):
    try:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "operation", "result"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["operation"] != OPERATION
            or not isinstance(payload["result"], dict)
            or set(payload["result"]) != set(
                ConstructionTemplateDraftResult.__dataclass_fields__)
        ):
            raise _invalid()
        values = dict(payload["result"])
        for name in UUID_FIELDS:
            values[name] = _uuid(values[name])
        if values["source_starter_version_id"] is not None:
            values["source_starter_version_id"] = _uuid(
                values["source_starter_version_id"])
        if not isinstance(values["created_at"], str):
            raise _invalid()
        values["created_at"] = _time(datetime.fromisoformat(values["created_at"]))
        if any(type(values[name]) is not int for name in INTEGER_FIELDS):
            raise _invalid()
        if (
            values["tenant_id"] != actor.tenant_id
            or values["created_by"] != actor.user_id
            or values["template_id"] != UUID(request["template_id"])
            or values["base_version_id"] != UUID(request["base_version_id"])
            or values["template_row_version"] != request["expected_template_row_version"]
            or not 1 <= values["template_row_version"] <= 2**63 - 1
            or values["version_id"] in (
                values["template_id"], values["base_version_id"],
                actor.tenant_id, actor.user_id)
            or not 2 <= values["version"] <= 2**31 - 1
            or values["version_row_version"] != 1
            or values["status"] != "DRAFT"
            or not 1 <= values["section_count"] <= 100
            or not values["section_count"] <= values["question_count"] <= 2000
            or not isinstance(values["source_language"], str)
            or len(values["source_language"]) > 10
            or re.fullmatch(
                r"[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,6})?",
                values["source_language"]) is None
        ):
            raise _invalid()
        result = ConstructionTemplateDraftResult(**values)
        if encode_result(result) != payload:
            raise _invalid()
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _invalid() from None


class ConstructionTemplateDraftWorkflow:
    def __init__(self, *, session, draft_service, idempotency_service):
        if (
            not isinstance(draft_service, ConstructionTemplateDraftService)
            or draft_service.session is not session
            or "manage_transaction" not in signature(
                draft_service.create_draft).parameters
        ):
            raise ValueError("Draft workflow requires its shared-session domain service.")
        self.session, self.service = session, draft_service
        self.authorizer = draft_service.authorizer
        self.idempotency = idempotency_service
        self.runner = ConstructionTemplateKeyedWorkflow(
            session=session, authorizer=self.authorizer,
            idempotency_service=idempotency_service,
            operation=OPERATION, response_status=201,
            encode_result=encode_result, decode_result=decode_result)

    async def create_draft(
        self, *, actor, template_id, base_version_id,
        expected_template_row_version, idempotency_key, trace_id=None,
    ):
        if not isinstance(template_id, UUID) or not isinstance(base_version_id, UUID):
            raise Validation("Template and base version UUIDs are required.")
        expected = _version(expected_template_row_version)
        request = {
            "template_id": str(template_id),
            "base_version_id": str(base_version_id),
            "expected_template_row_version": expected,
        }

        async def perform():
            return await self.service.create_draft(
                actor=actor, template_id=template_id,
                base_version_id=base_version_id,
                expected_template_row_version=expected,
                trace_id=trace_id, manage_transaction=False)

        return await self.runner.execute(
            actor=actor, idempotency_key=idempotency_key,
            request=request, perform=perform)


def build_construction_template_draft_workflow(
    *, session, idempotency_encryption_key, now_provider=None,
):
    return ConstructionTemplateDraftWorkflow(
        session=session,
        draft_service=build_construction_template_draft_service(
            session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(
            session, encryption_key=idempotency_encryption_key))
