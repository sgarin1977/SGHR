"""Atomic publication and shared encrypted Platform idempotency receipts."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from inspect import signature
import re
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyReservation,
    ApiIdempotencyKeyReusedError,
    build_api_idempotency_service, hash_request_payload,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingStateConflictError as Conflict,
    _version,
)
from services.construction.construction_organization_templates import (
    OrganizationTemplateSummary,
    OrganizationTemplateVersionState,
)
from services.construction.construction_template_publication import (
    ConstructionTemplatePublicationService,
    build_construction_template_publication_service,
)

OPERATION = "construction.measurement_template.publish_version"
RESPONSE_STATUS = 200
RECORD_FIELDS = (
    "id", "tenant_id", "principal_type", "principal_id", "operation",
    "key_hash", "request_hash", "created_at", "expires_at",
    "status", "response_status", "response_ciphertext", "completed_at",
)
CONTEXT_FIELDS = RECORD_FIELDS[:9]
UUID_FIELDS = ("tenant_id", "template_id", "version_id", "created_by")
TIME_FIELDS = ("created_at", "updated_at", "published_at")
INTEGER_FIELDS = (
    "template_row_version", "version", "version_row_version",
    "definition_schema_version", "section_count", "question_count",
)


class ConstructionTemplatePublicationKeyReusedError(
    ApiIdempotencyKeyReusedError, Conflict,
):
    pass


class ConstructionTemplatePublicationInProgressError(Conflict):
    status_code = 409
    code = "idempotency_in_progress"


@dataclass(frozen=True)
class ConstructionTemplatePublicationResult(OrganizationTemplateSummary):
    section_count: int
    question_count: int


def _invalid():
    return Operation("Template publication receipt is invalid.")


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
        result = UUID(value)
    except ValueError:
        raise _invalid() from None
    if str(result) != value:
        raise _invalid()
    return result


def _text(value):
    return (
        isinstance(value, str) and 1 <= len(value) <= 120
        and value == value.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _result(state):
    if (
        not isinstance(state, OrganizationTemplateVersionState)
        or state.definition.content_hash != state.content_hash
        or not isinstance(state.sections, tuple)
    ):
        raise _invalid()
    return ConstructionTemplatePublicationResult(
        **{
            name: getattr(state, name)
            for name in OrganizationTemplateSummary.__dataclass_fields__
        },
        section_count=len(state.sections),
        question_count=sum(len(section.questions) for section in state.sections),
    )


def encode_result(result):
    if not isinstance(result, ConstructionTemplatePublicationResult):
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
                ConstructionTemplatePublicationResult.__dataclass_fields__)
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
        if any(type(values[name]) is not int for name in INTEGER_FIELDS):
            raise _invalid()
        if (
            values["tenant_id"] != actor.tenant_id
            or values["template_id"] != UUID(request["template_id"])
            or values["version_id"] != UUID(request["version_id"])
            or values["template_id"] == values["version_id"]
            or values["version_row_version"] != request["expected_row_version"] + 1
            or not 1 <= values["template_row_version"] <= 2**63 - 1
            or not 1 <= values["version_row_version"] <= 2**63 - 1
            or not 1 <= values["version"] <= 2**31 - 1
            or values["definition_schema_version"] != 1
            or type(values["active"]) is not bool
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
            or not isinstance(values["content_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", values["content_hash"]) is None
            or values["updated_at"] != values["published_at"]
        ):
            raise _invalid()
        # created_by is the draft author. The receipt principal independently
        # identifies and authorizes the user performing publication.
        result = ConstructionTemplatePublicationResult(**values)
        if encode_result(result) != payload:
            raise _invalid()
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _invalid() from None


class ConstructionTemplatePublicationWorkflow:
    def __init__(self, *, session, publication_service, idempotency_service):
        if (
            not isinstance(publication_service, ConstructionTemplatePublicationService)
            or publication_service.session is not session
            or "manage_transaction" not in signature(
                publication_service.publish_draft).parameters
            or not isinstance(idempotency_service, ApiIdempotencyService)
            or getattr(idempotency_service.repository, "session", None) is not session
            or any(not callable(getattr(
                idempotency_service.repository, name, None))
                for name in ("reserve_record", "get_active_record_for_update"))
            or any(not callable(getattr(
                idempotency_service.response_codec, name, None))
                for name in ("encrypt", "decrypt"))
        ):
            raise ValueError(
                "Publication workflow requires shared-session encrypted receipts.")
        self.session = session
        self.service = publication_service
        self.authorizer = publication_service.authorizer
        self.idempotency = idempotency_service

    def _record(self, record, *, actor, key, request):
        data = {name: getattr(record, name, None) for name in RECORD_FIELDS}
        now = self.authorizer._now()
        if (
            not isinstance(data["id"], UUID)
            or data["tenant_id"] != actor.tenant_id
            or data["principal_type"] != "user"
            or data["principal_id"] != actor.user_id
            or data["operation"] != OPERATION
            or data["key_hash"] != sha256(key.encode("utf-8")).hexdigest()
            or data["request_hash"] != hash_request_payload(request)
            or data["status"] not in ("processing", "completed")
        ):
            raise _invalid()
        data["created_at"] = _time(data["created_at"])
        data["expires_at"] = _time(data["expires_at"])
        if (
            data["created_at"] > now or data["expires_at"] <= now
            or data["expires_at"] <= data["created_at"]
        ):
            raise _invalid()
        return data

    @staticmethod
    def _processing(record):
        if (
            record["status"] != "processing"
            or record["response_status"] is not None
            or record["response_ciphertext"] is not None
            or record["completed_at"] is not None
        ):
            raise _invalid()

    def _completed(self, record, *, payload):
        if (
            record["status"] != "completed"
            or type(record["response_status"]) is not int
            or record["response_status"] != RESPONSE_STATUS
            or not isinstance(record["response_ciphertext"], bytes)
            or not record["response_ciphertext"]
        ):
            raise _invalid()
        completed_at = _time(record["completed_at"])
        if not record["created_at"] <= completed_at <= self.authorizer._now():
            raise _invalid()
        decrypted = self.idempotency.response_codec.decrypt(
            record["response_ciphertext"])
        if hash_request_payload(decrypted) != hash_request_payload(payload):
            raise _invalid()

    async def _reload(self, *, actor, key, request, transaction, original):
        record = await self.idempotency.repository.get_active_record_for_update(
            tenant_id=actor.tenant_id, principal_type="user",
            principal_id=actor.user_id, operation=OPERATION,
            key_hash=original["key_hash"], now=self.authorizer._now())
        self.authorizer._same_transaction(transaction)
        data = self._record(record, actor=actor, key=key, request=request)
        if any(data[name] != original[name] for name in CONTEXT_FIELDS):
            raise _invalid()
        return record, data

    async def publish_draft(
        self, *, actor, template_id, version_id, expected_row_version,
        idempotency_key, trace_id=None,
    ):
        if (
            not isinstance(template_id, UUID) or not isinstance(version_id, UUID)
            or not isinstance(idempotency_key, str)
            or not idempotency_key.strip() or len(idempotency_key) > 255
        ):
            raise Validation("Invalid keyed publication request.")
        expected = _version(expected_row_version)
        request = {
            "template_id": str(template_id), "version_id": str(version_id),
            "expected_row_version": expected,
        }
        async with self.authorizer._operation(actor, write=True) as transaction:
            try:
                reservation = await self.idempotency.reserve(
                    tenant_id=actor.tenant_id, principal_type="user",
                    principal_id=actor.user_id, operation=OPERATION,
                    idempotency_key=idempotency_key, payload=request,
                    now=self.authorizer._now())
            except ApiIdempotencyKeyReusedError:
                raise ConstructionTemplatePublicationKeyReusedError(
                    "Idempotency key was reused with a different publication request."
                ) from None
            self.authorizer._same_transaction(transaction)
            if (
                not isinstance(reservation, ApiIdempotencyReservation)
                or type(reservation.is_replay) is not bool
            ):
                raise _invalid()
            original = self._record(
                reservation.record, actor=actor, key=idempotency_key, request=request)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)

            if reservation.is_replay:
                if original["status"] == "processing":
                    self._processing(original)
                    if (
                        reservation.response_status is not None
                        or reservation.response_payload is not None
                    ):
                        raise _invalid()
                    raise ConstructionTemplatePublicationInProgressError(
                        "Template publication is still processing.")
                if (
                    type(reservation.response_status) is not int
                    or reservation.response_status != RESPONSE_STATUS
                ):
                    raise _invalid()
                self._completed(original, payload=reservation.response_payload)
                result = decode_result(
                    reservation.response_payload, actor=actor, request=request)
            else:
                self._processing(original)
                if (
                    reservation.response_status is not None
                    or reservation.response_payload is not None
                ):
                    raise _invalid()
                published = await self.service.publish_draft(
                    actor=actor, template_id=template_id, version_id=version_id,
                    expected_row_version=expected, trace_id=trace_id,
                    manage_transaction=False)
                self.authorizer._same_transaction(transaction)
                payload = encode_result(_result(published))
                result = decode_result(payload, actor=actor, request=request)

                fresh, processing = await self._reload(
                    actor=actor, key=idempotency_key, request=request,
                    transaction=transaction, original=original)
                self._processing(processing)
                await self.idempotency.complete(
                    reservation=replace(reservation, record=fresh),
                    response_status=RESPONSE_STATUS, response_payload=payload,
                    now=self.authorizer._now())
                self.authorizer._same_transaction(transaction)
                _, completed = await self._reload(
                    actor=actor, key=idempotency_key, request=request,
                    transaction=transaction, original=original)
                self._completed(completed, payload=payload)

            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            await self.session.commit()
            return result


def build_construction_template_publication_workflow(
    *, session, idempotency_encryption_key,
    now_provider=None, binding_validator=None,
):
    return ConstructionTemplatePublicationWorkflow(
        session=session,
        publication_service=build_construction_template_publication_service(
            session=session, now_provider=now_provider,
            binding_validator=binding_validator),
        idempotency_service=build_api_idempotency_service(
            session, encryption_key=idempotency_encryption_key))
