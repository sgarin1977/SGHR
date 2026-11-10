"""CON-017 atomic pricing writes with shared encrypted Platform receipts."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from inspect import signature
from hashlib import sha256
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, ApiIdempotencyReservation,
    build_api_idempotency_service, hash_request_payload)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_pricing_profiles import (
    ConstructionPricingProfileService as Service,
    ConstructionPricingProfileResult as Result,
    ConstructionPricingProfileState as State,
    ConstructionPricingProfileValidationError as ValidationError,
    ConstructionPricingProfileNotFoundError as NotFoundError,
    ConstructionPricingProfileConflictError as ConflictError,
    ConstructionPricingProfileOperationError as OperationError,
    EDITABLE_FIELDS, positive_version, validated_fields,
    build_construction_pricing_profile_service)

CREATE = "construction.pricing_profile.create"
UPDATE = "construction.pricing_profile.update"


class ConstructionPricingProfileInProgressError(OperationError):
    status_code = 409
    code = "idempotency_in_progress"


def _decimal_text(value):
    # No normalize()/arithmetic: independent of Decimal context precision.
    # Scientific notation also avoids expanding a large positive exponent.
    if value == 0:
        return "0"
    parts = value.as_tuple()
    digits, exponent = list(parts.digits), parts.exponent
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return str(Decimal((parts.sign, tuple(digits), exponent)))


def _wire_fields(values, *, complete=False):
    result = validated_fields(values, complete=complete)
    for name in EDITABLE_FIELDS:
        if name in result:
            result[name] = _decimal_text(result[name])
    return result


def _domain_fields(values, *, complete=False):
    result = dict(values)
    for name in EDITABLE_FIELDS:
        if name in result:
            if not isinstance(result[name], str):
                raise OperationError("Pricing wire value is invalid.")
            result[name] = Decimal(result[name])
    return validated_fields(result, complete=complete)


class ConstructionPricingProfileWorkflow:
    def __init__(self, *, session, profile_service, idempotency_service=None):
        if (not isinstance(profile_service, Service) or profile_service.session is not session
                or any("manage_transaction" not in signature(getattr(profile_service, name)).parameters
                       for name in ("create_profile", "update_profile"))
                or (idempotency_service is not None and (
                    getattr(getattr(idempotency_service, "repository", None), "session", None) is not session
                    or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
                    or any(not callable(getattr(idempotency_service.repository, name, None))
                           for name in ("reserve_record", "get_active_record_for_update"))
                    or any(not callable(getattr(getattr(idempotency_service, "response_codec", None), name, None))
                           for name in ("encrypt", "decrypt"))))):
            raise ValueError("Pricing workflow requires shared-session services and encrypted atomic receipts.")
        self.session, self.service, self.idempotency = session, profile_service, idempotency_service

    @staticmethod
    def _error():
        return OperationError("Pricing workflow state is invalid.")

    @staticmethod
    def _uuid(value):
        if not isinstance(value, str):
            raise ConstructionPricingProfileWorkflow._error()
        try:
            result = UUID(value)
        except ValueError:
            raise ConstructionPricingProfileWorkflow._error() from None
        if str(result) != value:
            raise ConstructionPricingProfileWorkflow._error()
        return result

    @staticmethod
    def _time(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionPricingProfileWorkflow._error()
        return value.astimezone(timezone.utc)

    def _encode(self, result):
        if not isinstance(result, Result) or type(result.changed) is not bool or not isinstance(result.profile, State):
            raise self._error()
        state, fields = result.profile, asdict(result.profile)
        self.service._state(fields, tenant_id=state.tenant_id, profile_id=state.id)
        for name in ("id", "tenant_id", "created_by"):
            fields[name] = str(fields[name])
        for name in ("created_at", "updated_at"):
            fields[name] = self._time(fields[name]).isoformat()
        for name in EDITABLE_FIELDS:
            fields[name] = _decimal_text(fields[name])
        return {"schema_version": 1, "profile": fields, "changed": result.changed}

    def _decode(self, payload, *, actor, operation, request):
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "profile", "changed"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or type(payload["changed"]) is not bool or not isinstance(payload["profile"], dict)
                or set(payload["profile"]) != set(State.__dataclass_fields__) or operation not in (CREATE, UPDATE)):
            raise self._error()
        fields = dict(payload["profile"])
        for name in ("id", "tenant_id", "created_by"):
            fields[name] = self._uuid(fields[name])
        for name in ("created_at", "updated_at"):
            if not isinstance(fields[name], str):
                raise self._error()
            fields[name] = self._time(datetime.fromisoformat(fields[name]))
        for name in EDITABLE_FIELDS:
            if not isinstance(fields[name], str):
                raise self._error()
            fields[name] = Decimal(fields[name])
        profile_id = fields["id"] if operation == CREATE else self._uuid(request["profile_id"])
        state = self.service._state(fields, tenant_id=actor.tenant_id, profile_id=profile_id)
        requested = _domain_fields(request["fields"], complete=operation == CREATE)
        if operation == CREATE:
            if state.created_by != actor.user_id or state.row_version != 1 or payload["changed"] is not True:
                raise self._error()
        elif state.row_version != request["expected_row_version"] + int(payload["changed"]):
            raise self._error()
        if any(getattr(state, name) != value for name, value in requested.items()):
            raise self._error()
        result = Result(state, payload["changed"])
        if self._encode(result) != payload:
            raise self._error()
        return result

    def _record(self, record, *, actor, operation, key, payload):
        # Materialize before fresh authorization expires ORM attributes.
        data = {name: getattr(record, name, None) for name in (
            "id", "tenant_id", "principal_type", "principal_id", "operation", "key_hash",
            "request_hash", "created_at", "expires_at", "status")}
        instant = self.service._now()
        if (not isinstance(data["id"], UUID) or data["tenant_id"] != actor.tenant_id
                or data["principal_type"] != "user" or data["principal_id"] != actor.user_id
                or data["operation"] != operation
                or data["key_hash"] != sha256(key.encode("utf-8")).hexdigest()
                or data["request_hash"] != hash_request_payload(payload)
                or self._time(data["created_at"]) > instant or self._time(data["expires_at"]) <= instant):
            raise self._error()
        return data

    async def create_profile(self, *, actor, code, work_coefficient, material_markup_percent,
                             idempotency_key=None, trace_id=None):
        payload = {"fields": _wire_fields(dict(
            code=code, work_coefficient=work_coefficient,
            material_markup_percent=material_markup_percent), complete=True)}
        return await self._run(actor=actor, operation=CREATE, payload=payload,
                               idempotency_key=idempotency_key, trace_id=trace_id)

    async def update_profile(self, *, actor, profile_id, expected_row_version, fields,
                             idempotency_key=None, trace_id=None):
        if not isinstance(profile_id, UUID):
            raise ValidationError("A profile UUID is required.")
        payload = {"profile_id": str(profile_id),
                   "expected_row_version": positive_version(expected_row_version),
                   "fields": _wire_fields(fields)}
        return await self._run(actor=actor, operation=UPDATE, payload=payload,
                               idempotency_key=idempotency_key, trace_id=trace_id)

    async def _run(self, *, actor, operation, payload, idempotency_key, trace_id):
        self.service._actor(actor)
        if idempotency_key is not None and (
                not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255):
            raise ValidationError("Idempotency key is invalid.")
        if (self.session.in_transaction() is not True or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise OperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                await self.service.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self.service._authorize(actor, "construction.pricing.edit")
                reservation = None
                if idempotency_key is not None:
                    if self.idempotency is None:
                        raise OperationError("Pricing replay is not configured.")
                    reservation = await self.idempotency.reserve(
                        tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                        operation=operation, idempotency_key=idempotency_key,
                        payload=payload, now=self.service._now())
                    if not isinstance(reservation, ApiIdempotencyReservation) or type(reservation.is_replay) is not bool:
                        raise self._error()
                    record = self._record(reservation.record, actor=actor, operation=operation,
                                          key=idempotency_key, payload=payload)
                    await self.service._authorize(actor, "construction.pricing.edit")
                if reservation is not None and reservation.is_replay:
                    if record["status"] == "processing" and reservation.response_status is None:
                        raise ConstructionPricingProfileInProgressError("Pricing write is processing.")
                    if record["status"] != "completed" or reservation.response_status != (201 if operation == CREATE else 200):
                        raise self._error()
                    result = self._decode(reservation.response_payload, actor=actor, operation=operation, request=payload)
                else:
                    if reservation is not None and record["status"] != "processing":
                        raise self._error()
                    if operation == CREATE:
                        result = await self.service.create_profile(
                            actor=actor, **_domain_fields(payload["fields"], complete=True),
                            trace_id=trace_id, manage_transaction=False)
                    else:
                        result = await self.service.update_profile(
                            actor=actor, profile_id=UUID(payload["profile_id"]),
                            expected_row_version=payload["expected_row_version"],
                            fields=_domain_fields(payload["fields"]), trace_id=trace_id, manage_transaction=False)
                    snapshot = self._encode(result)
                    result = self._decode(snapshot, actor=actor, operation=operation, request=payload)
                    if reservation is not None:
                        # Domain authorization expired the record; refresh by awaited SQL before completion.
                        fresh = await self.idempotency.repository.get_active_record_for_update(
                            tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                            operation=operation, key_hash=record["key_hash"], now=self.service._now())
                        checked = self._record(fresh, actor=actor, operation=operation, key=idempotency_key, payload=payload)
                        if checked["id"] != record["id"] or checked["status"] != "processing":
                            raise self._error()
                        await self.idempotency.complete(
                            reservation=replace(reservation, record=fresh),
                            response_status=201 if operation == CREATE else 200,
                            response_payload=snapshot, now=self.service._now())
                await self.service._authorize(actor, "construction.pricing.edit")
                await self.session.commit()
                return result
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise OperationError("Pricing workflow rollback failed.") from None
            if isinstance(error, (ValidationError, NotFoundError, ConflictError, OperationError,
                                  ConstructionRowVersionConflictError, ConstructionPermissionDeniedError,
                                  ConstructionScopeDeniedError, ApiIdempotencyKeyReusedError)
                          ) or not isinstance(error, Exception):
                raise
            raise OperationError("Pricing workflow failed.") from None


def build_construction_pricing_profile_workflow(*, session, idempotency_encryption_key=None, now_provider=None):
    return ConstructionPricingProfileWorkflow(
        session=session,
        profile_service=build_construction_pricing_profile_service(session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session, encryption_key=idempotency_encryption_key)
            if idempotency_encryption_key is not None else None)
