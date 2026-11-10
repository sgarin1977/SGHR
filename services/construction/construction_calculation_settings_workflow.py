"""CON-019 atomic configuration writes using shared encrypted receipts."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyService, ApiIdempotencyReservation,
    ApiIdempotencyKeyReusedError, build_api_idempotency_service,
    hash_request_payload,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_calculation_settings import (
    ConstructionCalculationSettingsService as Service,
    ConstructionCalculationSettingsState as State,
    ConstructionCalculationSettingsResult as Result,
    ConstructionCalculationSettingsValidationError as Validation,
    ConstructionCalculationSettingsNotFoundError as NotFound,
    ConstructionCalculationSettingsConflictError as Conflict,
    ConstructionCalculationSettingsOperationError as Operation,
    validated_fields, positive_version,
    build_construction_calculation_settings_service,
)

CREATE = "construction.calculation_settings.create"
UPDATE = "construction.calculation_settings.update"
DECIMALS = (
    "minimum_job_amount", "default_technological_waste_percent",
    "default_local_repair_percent",
)


class ConstructionCalculationSettingsInProgressError(Operation):
    status_code = 409
    code = "idempotency_in_progress"


def _decimal_text(value):
    """Canonical exact representation without context-dependent arithmetic."""
    if value == 0:
        return "0"
    parts = value.as_tuple()
    digits, exponent = list(parts.digits), parts.exponent
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return str(Decimal((parts.sign, tuple(digits), exponent)))


def _wire(values, *, complete=False):
    result = validated_fields(values, complete=complete)
    for name in DECIMALS:
        if name in result and result[name] is not None:
            result[name] = _decimal_text(result[name])
    return result


def _domain(values, *, complete=False):
    if not isinstance(values, dict):
        raise Operation("Calculation receipt fields are invalid.")
    result = dict(values)
    for name in DECIMALS:
        if name in result and result[name] is not None:
            if not isinstance(result[name], str):
                raise Operation("Calculation decimal receipt is invalid.")
            result[name] = Decimal(result[name])
    return validated_fields(result, complete=complete)


class ConstructionCalculationSettingsWorkflow:
    def __init__(self, *, session, settings_service, idempotency_service=None):
        if (
            not isinstance(settings_service, Service)
            or settings_service.session is not session
            or (
                idempotency_service is not None and (
                    not isinstance(idempotency_service, ApiIdempotencyService)
                    or getattr(idempotency_service.repository, "session", None) is not session
                    or any(not callable(getattr(idempotency_service.repository, name, None))
                           for name in ("reserve_record", "get_active_record_for_update"))
                    or any(not callable(getattr(idempotency_service.response_codec, name, None))
                           for name in ("encrypt", "decrypt"))
                )
            )
        ):
            raise ValueError("Calculation workflow requires shared-session atomic services.")
        self.session = session
        self.service = settings_service
        self.idempotency = idempotency_service

    @staticmethod
    def _error():
        return Operation("Calculation workflow state is invalid.")

    @staticmethod
    def _uuid(value):
        if not isinstance(value, str):
            raise ConstructionCalculationSettingsWorkflow._error()
        try:
            parsed = UUID(value)
        except ValueError:
            raise ConstructionCalculationSettingsWorkflow._error() from None
        if str(parsed) != value:
            raise ConstructionCalculationSettingsWorkflow._error()
        return parsed

    @staticmethod
    def _time(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionCalculationSettingsWorkflow._error()
        return value.astimezone(timezone.utc)

    def _encode(self, result):
        if (
            not isinstance(result, Result)
            or type(result.changed) is not bool
            or not isinstance(result.settings, State)
        ):
            raise self._error()
        fields = asdict(result.settings)
        self.service._state(fields, tenant_id=result.settings.tenant_id)
        for name in ("tenant_id", "created_by"):
            fields[name] = str(fields[name])
        for name in ("created_at", "updated_at"):
            fields[name] = self._time(fields[name]).isoformat()
        for name in DECIMALS:
            if fields[name] is not None:
                fields[name] = _decimal_text(fields[name])
        return {"schema_version": 1, "settings": fields, "changed": result.changed}

    def _decode(self, payload, *, actor, operation, request):
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "settings", "changed"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or type(payload["changed"]) is not bool
            or not isinstance(payload["settings"], dict)
            or set(payload["settings"]) != set(State.__dataclass_fields__)
            or operation not in (CREATE, UPDATE)
        ):
            raise self._error()
        fields = dict(payload["settings"])
        for name in ("tenant_id", "created_by"):
            fields[name] = self._uuid(fields[name])
        for name in ("created_at", "updated_at"):
            if not isinstance(fields[name], str):
                raise self._error()
            fields[name] = self._time(datetime.fromisoformat(fields[name]))
        for name in DECIMALS:
            if fields[name] is not None:
                if not isinstance(fields[name], str):
                    raise self._error()
                fields[name] = Decimal(fields[name])
        state = self.service._state(fields, tenant_id=actor.tenant_id)
        requested = _domain(request["fields"], complete=operation == CREATE)
        if operation == CREATE:
            if (
                state.created_by != actor.user_id
                or state.row_version != 1
                or payload["changed"] is not True
            ):
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
            "id", "tenant_id", "principal_type", "principal_id", "operation",
            "key_hash", "request_hash", "created_at", "expires_at", "status",
        )}
        now = self.service._now()
        if (
            not isinstance(data["id"], UUID)
            or data["tenant_id"] != actor.tenant_id
            or data["principal_type"] != "user"
            or data["principal_id"] != actor.user_id
            or data["operation"] != operation
            or data["key_hash"] != sha256(key.encode("utf-8")).hexdigest()
            or data["request_hash"] != hash_request_payload(payload)
            or self._time(data["created_at"]) > now
            or self._time(data["expires_at"]) <= now
        ):
            raise self._error()
        return data

    async def create_settings(
        self, *, actor, fields, idempotency_key=None, trace_id=None,
    ):
        return await self._run(
            actor=actor, operation=CREATE,
            payload={"fields": _wire(fields, complete=True)},
            idempotency_key=idempotency_key, trace_id=trace_id)

    async def update_settings(
        self, *, actor, expected_row_version, fields,
        idempotency_key=None, trace_id=None,
    ):
        return await self._run(
            actor=actor, operation=UPDATE,
            payload={
                "expected_row_version": positive_version(expected_row_version),
                "fields": _wire(fields),
            },
            idempotency_key=idempotency_key, trace_id=trace_id)

    async def _run(self, *, actor, operation, payload, idempotency_key, trace_id):
        self.service._actor(actor)
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip() or len(idempotency_key) > 255
        ):
            raise Validation("Idempotency key is invalid.")
        transaction = self.session.get_transaction()
        self.service._check_transaction(transaction)
        if any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted")):
            raise Operation("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                await self.service.serialization.lock_member(
                    tenant_id=actor.tenant_id, user_id=actor.user_id)
                self.service._check_transaction(transaction)
                await self.service._authorize(actor, transaction)
                reservation = None
                record = None
                if idempotency_key is not None:
                    if self.idempotency is None:
                        raise Operation("Calculation replay is not configured.")
                    reservation = await self.idempotency.reserve(
                        tenant_id=actor.tenant_id,
                        principal_type="user", principal_id=actor.user_id,
                        operation=operation, idempotency_key=idempotency_key,
                        payload=payload, now=self.service._now())
                    self.service._check_transaction(transaction)
                    if (
                        not isinstance(reservation, ApiIdempotencyReservation)
                        or type(reservation.is_replay) is not bool
                    ):
                        raise self._error()
                    record = self._record(
                        reservation.record, actor=actor, operation=operation,
                        key=idempotency_key, payload=payload)
                    await self.service._authorize(actor, transaction)

                response_status = 201 if operation == CREATE else 200
                if reservation is not None and reservation.is_replay:
                    if record["status"] == "processing" and reservation.response_status is None:
                        raise ConstructionCalculationSettingsInProgressError(
                            "Calculation write is processing.")
                    if (
                        record["status"] != "completed"
                        or type(reservation.response_status) is not int
                        or reservation.response_status != response_status
                    ):
                        raise self._error()
                    result = self._decode(
                        reservation.response_payload, actor=actor,
                        operation=operation, request=payload)
                else:
                    if reservation is not None and record["status"] != "processing":
                        raise self._error()
                    fields = _domain(payload["fields"], complete=operation == CREATE)
                    if operation == CREATE:
                        result = await self.service.create_settings(
                            actor=actor, fields=fields,
                            trace_id=trace_id, manage_transaction=False)
                    else:
                        result = await self.service.update_settings(
                            actor=actor,
                            expected_row_version=payload["expected_row_version"],
                            fields=fields, trace_id=trace_id, manage_transaction=False)
                    self.service._check_transaction(transaction)
                    snapshot = self._encode(result)
                    result = self._decode(
                        snapshot, actor=actor, operation=operation, request=payload)
                    if reservation is not None:
                        fresh = await self.idempotency.repository.get_active_record_for_update(
                            tenant_id=actor.tenant_id,
                            principal_type="user", principal_id=actor.user_id,
                            operation=operation, key_hash=record["key_hash"],
                            now=self.service._now())
                        self.service._check_transaction(transaction)
                        checked = self._record(
                            fresh, actor=actor, operation=operation,
                            key=idempotency_key, payload=payload)
                        if checked["id"] != record["id"] or checked["status"] != "processing":
                            raise self._error()
                        await self.idempotency.complete(
                            reservation=replace(reservation, record=fresh),
                            response_status=response_status,
                            response_payload=snapshot, now=self.service._now())
                        self.service._check_transaction(transaction)

                await self.service._authorize(actor, transaction)
                await self.session.commit()
                return result
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise Operation("Calculation workflow rollback failed.") from None
            if isinstance(error, (
                Validation, NotFound, Conflict, Operation,
                ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ApiIdempotencyKeyReusedError,
            )) or not isinstance(error, Exception):
                raise
            raise Operation("Calculation workflow failed.") from None


def build_construction_calculation_settings_workflow(
    *, session, idempotency_encryption_key=None, now_provider=None,
):
    return ConstructionCalculationSettingsWorkflow(
        session=session,
        settings_service=build_construction_calculation_settings_service(
            session=session, now_provider=now_provider),
        idempotency_service=(
            build_api_idempotency_service(
                session, encryption_key=idempotency_encryption_key)
            if idempotency_encryption_key is not None else None
        ),
    )
