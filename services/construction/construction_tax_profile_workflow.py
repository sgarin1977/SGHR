"""Atomic tax-profile writes with the shared optional encrypted receipt protocol.

Requires a dedicated active Construction transaction with trusted tenant RLS.
Membership barriers precede idempotency/profile locks. Replays return the
original immutable result under fresh current tax.edit authority.
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from inspect import signature
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, ApiIdempotencyReservation,
    build_api_idempotency_service, hash_request_payload,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileService, ConstructionTaxProfileResult, ConstructionTaxProfileState,
    ConstructionTaxProfileValidationError, ConstructionTaxProfileNotFoundError, ConstructionTaxProfileOperationError,
    positive_version, validated_fields, build_construction_tax_profile_service,
)

CREATE = "construction.tax_profile.create"
UPDATE = "construction.tax_profile.update"


class ConstructionTaxProfileInProgressError(ConstructionTaxProfileOperationError):
    status_code = 409
    code = "idempotency_in_progress"


class ConstructionTaxProfileWorkflow:
    def __init__(self, *, session, profile_service, idempotency_service=None):
        if (
            not isinstance(profile_service, ConstructionTaxProfileService) or profile_service.session is not session
            or any("manage_transaction" not in signature(getattr(profile_service, name)).parameters
                   for name in ("create_profile", "update_profile"))
            or (idempotency_service is not None and (
                getattr(getattr(idempotency_service, "repository", None), "session", None) is not session
                or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
                or any(not callable(getattr(idempotency_service.repository, name, None))
                       for name in ("reserve_record", "get_active_record_for_update"))
                or not callable(getattr(getattr(idempotency_service, "response_codec", None), "decrypt", None))
            ))
        ):
            raise ValueError("Tax workflow requires shared-session services and encrypted atomic receipts.")
        self.session, self.service, self.idempotency = session, profile_service, idempotency_service

    @staticmethod
    def _error():
        return ConstructionTaxProfileOperationError("Tax profile workflow state is invalid.")

    @staticmethod
    def _uuid(value):
        if not isinstance(value, str):
            raise ConstructionTaxProfileWorkflow._error()
        try:
            result = UUID(value)
        except ValueError:
            raise ConstructionTaxProfileWorkflow._error() from None
        if str(result) != value:
            raise ConstructionTaxProfileWorkflow._error()
        return result

    @staticmethod
    def _time(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionTaxProfileWorkflow._error()
        return value.astimezone(timezone.utc)

    def _encode(self, result):
        if not isinstance(result, ConstructionTaxProfileResult) or type(result.changed) is not bool:
            raise self._error()
        state = result.profile
        if not isinstance(state, ConstructionTaxProfileState):
            raise self._error()
        self.service._state(asdict(state), tenant_id=state.tenant_id, profile_id=state.id)
        fields = asdict(state)
        for name in ("id", "tenant_id", "created_by"):
            fields[name] = str(fields[name])
        for name in ("created_at", "updated_at"):
            fields[name] = self._time(fields[name]).isoformat()
        return {"schema_version": 1, "profile": fields, "changed": result.changed}

    def _decode(self, payload, *, actor, operation, request):
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "profile", "changed"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or type(payload["changed"]) is not bool or not isinstance(payload["profile"], dict)
                or set(payload["profile"]) != set(ConstructionTaxProfileState.__dataclass_fields__)):
            raise self._error()
        fields = dict(payload["profile"])
        for name in ("id", "tenant_id", "created_by"):
            fields[name] = self._uuid(fields[name])
        for name in ("created_at", "updated_at"):
            if not isinstance(fields[name], str):
                raise self._error()
            fields[name] = self._time(datetime.fromisoformat(fields[name]))
        profile_id = fields["id"] if operation == CREATE else UUID(request["profile_id"])
        state = self.service._state(fields, tenant_id=actor.tenant_id, profile_id=profile_id)
        if operation == CREATE:
            if (state.created_by != actor.user_id or state.row_version != 1 or payload["changed"] is not True
                    or any(getattr(state, key) != value for key, value in request["fields"].items())):
                raise self._error()
        elif (state.row_version != request["expected_row_version"] + int(payload["changed"])
              or any(getattr(state, key) != value for key, value in request["fields"].items())):
            raise self._error()
        result = ConstructionTaxProfileResult(state, payload["changed"])
        if self._encode(result) != payload:
            raise self._error()
        return result

    def _record(self, record, *, actor, operation, key, payload):
        # Materialize before fresh authorization expires ORM identity-map entries.
        fields = {name: getattr(record, name, None) for name in (
            "id", "tenant_id", "principal_type", "principal_id", "operation", "key_hash",
            "request_hash", "created_at", "expires_at", "status",
        )}
        if (not isinstance(fields["id"], UUID) or fields["tenant_id"] != actor.tenant_id
                or fields["principal_id"] != actor.user_id or fields["principal_type"] != "user"
                or fields["operation"] != operation or fields["key_hash"] != sha256(key.encode("utf-8")).hexdigest()
                or fields["request_hash"] != hash_request_payload(payload)
                or self._time(fields["created_at"]) > self.service._now()
                or self._time(fields["expires_at"]) <= self.service._now()):
            raise self._error()
        return fields

    async def create_profile(self, *, actor, name, client_price_tax_mode, material_cost_basis,
                             idempotency_key=None, trace_id=None):
        fields = validated_fields(dict(name=name, client_price_tax_mode=client_price_tax_mode,
                                       material_cost_basis=material_cost_basis), complete=True)
        return await self._run(actor=actor, operation=CREATE, payload={"fields": fields},
                               idempotency_key=idempotency_key, trace_id=trace_id)

    async def update_profile(self, *, actor, profile_id, expected_row_version, fields,
                             idempotency_key=None, trace_id=None):
        if not isinstance(profile_id, UUID):
            raise ConstructionTaxProfileValidationError("A profile UUID is required.")
        payload = {"profile_id": str(profile_id), "expected_row_version": positive_version(expected_row_version),
                   "fields": validated_fields(fields)}
        return await self._run(actor=actor, operation=UPDATE, payload=payload,
                               idempotency_key=idempotency_key, trace_id=trace_id)

    async def _run(self, *, actor, operation, payload, idempotency_key, trace_id):
        self.service._actor(actor)
        if (idempotency_key is not None and (
                not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255)):
            raise ConstructionTaxProfileValidationError("Idempotency key is invalid.")
        if (self.session.in_transaction() is not True or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionTaxProfileOperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                await self.service.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self.service._authorize(actor, "construction.tax.edit")
                reservation = None
                if idempotency_key is not None:
                    if self.idempotency is None:
                        raise ConstructionTaxProfileOperationError("Tax profile replay is not configured.")
                    reservation = await self.idempotency.reserve(
                        tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                        operation=operation, idempotency_key=idempotency_key, payload=payload, now=self.service._now(),
                    )
                    if not isinstance(reservation, ApiIdempotencyReservation) or type(reservation.is_replay) is not bool:
                        raise self._error()
                    record = self._record(reservation.record, actor=actor, operation=operation,
                                          key=idempotency_key, payload=payload)
                    await self.service._authorize(actor, "construction.tax.edit")  # Refresh after receipt-lock wait.
                if reservation is not None and reservation.is_replay:
                    if record["status"] == "processing" and reservation.response_status is None:
                        raise ConstructionTaxProfileInProgressError("Tax profile write is processing.")
                    if record["status"] != "completed" or reservation.response_status != (201 if operation == CREATE else 200):
                        raise self._error()
                    result = self._decode(reservation.response_payload, actor=actor, operation=operation, request=payload)
                else:
                    if reservation is not None and record["status"] != "processing":
                        raise self._error()
                    # The service re-enters the same already-held actor barriers; no new member keys are taken.
                    if operation == CREATE:
                        result = await self.service.create_profile(
                            actor=actor, **payload["fields"], trace_id=trace_id, manage_transaction=False)
                    else:
                        result = await self.service.update_profile(
                            actor=actor, profile_id=UUID(payload["profile_id"]),
                            expected_row_version=payload["expected_row_version"], fields=payload["fields"],
                            trace_id=trace_id, manage_transaction=False)
                    snapshot = self._encode(result)
                    result = self._decode(snapshot, actor=actor, operation=operation, request=payload)
                    if reservation is not None:
                        # Refresh explicitly via awaited SQL: domain authorization expired the original ORM record.
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
                await self.service._authorize(actor, "construction.tax.edit")
                await self.session.commit()
                return result
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionTaxProfileOperationError("Tax profile workflow rollback failed.") from None
            if isinstance(error, (
                ConstructionTaxProfileValidationError, ConstructionTaxProfileNotFoundError,
                ConstructionTaxProfileOperationError, ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ApiIdempotencyKeyReusedError,
            )) or not isinstance(error, Exception):
                raise
            raise ConstructionTaxProfileOperationError("Tax profile workflow failed.") from None


def build_construction_tax_profile_workflow(*, session, idempotency_encryption_key=None, now_provider=None):
    return ConstructionTaxProfileWorkflow(
        session=session,
        profile_service=build_construction_tax_profile_service(session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session, encryption_key=idempotency_encryption_key)
            if idempotency_encryption_key is not None else None,
    )
