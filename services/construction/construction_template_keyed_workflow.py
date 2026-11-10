"""Shared transaction runner for encrypted template-operation receipts."""
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import re
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyReservation,
    ApiIdempotencyKeyReusedError, hash_request_payload,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingService,
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingStateConflictError as Conflict,
)

RECORD_FIELDS = (
    "id", "tenant_id", "principal_type", "principal_id", "operation",
    "key_hash", "request_hash", "created_at", "expires_at",
    "status", "response_status", "response_ciphertext", "completed_at",
)
CONTEXT_FIELDS = RECORD_FIELDS[:9]


class ConstructionTemplateIdempotencyKeyReusedError(
    ApiIdempotencyKeyReusedError, Conflict,
):
    pass


class ConstructionTemplateOperationInProgressError(Conflict):
    status_code = 409
    code = "idempotency_in_progress"


def _invalid():
    return Operation("Template operation receipt is invalid.")


def _time(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None or value.utcoffset() is None
    ):
        raise _invalid()
    return value.astimezone(timezone.utc)


class ConstructionTemplateKeyedWorkflow:
    def __init__(
        self, *, session, authorizer, idempotency_service,
        operation, response_status, encode_result, decode_result,
    ):
        if (
            not isinstance(authorizer, ConstructionOnboardingService)
            or authorizer.session is not session
            or not isinstance(idempotency_service, ApiIdempotencyService)
            or getattr(idempotency_service.repository, "session", None) is not session
            or any(not callable(getattr(
                idempotency_service.repository, name, None))
                for name in ("reserve_record", "get_active_record_for_update"))
            or any(not callable(getattr(
                idempotency_service.response_codec, name, None))
                for name in ("encrypt", "decrypt"))
            or not isinstance(operation, str)
            or re.fullmatch(
                r"construction\.measurement_template\.[a-z_]{1,48}", operation) is None
            or type(response_status) is not int or response_status not in (200, 201)
            or not callable(encode_result) or not callable(decode_result)
        ):
            raise ValueError("Template receipts require shared-session server collaborators.")
        self.session, self.authorizer = session, authorizer
        self.idempotency = idempotency_service
        self.operation, self.response_status = operation, response_status
        self.encode_result, self.decode_result = encode_result, decode_result

    def _record(self, record, *, actor, key, request):
        data = {name: getattr(record, name, None) for name in RECORD_FIELDS}
        now = self.authorizer._now()
        if (
            not isinstance(data["id"], UUID)
            or data["tenant_id"] != actor.tenant_id
            or data["principal_type"] != "user"
            or data["principal_id"] != actor.user_id
            or data["operation"] != self.operation
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
    def _processing(data):
        if (
            data["status"] != "processing"
            or data["response_status"] is not None
            or data["response_ciphertext"] is not None
            or data["completed_at"] is not None
        ):
            raise _invalid()

    def _completed(self, data, *, payload):
        if (
            data["status"] != "completed"
            or type(data["response_status"]) is not int
            or data["response_status"] != self.response_status
            or not isinstance(data["response_ciphertext"], bytes)
            or not data["response_ciphertext"]
        ):
            raise _invalid()
        completed = _time(data["completed_at"])
        if not data["created_at"] <= completed <= self.authorizer._now():
            raise _invalid()
        decoded = self.idempotency.response_codec.decrypt(
            data["response_ciphertext"])
        if hash_request_payload(decoded) != hash_request_payload(payload):
            raise _invalid()

    async def _reload(self, *, actor, key, request, transaction, original):
        record = await self.idempotency.repository.get_active_record_for_update(
            tenant_id=actor.tenant_id, principal_type="user",
            principal_id=actor.user_id, operation=self.operation,
            key_hash=original["key_hash"], now=self.authorizer._now())
        self.authorizer._same_transaction(transaction)
        data = self._record(record, actor=actor, key=key, request=request)
        if any(data[name] != original[name] for name in CONTEXT_FIELDS):
            raise _invalid()
        return record, data

    async def execute(self, *, actor, idempotency_key, request, perform):
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip() or len(idempotency_key) > 255
            or not isinstance(request, dict) or not callable(perform)
        ):
            raise Validation("Invalid keyed template operation.")
        async with self.authorizer._operation(actor, write=True) as transaction:
            try:
                reservation = await self.idempotency.reserve(
                    tenant_id=actor.tenant_id, principal_type="user",
                    principal_id=actor.user_id, operation=self.operation,
                    idempotency_key=idempotency_key, payload=request,
                    now=self.authorizer._now())
            except ApiIdempotencyKeyReusedError:
                raise ConstructionTemplateIdempotencyKeyReusedError(
                    "Idempotency key was reused with a different template request."
                ) from None
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
                    raise ConstructionTemplateOperationInProgressError(
                        "Template operation is still processing.")
                if (
                    type(reservation.response_status) is not int
                    or reservation.response_status != self.response_status
                ):
                    raise _invalid()
                self._completed(original, payload=reservation.response_payload)
                result = self.decode_result(
                    reservation.response_payload, actor=actor, request=request)
            else:
                self._processing(original)
                if (
                    reservation.response_status is not None
                    or reservation.response_payload is not None
                ):
                    raise _invalid()
                performed = await perform()
                self.authorizer._same_transaction(transaction)
                payload = self.encode_result(performed)
                result = self.decode_result(payload, actor=actor, request=request)

                fresh, processing = await self._reload(
                    actor=actor, key=idempotency_key, request=request,
                    transaction=transaction, original=original)
                self._processing(processing)
                await self.idempotency.complete(
                    reservation=replace(reservation, record=fresh),
                    response_status=self.response_status,
                    response_payload=payload, now=self.authorizer._now())
                self.authorizer._same_transaction(transaction)
                _, completed = await self._reload(
                    actor=actor, key=idempotency_key, request=request,
                    transaction=transaction, original=original)
                self._completed(completed, payload=payload)

            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            await self.session.commit()
            return result
