"""CON-019 configuration, not an Estimate/Offer calculation engine.

Uses a trusted tenant session and the existing settings.manage permission.
No automatic percentages, FX, minimum surcharge or historical recalculation.
"""
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from services.platform_currency_catalog import (
    PlatformCurrencyCodeError, require_platform_currency,
)
from services.construction.construction_audit import (
    ConstructionAuditChange, ConstructionAuditLogger,
)
from services.construction.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access,
)

MAX_VERSION = 2**63 - 1
FIELDS = (
    "money_input_precision", "quantity_precision",
    "minimum_job_amount", "minimum_job_currency",
    "supplier_stale_price_days",
    "default_technological_waste_percent", "default_local_repair_percent",
)
OPTIONAL_FIELDS = FIELDS[2:4] + FIELDS[5:]


class ConstructionCalculationSettingsValidationError(ValueError):
    pass


class ConstructionCalculationSettingsNotFoundError(Exception):
    pass


class ConstructionCalculationSettingsConflictError(Exception):
    pass


class ConstructionCalculationSettingsOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionCalculationSettingsState:
    tenant_id: UUID
    money_input_precision: int
    quantity_precision: int
    minimum_job_amount: Decimal | None
    minimum_job_currency: str | None
    supplier_stale_price_days: int
    default_technological_waste_percent: Decimal | None
    default_local_repair_percent: Decimal | None
    row_version: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConstructionCalculationSettingsResult:
    settings: ConstructionCalculationSettingsState
    changed: bool


def validated_fields(values, *, complete=False):
    if not isinstance(values, Mapping) or not set(values).issubset(FIELDS):
        raise ConstructionCalculationSettingsValidationError(
            "Only calculation configuration fields are accepted.")
    result = dict(values)
    if complete:
        if not {"money_input_precision", "supplier_stale_price_days"}.issubset(result):
            raise ConstructionCalculationSettingsValidationError(
                "Money precision and supplier price lifetime must be explicit.")
        result.setdefault("quantity_precision", 4)
        for name in OPTIONAL_FIELDS:
            result.setdefault(name, None)

    for name, maximum in (
        ("money_input_precision", 6),
        ("quantity_precision", 6),
        ("supplier_stale_price_days", 3650),
    ):
        if name in result:
            value = result[name]
            if type(value) is not int or not 0 <= value <= maximum:
                raise ConstructionCalculationSettingsValidationError(
                    "Precision or supplier lifetime is outside the allowed range.")

    for name in (
        "minimum_job_amount",
        "default_technological_waste_percent",
        "default_local_repair_percent",
    ):
        if name in result and result[name] is not None:
            value = result[name]
            if (
                not isinstance(value, Decimal) or not value.is_finite()
                or value < 0
                or (name != "minimum_job_amount" and value > 100)
            ):
                raise ConstructionCalculationSettingsValidationError(
                    "An exact finite non-negative Decimal within the bounds is required.")

    if result.get("minimum_job_currency") is not None:
        try:
            require_platform_currency(result["minimum_job_currency"])
        except PlatformCurrencyCodeError:
            raise ConstructionCalculationSettingsValidationError(
                "Minimum amount currency is invalid.") from None

    if complete and (
        (result["minimum_job_amount"] is None)
        != (result["minimum_job_currency"] is None)
    ):
        raise ConstructionCalculationSettingsValidationError(
            "Minimum amount and its currency must be configured or cleared together.")
    return result


def positive_version(value):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ConstructionCalculationSettingsValidationError(
            "A positive BIGINT version is required.")
    return value


def _duplicate_settings(error):
    pending = [error.orig] if isinstance(error, IntegrityError) else []
    visited = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in visited:
            continue
        visited.add(id(current))
        diagnostic = getattr(current, "diag", None)
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        name = (
            getattr(current, "constraint_name", None)
            or getattr(diagnostic, "constraint_name", None)
        )
        if code == "23505" and name == "construction_calculation_settings_pkey":
            return True
        pending.extend([
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ])
    return False


class ConstructionCalculationSettingsService:
    def __init__(
        self, *, session, repository, access_repository,
        event_repository, serialization, now_provider=None,
    ):
        requirements = (
            (repository, ("get_settings", "create_settings", "update_settings")),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
            (event_repository, ("create_event",)),
            (serialization, ("lock_member",)),
        )
        if (
            session is None or not hasattr(session, "no_autoflush")
            or any(not callable(getattr(session, name, None)) for name in (
                "in_transaction", "get_transaction", "get_nested_transaction",
                "expire_all", "commit", "rollback",
            ))
            or any(
                getattr(repo, "session", None) is not session
                or any(not callable(getattr(repo, name, None)) for name in methods)
                for repo, methods in requirements
            )
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Calculation settings require shared-session wiring.")
        self.session = session
        self.repository = repository
        self.access_repository = access_repository
        self.serialization = serialization
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(
            repository=access_repository, now_provider=self._now)
        self.audit = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionCalculationSettingsOperationError("Calculation clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(getattr(actor, "user_id", None), UUID)
        ):
            raise ConstructionPermissionDeniedError("Calculation identity is unavailable.")

    def _check_transaction(self, transaction):
        if (
            transaction is None
            or self.session.in_transaction() is not True
            or self.session.get_transaction() is not transaction
            or not transaction.is_active
            or self.session.get_nested_transaction() is not None
        ):
            raise ConstructionCalculationSettingsOperationError(
                "The original outer transaction must remain active.")

    async def _authorize(self, actor, transaction):
        self._actor(actor)
        self._check_transaction(transaction)
        self.session.expire_all()
        membership = await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id)
        self._check_transaction(transaction)
        if membership is not True:
            raise ConstructionPermissionDeniedError("Calculation membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        self._check_transaction(transaction)
        require_construction_access(
            actor=actor, access_context=context,
            permission_code="construction.settings.manage")

    @asynccontextmanager
    async def _operation(self, *, actor, write):
        self._actor(actor)
        transaction = self.session.get_transaction()
        self._check_transaction(transaction)
        if any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted")):
            raise ConstructionCalculationSettingsOperationError(
                "A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                if write:
                    await self.serialization.lock_member(
                        tenant_id=actor.tenant_id, user_id=actor.user_id)
                    self._check_transaction(transaction)
                await self._authorize(actor, transaction)
                yield transaction
        except BaseException as error:
            try:
                await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionCalculationSettingsOperationError(
                    "Calculation settings rollback failed.") from None
            if isinstance(error, (
                ConstructionCalculationSettingsValidationError,
                ConstructionCalculationSettingsNotFoundError,
                ConstructionCalculationSettingsConflictError,
                ConstructionCalculationSettingsOperationError,
                ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
            )):
                raise
            if _duplicate_settings(error):
                raise ConstructionCalculationSettingsConflictError(
                    "Calculation settings already exist.") from None
            if not isinstance(error, Exception):
                raise
            raise ConstructionCalculationSettingsOperationError(
                "Calculation settings operation failed.") from None

    @staticmethod
    def _state(row, *, tenant_id):
        if row is None:
            raise ConstructionCalculationSettingsNotFoundError(
                "Calculation settings are unavailable.")
        try:
            state = ConstructionCalculationSettingsState(**row)
            fields = {name: getattr(state, name) for name in FIELDS}
            if (
                state.tenant_id != tenant_id
                or not isinstance(state.tenant_id, UUID)
                or not isinstance(state.created_by, UUID)
                or positive_version(state.row_version) != state.row_version
                or validated_fields(fields, complete=True) != fields
                or any(
                    not isinstance(value, datetime)
                    or value.tzinfo is None or value.utcoffset() is None
                    for value in (state.created_at, state.updated_at)
                )
            ):
                raise ValueError()
            return state
        except (TypeError, ValueError):
            raise ConstructionCalculationSettingsOperationError(
                "Stored calculation settings are invalid.") from None

    async def get_settings(self, *, actor):
        async with self._operation(actor=actor, write=False) as transaction:
            row = await self.repository.get_settings(tenant_id=actor.tenant_id)
            self._check_transaction(transaction)
            await self._authorize(actor, transaction)
            return None if row is None else self._state(row, tenant_id=actor.tenant_id)

    async def create_settings(
        self, *, actor, fields, trace_id=None, manage_transaction=True,
    ):
        if type(manage_transaction) is not bool:
            raise ConstructionCalculationSettingsValidationError("Transaction mode is invalid.")
        fields = validated_fields(fields, complete=True)
        async with self._operation(actor=actor, write=True) as transaction:
            existing = await self.repository.get_settings(
                tenant_id=actor.tenant_id, for_update=True)
            self._check_transaction(transaction)
            await self._authorize(actor, transaction)
            if existing is not None:
                self._state(existing, tenant_id=actor.tenant_id)
                raise ConstructionCalculationSettingsConflictError(
                    "Calculation settings already exist.")
            row = await self.repository.create_settings(
                tenant_id=actor.tenant_id, created_by=actor.user_id,
                fields=fields, as_of=self._now())
            self._check_transaction(transaction)
            state = self._state(row, tenant_id=actor.tenant_id)
            if (
                state.row_version != 1 or state.created_by != actor.user_id
                or any(getattr(state, key) != value for key, value in fields.items())
            ):
                raise ConstructionCalculationSettingsOperationError(
                    "Created calculation settings are invalid.")
            await self._audit(actor, state, before=None, trace_id=trace_id)
            self._check_transaction(transaction)
            await self._authorize(actor, transaction)
            if manage_transaction:
                await self.session.commit()
            return ConstructionCalculationSettingsResult(state, True)

    async def update_settings(
        self, *, actor, expected_row_version, fields,
        trace_id=None, manage_transaction=True,
    ):
        if type(manage_transaction) is not bool:
            raise ConstructionCalculationSettingsValidationError("Transaction mode is invalid.")
        expected_row_version = positive_version(expected_row_version)
        fields = validated_fields(fields)
        async with self._operation(actor=actor, write=True) as transaction:
            row = await self.repository.get_settings(
                tenant_id=actor.tenant_id, for_update=True)
            self._check_transaction(transaction)
            await self._authorize(actor, transaction)
            before = self._state(row, tenant_id=actor.tenant_id)
            require_expected_row_version(
                actual_row_version=before.row_version,
                expected_row_version=expected_row_version)
            merged = {name: getattr(before, name) for name in FIELDS} | fields
            validated_fields(merged, complete=True)
            changes = {
                name: value for name, value in fields.items()
                if getattr(before, name) != value
            }
            if not changes:
                if manage_transaction:
                    await self.session.commit()
                return ConstructionCalculationSettingsResult(before, False)
            if before.row_version == MAX_VERSION:
                raise ConstructionCalculationSettingsValidationError(
                    "Calculation settings version is exhausted.")
            row = await self.repository.update_settings(
                tenant_id=actor.tenant_id,
                expected_row_version=before.row_version,
                fields=changes, as_of=self._now())
            self._check_transaction(transaction)
            if row is None:
                raise ConstructionRowVersionConflictError()
            state = self._state(row, tenant_id=actor.tenant_id)
            if (
                state.row_version != before.row_version + 1
                or state.created_by != before.created_by
                or state.created_at != before.created_at
                or any(getattr(state, name) != merged[name] for name in FIELDS)
            ):
                raise ConstructionCalculationSettingsOperationError(
                    "Conditional calculation settings result is invalid.")
            await self._audit(actor, state, before=before, trace_id=trace_id)
            self._check_transaction(transaction)
            await self._authorize(actor, transaction)
            if manage_transaction:
                await self.session.commit()
            return ConstructionCalculationSettingsResult(state, True)

    async def _audit(self, actor, state, *, before, trace_id):
        def value(source, name):
            raw = getattr(source, name)
            return str(raw) if isinstance(raw, Decimal) else raw
        await self.audit.record_operation(
            actor=actor,
            event_type=(
                "construction_calculation_settings_created"
                if before is None else "construction_calculation_settings_updated"
            ),
            entity_type="construction_calculation_settings",
            entity_id=state.tenant_id,
            operation="create" if before is None else "update",
            trace_id=trace_id,
            changes=tuple(
                ConstructionAuditChange(
                    name, None if before is None else value(before, name),
                    value(state, name),
                )
                for name in FIELDS
                if before is None or getattr(before, name) != getattr(state, name)
            ),
            details={
                "row_version": state.row_version,
                "previous_row_version": None if before is None else before.row_version,
            },
        )


def build_construction_calculation_settings_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_calculation_settings import (
        ConstructionCalculationSettingsRepository,
    )
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import (
        PlatformMembershipSerializationRepository,
    )
    return ConstructionCalculationSettingsService(
        session=session,
        repository=ConstructionCalculationSettingsRepository(session),
        access_repository=ConstructionAccessRepository(session),
        event_repository=EventRepository(session),
        serialization=PlatformMembershipSerializationRepository(session),
        now_provider=now_provider,
    )
