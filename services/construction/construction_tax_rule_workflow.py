"""Atomic tax-rule writes using shared encrypted Platform idempotency receipts.

Requires an active trusted Construction transaction. Replays return the original
result under fresh current tax.edit authority, without another rule or audit.
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from inspect import signature
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, ApiIdempotencyReservation,
    build_api_idempotency_service, hash_request_payload,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleService, ConstructionTaxRuleResult, ConstructionTaxRuleState,
    ConstructionTaxRuleValidationError, ConstructionTaxRuleNotFoundError, ConstructionTaxRuleOperationError,
    ConstructionTaxRuleConflictError,
    positive_version, validated_fields, build_construction_tax_rule_service,
)

CREATE = "construction.tax_rule.create"
UPDATE = "construction.tax_rule.update"


class ConstructionTaxRuleInProgressError(ConstructionTaxRuleOperationError):
    status_code = 409
    code = "idempotency_in_progress"


def _rate_text(value):
    # Strip insignificant zeros through the tuple, never context-sensitive normalize().
    value = validated_fields({"rate_percent": value})["rate_percent"]
    if value == 0:
        return "0"
    parts = value.as_tuple()
    digits, exponent = list(parts.digits), parts.exponent
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    result = Decimal((parts.sign, tuple(digits), exponent))
    return format(result, "f") if exponent >= 0 else str(result)


def _wire_fields(fields, *, complete=False):
    result = validated_fields(fields, complete=complete)
    if "rate_percent" in result:
        result["rate_percent"] = _rate_text(result["rate_percent"])
    return result


def _domain_fields(fields):
    result = dict(fields)
    if "rate_percent" in result:
        result["rate_percent"] = Decimal(result["rate_percent"])
    return validated_fields(result)


class ConstructionTaxRuleWorkflow:
    def __init__(self, *, session, rule_service, idempotency_service=None):
        if (
            not isinstance(rule_service, ConstructionTaxRuleService) or rule_service.session is not session
            or any("manage_transaction" not in signature(getattr(rule_service, name)).parameters
                   for name in ("create_rule", "update_rule"))
            or (idempotency_service is not None and (
                getattr(getattr(idempotency_service, "repository", None), "session", None) is not session
                or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
                or any(not callable(getattr(idempotency_service.repository, name, None))
                       for name in ("reserve_record", "get_active_record_for_update"))
                or not callable(getattr(getattr(idempotency_service, "response_codec", None), "decrypt", None))
            ))
        ):
            raise ValueError("Tax rule workflow requires shared-session services and encrypted atomic receipts.")
        self.session, self.service, self.idempotency = session, rule_service, idempotency_service

    @staticmethod
    def _error():
        return ConstructionTaxRuleOperationError("Tax rule workflow state is invalid.")

    @staticmethod
    def _uuid(value):
        if not isinstance(value, str):
            raise ConstructionTaxRuleWorkflow._error()
        try:
            result = UUID(value)
        except ValueError:
            raise ConstructionTaxRuleWorkflow._error() from None
        if str(result) != value:
            raise ConstructionTaxRuleWorkflow._error()
        return result

    @staticmethod
    def _time(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionTaxRuleWorkflow._error()
        return value.astimezone(timezone.utc)

    def _encode(self, result):
        if not isinstance(result, ConstructionTaxRuleResult) or type(result.changed) is not bool:
            raise self._error()
        state = result.rule
        if not isinstance(state, ConstructionTaxRuleState):
            raise self._error()
        fields = asdict(state)
        self.service._state(fields, tenant_id=state.tenant_id, profile_id=state.tax_profile_id, rule_id=state.id)
        for name in ("id", "tenant_id", "tax_profile_id", "created_by"):
            fields[name] = str(fields[name])
        for name in ("created_at", "updated_at"):
            fields[name] = self._time(fields[name]).isoformat()
        fields["rate_percent"] = _rate_text(fields["rate_percent"])
        return {"schema_version": 1, "rule": fields, "changed": result.changed}

    def _decode(self, payload, *, actor, operation, request):
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "rule", "changed"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or type(payload["changed"]) is not bool or not isinstance(payload["rule"], dict)
                or set(payload["rule"]) != set(ConstructionTaxRuleState.__dataclass_fields__)
                or operation not in (CREATE, UPDATE)):
            raise self._error()
        fields = dict(payload["rule"])
        for name in ("id", "tenant_id", "tax_profile_id", "created_by"):
            fields[name] = self._uuid(fields[name])
        for name in ("created_at", "updated_at"):
            if not isinstance(fields[name], str):
                raise self._error()
            fields[name] = self._time(datetime.fromisoformat(fields[name]))
        if not isinstance(fields["rate_percent"], str):
            raise self._error()
        fields["rate_percent"] = Decimal(fields["rate_percent"])
        rule_id = fields["id"] if operation == CREATE else self._uuid(request["rule_id"])
        state = self.service._state(
            fields, tenant_id=actor.tenant_id, profile_id=self._uuid(request["profile_id"]), rule_id=rule_id,
        )
        requested_fields = _domain_fields(request["fields"])
        if operation == CREATE:
            if state.created_by != actor.user_id or state.row_version != 1 or payload["changed"] is not True:
                raise self._error()
        elif state.row_version != request["expected_row_version"] + int(payload["changed"]):
            raise self._error()
        if any(getattr(state, key) != value for key, value in requested_fields.items()):
            raise self._error()
        result = ConstructionTaxRuleResult(state, payload["changed"])
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

    async def create_rule(self, *, actor, profile_id, category, rate_percent, idempotency_key=None, trace_id=None):
        if not isinstance(profile_id, UUID):
            raise ConstructionTaxRuleValidationError("A profile UUID is required.")
        payload = {
            "profile_id": str(profile_id),
            "fields": _wire_fields(dict(category=category, rate_percent=rate_percent), complete=True),
        }
        return await self._run(actor=actor, operation=CREATE, payload=payload,
                               idempotency_key=idempotency_key, trace_id=trace_id)

    async def update_rule(self, *, actor, profile_id, rule_id, expected_row_version, fields,
                          idempotency_key=None, trace_id=None):
        if not isinstance(profile_id, UUID) or not isinstance(rule_id, UUID):
            raise ConstructionTaxRuleValidationError("Profile and rule UUIDs are required.")
        payload = {
            "profile_id": str(profile_id), "rule_id": str(rule_id),
            "expected_row_version": positive_version(expected_row_version),
            "fields": _wire_fields(fields),
        }
        return await self._run(actor=actor, operation=UPDATE, payload=payload,
                               idempotency_key=idempotency_key, trace_id=trace_id)

    async def _run(self, *, actor, operation, payload, idempotency_key, trace_id):
        self.service._actor(actor)
        if (idempotency_key is not None and (
                not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255)):
            raise ConstructionTaxRuleValidationError("Idempotency key is invalid.")
        if (self.session.in_transaction() is not True or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionTaxRuleOperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                await self.service.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self.service._authorize(actor, "construction.tax.edit")
                reservation = None
                if idempotency_key is not None:
                    if self.idempotency is None:
                        raise ConstructionTaxRuleOperationError("Tax rule replay is not configured.")
                    reservation = await self.idempotency.reserve(
                        tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                        operation=operation, idempotency_key=idempotency_key, payload=payload, now=self.service._now(),
                    )
                    if not isinstance(reservation, ApiIdempotencyReservation) or type(reservation.is_replay) is not bool:
                        raise self._error()
                    record = self._record(reservation.record, actor=actor, operation=operation,
                                          key=idempotency_key, payload=payload)
                    await self.service._authorize(actor, "construction.tax.edit")
                if reservation is not None and reservation.is_replay:
                    if record["status"] == "processing" and reservation.response_status is None:
                        raise ConstructionTaxRuleInProgressError("Tax rule write is processing.")
                    if record["status"] != "completed" or reservation.response_status != (201 if operation == CREATE else 200):
                        raise self._error()
                    result = self._decode(reservation.response_payload, actor=actor, operation=operation, request=payload)
                else:
                    if reservation is not None and record["status"] != "processing":
                        raise self._error()
                    if operation == CREATE:
                        result = await self.service.create_rule(
                            actor=actor, profile_id=UUID(payload["profile_id"]),
                            **_domain_fields(payload["fields"]), trace_id=trace_id, manage_transaction=False,
                        )
                    else:
                        result = await self.service.update_rule(
                            actor=actor, profile_id=UUID(payload["profile_id"]), rule_id=UUID(payload["rule_id"]),
                            expected_row_version=payload["expected_row_version"], fields=_domain_fields(payload["fields"]),
                            trace_id=trace_id, manage_transaction=False,
                        )
                    snapshot = self._encode(result)
                    result = self._decode(snapshot, actor=actor, operation=operation, request=payload)
                    if reservation is not None:
                        # Domain authorization expired the original record: refresh via awaited SQL.
                        fresh = await self.idempotency.repository.get_active_record_for_update(
                            tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                            operation=operation, key_hash=record["key_hash"], now=self.service._now(),
                        )
                        checked = self._record(fresh, actor=actor, operation=operation, key=idempotency_key, payload=payload)
                        if checked["id"] != record["id"] or checked["status"] != "processing":
                            raise self._error()
                        await self.idempotency.complete(
                            reservation=replace(reservation, record=fresh),
                            response_status=201 if operation == CREATE else 200,
                            response_payload=snapshot, now=self.service._now(),
                        )
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
                raise ConstructionTaxRuleOperationError("Tax rule workflow rollback failed.") from None
            if isinstance(error, (
                ConstructionTaxRuleValidationError, ConstructionTaxRuleNotFoundError,
                ConstructionTaxRuleConflictError,
                ConstructionTaxRuleOperationError, ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ApiIdempotencyKeyReusedError,
            )) or not isinstance(error, Exception):
                raise
            raise ConstructionTaxRuleOperationError("Tax rule workflow failed.") from None


def build_construction_tax_rule_workflow(*, session, idempotency_encryption_key=None, now_provider=None):
    return ConstructionTaxRuleWorkflow(
        session=session,
        rule_service=build_construction_tax_rule_service(session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session, encryption_key=idempotency_encryption_key)
            if idempotency_encryption_key is not None else None,
    )

# con016_canonical_categories_unique_v1
