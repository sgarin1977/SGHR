"""Atomic starter-copy workflow using shared encrypted Platform receipts."""
from dataclasses import asdict, replace
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
from services.construction.construction_template_copy import (
    ConstructionTemplateCopyService,
    ConstructionTemplateCopyResult,
    ConstructionTemplateCopyValidationError as Validation,
    ConstructionTemplateCopyOperationError as Operation,
    ConstructionTemplateCopyConflictError as Conflict,
    build_construction_template_copy_service,
)

OPERATION = "construction.measurement_template.copy_starter"
UUID_FIELDS = (
    "tenant_id", "template_id", "template_version_id",
    "source_starter_version_id", "created_by",
)
TIME_FIELDS = ("created_at", "published_at")
INTEGER_FIELDS = (
    "template_row_version", "version", "version_row_version",
    "definition_schema_version", "section_count", "question_count",
)
RECORD_FIELDS = (
    "id", "tenant_id", "principal_type", "principal_id", "operation",
    "key_hash", "request_hash", "created_at", "expires_at",
    "status", "response_status", "response_ciphertext", "completed_at",
)
CONTEXT_FIELDS = RECORD_FIELDS[:9]


class ConstructionTemplateCopyKeyReusedError(
    ApiIdempotencyKeyReusedError, Conflict,
):
    pass


class ConstructionTemplateCopyInProgressError(Conflict):
    status_code = 409
    code = "idempotency_in_progress"


def _invalid():
    return Operation("Template copy receipt state is invalid.")


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


def encode_result(result):
    if not isinstance(result, ConstructionTemplateCopyResult):
        raise _invalid()
    fields = asdict(result)
    for name in UUID_FIELDS:
        if not isinstance(fields[name], UUID):
            raise _invalid()
        fields[name] = str(fields[name])
    for name in TIME_FIELDS:
        fields[name] = _time(fields[name]).isoformat()
    return {"schema_version": 1, "operation": OPERATION, "result": fields}


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
                ConstructionTemplateCopyResult.__dataclass_fields__)
        ):
            raise _invalid()
        fields = dict(payload["result"])
        for name in UUID_FIELDS:
            fields[name] = _uuid(fields[name])
        for name in TIME_FIELDS:
            if not isinstance(fields[name], str):
                raise _invalid()
            fields[name] = _time(datetime.fromisoformat(fields[name]))
        if any(type(fields[name]) is not int for name in INTEGER_FIELDS):
            raise _invalid()
        if (
            fields["tenant_id"] != actor.tenant_id
            or fields["created_by"] != actor.user_id
            or fields["source_starter_version_id"] != UUID(request["source_version_id"])
            or fields["template_id"] == fields["template_version_id"]
            or fields["template_id"] in (
                actor.tenant_id, actor.user_id, fields["source_starter_version_id"])
            or fields["template_version_id"] in (
                actor.tenant_id, actor.user_id, fields["source_starter_version_id"])
            or fields["template_row_version"] != 1
            or fields["version"] != 1 or fields["version_row_version"] != 2
            or fields["definition_schema_version"] != 1
            or not 1 <= fields["section_count"] <= 100
            or not fields["section_count"] <= fields["question_count"] <= 2000
            or not isinstance(fields["code"], str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", fields["code"]) is None
            or request["code"] is not None and fields["code"] != request["code"]
            or not isinstance(fields["source_language"], str)
            or re.fullmatch(
                r"[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,6})?",
                fields["source_language"]) is None
            or not isinstance(fields["content_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", fields["content_hash"]) is None
        ):
            raise _invalid()
        result = ConstructionTemplateCopyResult(**fields)
        if encode_result(result) != payload:
            raise _invalid()
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _invalid() from None


class ConstructionTemplateCopyWorkflow:
    def __init__(self, *, session, copy_service, idempotency_service):
        if (
            not isinstance(copy_service, ConstructionTemplateCopyService)
            or copy_service.session is not session
            or "manage_transaction" not in signature(
                copy_service.copy_template).parameters
            or not isinstance(idempotency_service, ApiIdempotencyService)
            or getattr(idempotency_service.repository, "session", None) is not session
            or any(
                not callable(getattr(idempotency_service.repository, name, None))
                for name in ("reserve_record", "get_active_record_for_update"))
            or any(
                not callable(getattr(idempotency_service.response_codec, name, None))
                for name in ("encrypt", "decrypt"))
        ):
            raise ValueError(
                "Copy workflow requires shared-session copy and encrypted receipt services.")
        self.session = session
        self.service = copy_service
        self.authorizer = copy_service.authorizer
        self.idempotency = idempotency_service

    def _record(self, record, *, actor, key, request):
        # Capture ORM values before fresh authorization expires identity-map state.
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
        if data["created_at"] > now or data["expires_at"] <= now:
            raise _invalid()
        if data["expires_at"] <= data["created_at"]:
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

    @staticmethod
    def _same_record(before, after):
        if any(before[name] != after[name] for name in CONTEXT_FIELDS):
            raise _invalid()

    def _completed(self, record, *, payload):
        if (
            record["status"] != "completed"
            or type(record["response_status"]) is not int
            or record["response_status"] != 201
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
        fresh = await self.idempotency.repository.get_active_record_for_update(
            tenant_id=actor.tenant_id, principal_type="user",
            principal_id=actor.user_id, operation=OPERATION,
            key_hash=original["key_hash"], now=self.authorizer._now())
        self.authorizer._same_transaction(transaction)
        checked = self._record(fresh, actor=actor, key=key, request=request)
        self._same_record(original, checked)
        return fresh, checked

    async def copy_template(
        self, *, actor, source_version_id, idempotency_key,
        code=None, trace_id=None,
    ):
        if (
            not isinstance(source_version_id, UUID)
            or not isinstance(idempotency_key, str)
            or not idempotency_key.strip() or len(idempotency_key) > 255
            or code is not None and (
                not isinstance(code, str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None)
        ):
            raise Validation("Invalid keyed template copy request.")
        request = {"source_version_id": str(source_version_id), "code": code}

        # Membership/ownership serialization precedes receipt and domain locks.
        async with self.authorizer._operation(actor, write=True) as transaction:
            try:
                reservation = await self.idempotency.reserve(
                    tenant_id=actor.tenant_id, principal_type="user",
                    principal_id=actor.user_id, operation=OPERATION,
                    idempotency_key=idempotency_key, payload=request,
                    now=self.authorizer._now())
            except ApiIdempotencyKeyReusedError:
                # Preserve the Platform conflict through the shared domain guard.
                raise ConstructionTemplateCopyKeyReusedError(
                    "Idempotency key was reused with a different copy request.") from None
            self.authorizer._same_transaction(transaction)
            if (
                not isinstance(reservation, ApiIdempotencyReservation)
                or type(reservation.is_replay) is not bool
            ):
                raise _invalid()
            original = self._record(
                reservation.record, actor=actor,
                key=idempotency_key, request=request)
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
                    raise ConstructionTemplateCopyInProgressError(
                        "Template copy is still processing.")
                if (
                    type(reservation.response_status) is not int
                    or reservation.response_status != 201
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
                result = await self.service.copy_template(
                    actor=actor, source_version_id=source_version_id,
                    code=code, trace_id=trace_id, manage_transaction=False)
                self.authorizer._same_transaction(transaction)
                payload = encode_result(result)
                result = decode_result(payload, actor=actor, request=request)

                # The copy service expired ORM state while refreshing authority.
                fresh, checked = await self._reload(
                    actor=actor, key=idempotency_key, request=request,
                    transaction=transaction, original=original)
                self._processing(checked)
                await self.idempotency.complete(
                    reservation=replace(reservation, record=fresh),
                    response_status=201, response_payload=payload,
                    now=self.authorizer._now())
                self.authorizer._same_transaction(transaction)

                # Verify the persisted completion, not just a collaborator return value.
                _, completed = await self._reload(
                    actor=actor, key=idempotency_key, request=request,
                    transaction=transaction, original=original)
                self._completed(completed, payload=payload)

            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            await self.session.commit()
            return result


def build_construction_template_copy_workflow(
    *, session, idempotency_encryption_key,
    now_provider=None, binding_validator=None,
):
    return ConstructionTemplateCopyWorkflow(
        session=session,
        copy_service=build_construction_template_copy_service(
            session=session, now_provider=now_provider,
            binding_validator=binding_validator),
        idempotency_service=build_api_idempotency_service(
            session, encryption_key=idempotency_encryption_key))
