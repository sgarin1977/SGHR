"""CON-018 atomic adjustment writes using shared encrypted Platform receipts."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from inspect import signature
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, ApiIdempotencyReservation,
    build_api_idempotency_service, hash_request_payload)
from services.construction.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_project_pricing_adjustments import (
    ConstructionProjectPricingAdjustmentService as Service,
    ConstructionProjectPricingAdjustmentResult as Result,
    ConstructionProjectPricingAdjustmentState as State,
    ConstructionProjectPricingAdjustmentValidationError as ValidationError,
    ConstructionProjectPricingAdjustmentNotFoundError as NotFoundError,
    ConstructionProjectPricingAdjustmentOperationError as OperationError,
    positive_version, validated_fields, build_construction_project_pricing_adjustment_service)

CREATE = "construction.project_pricing_adjustment.create"
UPDATE = "construction.project_pricing_adjustment.update"


class ConstructionProjectPricingAdjustmentInProgressError(OperationError):
    status_code = 409
    code = "idempotency_in_progress"


def _decimal_text(value):
    # Tuple manipulation keeps exact precision and compact large exponents.
    parts = value.as_tuple()
    digits, exponent = list(parts.digits), parts.exponent
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return str(Decimal((parts.sign, tuple(digits), exponent)))


def _wire_fields(values, *, complete=False):
    fields = validated_fields(values, complete=complete)
    if "coefficient" in fields:
        fields["coefficient"] = _decimal_text(fields["coefficient"])
    return fields


def _domain_fields(values, *, complete=False):
    fields = dict(values)
    if "coefficient" in fields:
        if not isinstance(fields["coefficient"], str):
            raise OperationError("Adjustment wire value is invalid.")
        fields["coefficient"] = Decimal(fields["coefficient"])
    return validated_fields(fields, complete=complete)


class ConstructionProjectPricingAdjustmentWorkflow:
    def __init__(self, *, session, adjustment_service, idempotency_service=None):
        if (not isinstance(adjustment_service, Service) or adjustment_service.session is not session
                or any("manage_transaction" not in signature(getattr(adjustment_service, name)).parameters
                       for name in ("create_adjustment", "update_adjustment"))
                or (idempotency_service is not None and (
                    getattr(getattr(idempotency_service, "repository", None), "session", None) is not session
                    or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
                    or any(not callable(getattr(idempotency_service.repository, name, None))
                           for name in ("reserve_record", "get_active_record_for_update"))
                    or any(not callable(getattr(getattr(idempotency_service, "response_codec", None), name, None))
                           for name in ("encrypt", "decrypt"))))):
            raise ValueError("Adjustment workflow requires shared-session services and encrypted atomic receipts.")
        self.session, self.service, self.idempotency = session, adjustment_service, idempotency_service

    @staticmethod
    def _error():
        return OperationError("Adjustment workflow state is invalid.")

    @staticmethod
    def _uuid(value):
        if not isinstance(value, str):
            raise ConstructionProjectPricingAdjustmentWorkflow._error()
        try:
            result = UUID(value)
        except ValueError:
            raise ConstructionProjectPricingAdjustmentWorkflow._error() from None
        if str(result) != value:
            raise ConstructionProjectPricingAdjustmentWorkflow._error()
        return result

    @staticmethod
    def _time(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionProjectPricingAdjustmentWorkflow._error()
        return value.astimezone(timezone.utc)

    def _encode(self, result):
        if not isinstance(result, Result) or type(result.changed) is not bool or not isinstance(result.adjustment, State):
            raise self._error()
        state, fields = result.adjustment, asdict(result.adjustment)
        self.service._state(fields, tenant_id=state.tenant_id, project_id=state.project_id, adjustment_id=state.id)
        for name in ("id", "tenant_id", "project_id", "created_by"):
            fields[name] = str(fields[name])
        for name in ("created_at", "updated_at"):
            fields[name] = self._time(fields[name]).isoformat()
        fields["coefficient"] = _decimal_text(fields["coefficient"])
        return {"schema_version": 1, "adjustment": fields, "changed": result.changed}

    def _decode(self, payload, *, actor, operation, request):
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "adjustment", "changed"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or type(payload["changed"]) is not bool or not isinstance(payload["adjustment"], dict)
                or set(payload["adjustment"]) != set(State.__dataclass_fields__) or operation not in (CREATE, UPDATE)):
            raise self._error()
        fields = dict(payload["adjustment"])
        for name in ("id", "tenant_id", "project_id", "created_by"):
            fields[name] = self._uuid(fields[name])
        for name in ("created_at", "updated_at"):
            if not isinstance(fields[name], str):
                raise self._error()
            fields[name] = self._time(datetime.fromisoformat(fields[name]))
        if not isinstance(fields["coefficient"], str):
            raise self._error()
        fields["coefficient"] = Decimal(fields["coefficient"])
        adjustment_id = fields["id"] if operation == CREATE else self._uuid(request["adjustment_id"])
        state = self.service._state(fields, tenant_id=actor.tenant_id,
                                    project_id=self._uuid(request["project_id"]), adjustment_id=adjustment_id)
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
        # Copy attributes before fresh authorization expires ORM identity state.
        data = {name: getattr(record, name, None) for name in (
            "id", "tenant_id", "principal_type", "principal_id", "operation", "key_hash",
            "request_hash", "created_at", "expires_at", "status")}
        instant = self.service._now()
        if (not isinstance(data["id"], UUID) or data["tenant_id"] != actor.tenant_id
                or data["principal_type"] != "user" or data["principal_id"] != actor.user_id
                or data["operation"] != operation or data["key_hash"] != sha256(key.encode("utf-8")).hexdigest()
                or data["request_hash"] != hash_request_payload(payload)
                or self._time(data["created_at"]) > instant or self._time(data["expires_at"]) <= instant):
            raise self._error()
        return data

    async def create_adjustment(self, *, actor, project_id, name, coefficient, enabled,
                                idempotency_key=None, trace_id=None):
        if not isinstance(project_id, UUID):
            raise ValidationError("A project UUID is required.")
        payload = {"project_id": str(project_id),
                   "fields": _wire_fields(dict(name=name, coefficient=coefficient, enabled=enabled), complete=True)}
        return await self._run(actor=actor, operation=CREATE, payload=payload,
                               idempotency_key=idempotency_key, trace_id=trace_id)

    async def update_adjustment(self, *, actor, project_id, adjustment_id, expected_row_version, fields,
                                idempotency_key=None, trace_id=None):
        if not isinstance(project_id, UUID) or not isinstance(adjustment_id, UUID):
            raise ValidationError("Project and adjustment UUIDs are required.")
        payload = {"project_id": str(project_id), "adjustment_id": str(adjustment_id),
                   "expected_row_version": positive_version(expected_row_version), "fields": _wire_fields(fields)}
        return await self._run(actor=actor, operation=UPDATE, payload=payload,
                               idempotency_key=idempotency_key, trace_id=trace_id)

    async def _run(self, *, actor, operation, payload, idempotency_key, trace_id):
        self.service._actor(actor)
        project_id = self._uuid(payload["project_id"])
        if idempotency_key is not None and (
                not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255):
            raise ValidationError("Idempotency key is invalid.")
        if (self.session.in_transaction() is not True or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise OperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                await self.service.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self.service._authorize(actor, project_id, write=True)
                reservation = None
                if idempotency_key is not None:
                    if self.idempotency is None:
                        raise OperationError("Adjustment replay is not configured.")
                    reservation = await self.idempotency.reserve(
                        tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                        operation=operation, idempotency_key=idempotency_key,
                        payload=payload, now=self.service._now())
                    if not isinstance(reservation, ApiIdempotencyReservation) or type(reservation.is_replay) is not bool:
                        raise self._error()
                    record = self._record(reservation.record, actor=actor, operation=operation,
                                          key=idempotency_key, payload=payload)
                    await self.service._authorize(actor, project_id, write=True)
                if reservation is not None and reservation.is_replay:
                    if record["status"] == "processing" and reservation.response_status is None:
                        raise ConstructionProjectPricingAdjustmentInProgressError("Adjustment write is processing.")
                    if record["status"] != "completed" or reservation.response_status != (201 if operation == CREATE else 200):
                        raise self._error()
                    # Historical receipt only: mutable-project guards run only for a new mutation.
                    result = self._decode(reservation.response_payload, actor=actor, operation=operation, request=payload)
                else:
                    if reservation is not None and record["status"] != "processing":
                        raise self._error()
                    if operation == CREATE:
                        result = await self.service.create_adjustment(
                            actor=actor, project_id=project_id, **_domain_fields(payload["fields"], complete=True),
                            trace_id=trace_id, manage_transaction=False)
                    else:
                        result = await self.service.update_adjustment(
                            actor=actor, project_id=project_id, adjustment_id=UUID(payload["adjustment_id"]),
                            expected_row_version=payload["expected_row_version"],
                            fields=_domain_fields(payload["fields"]), trace_id=trace_id, manage_transaction=False)
                    snapshot = self._encode(result)
                    result = self._decode(snapshot, actor=actor, operation=operation, request=payload)
                    if reservation is not None:
                        # Awaited SQL refresh prevents implicit I/O on the expired ORM record.
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
                await self.service._authorize(actor, project_id, write=True)
                await self.session.commit()
                return result
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise OperationError("Adjustment workflow rollback failed.") from None
            if isinstance(error, (
                ValidationError, NotFoundError, OperationError, ConstructionProjectTerminalError,
                ConstructionRowVersionConflictError, ConstructionPermissionDeniedError,
                ConstructionScopeDeniedError, ApiIdempotencyKeyReusedError,
            )) or not isinstance(error, Exception):
                raise
            raise OperationError("Adjustment workflow failed.") from None


def build_construction_project_pricing_adjustment_workflow(*, session, idempotency_encryption_key=None, now_provider=None):
    return ConstructionProjectPricingAdjustmentWorkflow(
        session=session,
        adjustment_service=build_construction_project_pricing_adjustment_service(session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session, encryption_key=idempotency_encryption_key)
            if idempotency_encryption_key is not None else None)
