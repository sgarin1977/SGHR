"""Keyed sparse draft-header edits using the shared encrypted receipt runner."""
from copy import deepcopy
from inspect import signature
from uuid import UUID

from services.api_idempotency import build_api_idempotency_service
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError as Validation,
    _version,
)
from services.construction.construction_template_draft_content import (
    ConstructionTemplateDraftContentService,
    HEADER_FIELDS,
    build_construction_template_draft_content_service,
)
from services.construction.construction_template_draft_write_receipts import (
    draft_write_result, encode_draft_write, decode_draft_write,
)
from services.construction.construction_template_keyed_workflow import (
    ConstructionTemplateKeyedWorkflow,
)

OPERATION = "construction.measurement_template.edit_draft_header"


def normalize_header_fields(fields):
    if not isinstance(fields, dict) or not set(fields).issubset(HEADER_FIELDS):
        raise Validation("Only supported draft header fields are accepted.")
    for name in ("source_language", "title_key"):
        if name in fields and not isinstance(fields[name], str):
            raise Validation("An explicit string is required.")
    if (
        "description_key" in fields
        and fields["description_key"] is not None
        and not isinstance(fields["description_key"], str)
    ):
        raise Validation("Description key must be a string or null.")
    if "labels" in fields:
        labels = fields["labels"]
        if not isinstance(labels, dict):
            raise Validation("Labels must be a language dictionary.")
        for language, entries in labels.items():
            if (
                not isinstance(language, str) or not isinstance(entries, dict)
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in entries.items())
            ):
                raise Validation("Labels must contain string keys and values.")
    return deepcopy(fields)


def encode_result(result):
    return encode_draft_write(result, operation=OPERATION)


def decode_result(payload, *, actor, request):
    return decode_draft_write(
        payload, operation=OPERATION, actor=actor, request=request)


class ConstructionTemplateDraftHeaderWorkflow:
    def __init__(self, *, session, content_service, idempotency_service):
        if (
            not isinstance(content_service, ConstructionTemplateDraftContentService)
            or content_service.session is not session
            or "manage_transaction" not in signature(
                content_service.edit_header).parameters
        ):
            raise ValueError("Header workflow requires its shared-session domain service.")
        self.session, self.service = session, content_service
        self.authorizer = content_service.authorizer
        self.idempotency = idempotency_service
        self.runner = ConstructionTemplateKeyedWorkflow(
            session=session, authorizer=self.authorizer,
            idempotency_service=idempotency_service,
            operation=OPERATION, response_status=200,
            encode_result=encode_result, decode_result=decode_result)

    async def edit_header(
        self, *, actor, template_id, version_id, expected_row_version,
        fields, idempotency_key, trace_id=None,
    ):
        if not isinstance(template_id, UUID) or not isinstance(version_id, UUID):
            raise Validation("Template and draft UUIDs are required.")
        expected = _version(expected_row_version)
        requested = normalize_header_fields(fields)
        request = {
            "template_id": str(template_id),
            "version_id": str(version_id),
            "expected_row_version": expected,
            "fields": requested,
        }

        async def perform():
            result = await self.service.edit_header(
                actor=actor, template_id=template_id, version_id=version_id,
                expected_row_version=expected, fields=deepcopy(requested),
                trace_id=trace_id, manage_transaction=False)
            return draft_write_result(result)

        return await self.runner.execute(
            actor=actor, idempotency_key=idempotency_key,
            request=request, perform=perform)


def build_construction_template_draft_header_workflow(
    *, session, idempotency_encryption_key, now_provider=None,
):
    return ConstructionTemplateDraftHeaderWorkflow(
        session=session,
        content_service=build_construction_template_draft_content_service(
            session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(
            session, encryption_key=idempotency_encryption_key))
