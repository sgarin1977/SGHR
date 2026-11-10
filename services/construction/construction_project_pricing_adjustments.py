"""CON-018 explicit project pricing configuration, without calculation or defaults.

Trusted Construction session; membership barrier -> project row -> adjustment.
Default writes commit once; manage_transaction=False prepares an outer workflow.
Failure/cancellation rolls back. HTTP module/rate gates and replay follow later.
"""
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from services.construction.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction.construction_foundation import (
    ConstructionProjectTerminalError, ConstructionRowVersionConflictError,
    require_construction_project_mutable, require_expected_row_version)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_access)
from services.construction.construction_projects import CONSTRUCTION_PROJECT_STATUS_TRANSITIONS

MAX_VERSION = 2**63 - 1
EDITABLE_FIELDS = ("name", "coefficient", "enabled")


class ConstructionProjectPricingAdjustmentValidationError(ValueError):
    pass


class ConstructionProjectPricingAdjustmentNotFoundError(Exception):
    pass


class ConstructionProjectPricingAdjustmentOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionProjectPricingAdjustmentState:
    id: UUID
    tenant_id: UUID
    project_id: UUID
    name: str
    coefficient: Decimal
    enabled: bool
    row_version: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConstructionProjectPricingAdjustmentResult:
    adjustment: ConstructionProjectPricingAdjustmentState
    changed: bool


@dataclass(frozen=True)
class ConstructionProjectPricingAdjustmentCursor:
    tenant_id: UUID
    user_id: UUID
    project_id: UUID
    after_id: UUID

    def __post_init__(self):
        if not all(isinstance(getattr(self, name), UUID)
                   for name in ("tenant_id", "user_id", "project_id", "after_id")):
            raise ConstructionProjectPricingAdjustmentValidationError("Adjustment cursor requires UUIDs.")


@dataclass(frozen=True)
class ConstructionProjectPricingAdjustmentPage:
    items: tuple[ConstructionProjectPricingAdjustmentState, ...]
    next_cursor: ConstructionProjectPricingAdjustmentCursor | None


def validated_fields(values, *, complete=False):
    if (not isinstance(values, Mapping) or not set(values).issubset(EDITABLE_FIELDS)
            or (complete and set(values) != set(EDITABLE_FIELDS))):
        raise ConstructionProjectPricingAdjustmentValidationError("Explicit valid adjustment fields are required.")
    fields = dict(values)
    if "name" in fields:
        value = fields["name"]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 255 or "\x00" in value:
            raise ConstructionProjectPricingAdjustmentValidationError("Adjustment name is invalid.")
        fields["name"] = value.strip()
    if "coefficient" in fields:
        value = fields["coefficient"]
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise ConstructionProjectPricingAdjustmentValidationError("A finite positive Decimal coefficient is required.")
    if "enabled" in fields and type(fields["enabled"]) is not bool:
        raise ConstructionProjectPricingAdjustmentValidationError("Enabled must be an explicit boolean.")
    return fields


def positive_version(value):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ConstructionProjectPricingAdjustmentValidationError("A positive BIGINT version is required.")
    return value


class ConstructionProjectPricingAdjustmentService:
    def __init__(self, *, session, repository, access_repository, event_repository,
                 serialization, now_provider=None):
        repositories = (
            (repository, ("get_project", "get_adjustment", "list_adjustments", "create_adjustment", "update_adjustment")),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
            (event_repository, ("create_event",)),
            (serialization, ("lock_member",)),
        )
        if (session is None or not hasattr(session, "no_autoflush")
                or any(not callable(getattr(session, name, None)) for name in
                       ("in_transaction", "get_nested_transaction", "expire_all", "commit", "rollback"))
                or any(getattr(repo, "session", None) is not session
                       or any(not callable(getattr(repo, name, None)) for name in names)
                       for repo, names in repositories)
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError("Adjustments require shared-session transaction wiring.")
        self.session, self.repository, self.serialization = session, repository, serialization
        self.access_repository = access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionProjectPricingAdjustmentOperationError("Adjustment clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor):
        if (getattr(actor, "status", None) != "active"
                or not isinstance(getattr(actor, "tenant_id", None), UUID)
                or not isinstance(getattr(actor, "user_id", None), UUID)):
            raise ConstructionPermissionDeniedError("Adjustment identity is unavailable.")

    @staticmethod
    def _inputs(project_id, *, adjustment_id=None, manage_transaction=True):
        if (not isinstance(project_id, UUID)
                or (adjustment_id is not None and not isinstance(adjustment_id, UUID))
                or type(manage_transaction) is not bool):
            raise ConstructionProjectPricingAdjustmentValidationError("Project context or transaction mode is invalid.")

    async def _authorize(self, actor, project_id, *, write):
        self._actor(actor)
        self.session.expire_all()
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id
        ) is not True:
            raise ConstructionPermissionDeniedError("Adjustment membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        require_construction_access(
            actor=actor, access_context=context, project_id=project_id,
            permission_code="construction.pricing.edit" if write else "construction.pricing.read")

    @asynccontextmanager
    async def _operation(self, *, actor, project_id, write):
        self._actor(actor)
        if (self.session.in_transaction() is not True or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionProjectPricingAdjustmentOperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                if write:
                    await self.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self._authorize(actor, project_id, write=write)
                yield
        except BaseException as error:
            try:
                await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionProjectPricingAdjustmentOperationError("Adjustment rollback failed.") from None
            if isinstance(error, (
                ConstructionProjectPricingAdjustmentValidationError,
                ConstructionProjectPricingAdjustmentNotFoundError,
                ConstructionProjectPricingAdjustmentOperationError,
                ConstructionProjectTerminalError, ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
            )) or not isinstance(error, Exception):
                raise
            raise ConstructionProjectPricingAdjustmentOperationError("Project pricing adjustment operation failed.") from None

    async def _project(self, actor, project_id, *, write):
        row = await self.repository.get_project(
            tenant_id=actor.tenant_id, project_id=project_id, for_update=write)
        await self._authorize(actor, project_id, write=write)
        if row is None:
            raise ConstructionProjectPricingAdjustmentNotFoundError("Project or adjustment is unavailable.")
        if (not isinstance(row, Mapping) or set(row) != {"id", "tenant_id", "status", "deleted_at"}
                or not isinstance(row["id"], UUID) or not isinstance(row["tenant_id"], UUID)
                or row["id"] != project_id or row["tenant_id"] != actor.tenant_id
                or not isinstance(row["status"], str) or row["status"] not in CONSTRUCTION_PROJECT_STATUS_TRANSITIONS):
            raise ConstructionProjectPricingAdjustmentOperationError("Project projection is invalid.")
        if row["deleted_at"] is not None:
            raise ConstructionProjectPricingAdjustmentNotFoundError("Project or adjustment is unavailable.")
        if write:
            require_construction_project_mutable(project_status=row["status"])

    @staticmethod
    def _state(row, *, tenant_id, project_id, adjustment_id):
        if row is None:
            raise ConstructionProjectPricingAdjustmentNotFoundError("Project or adjustment is unavailable.")
        try:
            state = ConstructionProjectPricingAdjustmentState(**row)
            fields = {key: getattr(state, key) for key in EDITABLE_FIELDS}
            if (state.id != adjustment_id or state.tenant_id != tenant_id or state.project_id != project_id
                    or not all(isinstance(getattr(state, key), UUID) for key in ("id", "tenant_id", "project_id", "created_by"))
                    or positive_version(state.row_version) != state.row_version
                    or validated_fields(fields, complete=True) != fields
                    or any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
                           for value in (state.created_at, state.updated_at))):
                raise ValueError()
            return state
        except (TypeError, ValueError):
            raise ConstructionProjectPricingAdjustmentOperationError("Stored adjustment is invalid.") from None

    async def get_adjustment(self, *, actor, project_id, adjustment_id):
        self._inputs(project_id, adjustment_id=adjustment_id)
        if adjustment_id is None:
            raise ConstructionProjectPricingAdjustmentValidationError("An adjustment UUID is required.")
        async with self._operation(actor=actor, project_id=project_id, write=False):
            await self._project(actor, project_id, write=False)
            row = await self.repository.get_adjustment(
                tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=adjustment_id)
            await self._project(actor, project_id, write=False)
            return self._state(row, tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=adjustment_id)

    async def list_adjustments(self, *, actor, project_id, limit=20, after=None):
        self._actor(actor)
        self._inputs(project_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConstructionProjectPricingAdjustmentValidationError("Adjustment limit must be from 1 to 100.")
        if after is not None:
            if not isinstance(after, ConstructionProjectPricingAdjustmentCursor):
                raise ConstructionProjectPricingAdjustmentValidationError("Invalid adjustment cursor.")
            after = ConstructionProjectPricingAdjustmentCursor(after.tenant_id, after.user_id, after.project_id, after.after_id)
            if (after.tenant_id, after.user_id, after.project_id) != (actor.tenant_id, actor.user_id, project_id):
                raise ConstructionProjectPricingAdjustmentValidationError("Adjustment cursor context differs.")
        async with self._operation(actor=actor, project_id=project_id, write=False):
            await self._project(actor, project_id, write=False)
            rows = await self.repository.list_adjustments(
                tenant_id=actor.tenant_id, project_id=project_id, limit=limit + 1,
                after_id=None if after is None else after.after_id)
            await self._project(actor, project_id, write=False)
            if not isinstance(rows, (list, tuple)) or len(rows) > limit + 1:
                raise ConstructionProjectPricingAdjustmentOperationError("Adjustment page is invalid.")
            states = []
            previous = None if after is None else after.after_id
            for row in rows:
                if not isinstance(row, Mapping) or not isinstance(row.get("id"), UUID):
                    raise ConstructionProjectPricingAdjustmentOperationError("Adjustment page is invalid.")
                state = self._state(row, tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=row["id"])
                if previous is not None and state.id <= previous:
                    raise ConstructionProjectPricingAdjustmentOperationError("Adjustment order is invalid.")
                states.append(state)
                previous = state.id
            items = tuple(states[:limit])
            cursor = ConstructionProjectPricingAdjustmentCursor(
                actor.tenant_id, actor.user_id, project_id, items[-1].id
            ) if len(states) > limit else None
            return ConstructionProjectPricingAdjustmentPage(items, cursor)

    async def create_adjustment(self, *, actor, project_id, name, coefficient, enabled,
                                trace_id=None, manage_transaction=True):
        self._inputs(project_id, manage_transaction=manage_transaction)
        fields = validated_fields(dict(name=name, coefficient=coefficient, enabled=enabled), complete=True)
        async with self._operation(actor=actor, project_id=project_id, write=True):
            await self._project(actor, project_id, write=True)
            adjustment_id = uuid4()
            row = await self.repository.create_adjustment(
                tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=adjustment_id,
                created_by=actor.user_id, fields=fields, as_of=self._now())
            if row is None:
                raise ConstructionProjectPricingAdjustmentOperationError("Created adjustment is invalid.")
            state = self._state(row, tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=adjustment_id)
            if (state.row_version != 1 or state.created_by != actor.user_id
                    or any(getattr(state, key) != value for key, value in fields.items())):
                raise ConstructionProjectPricingAdjustmentOperationError("Created adjustment is invalid.")
            await self._audit(actor, state, before=None, trace_id=trace_id)
            await self._authorize(actor, project_id, write=True)
            if manage_transaction:
                await self.session.commit()
            return ConstructionProjectPricingAdjustmentResult(state, True)

    async def update_adjustment(self, *, actor, project_id, adjustment_id, expected_row_version,
                                fields, trace_id=None, manage_transaction=True):
        self._inputs(project_id, adjustment_id=adjustment_id, manage_transaction=manage_transaction)
        if adjustment_id is None:
            raise ConstructionProjectPricingAdjustmentValidationError("An adjustment UUID is required.")
        expected_row_version, fields = positive_version(expected_row_version), validated_fields(fields)
        async with self._operation(actor=actor, project_id=project_id, write=True):
            await self._project(actor, project_id, write=True)
            row = await self.repository.get_adjustment(
                tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=adjustment_id, for_update=True)
            await self._authorize(actor, project_id, write=True)
            before = self._state(row, tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=adjustment_id)
            require_expected_row_version(actual_row_version=before.row_version, expected_row_version=expected_row_version)
            changes = {key: value for key, value in fields.items() if getattr(before, key) != value}
            if not changes:
                if manage_transaction:
                    await self.session.commit()
                return ConstructionProjectPricingAdjustmentResult(before, False)
            if before.row_version == MAX_VERSION:
                raise ConstructionProjectPricingAdjustmentValidationError("Adjustment version is exhausted.")
            row = await self.repository.update_adjustment(
                tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=adjustment_id,
                expected_row_version=before.row_version, fields=changes, as_of=self._now())
            if row is None:
                raise ConstructionRowVersionConflictError()
            state = self._state(row, tenant_id=actor.tenant_id, project_id=project_id, adjustment_id=adjustment_id)
            if (state.row_version != before.row_version + 1 or state.created_by != before.created_by
                    or state.created_at != before.created_at
                    or any(getattr(state, key) != changes.get(key, getattr(before, key)) for key in EDITABLE_FIELDS)):
                raise ConstructionProjectPricingAdjustmentOperationError("Conditional adjustment result is invalid.")
            await self._audit(actor, state, before=before, trace_id=trace_id)
            await self._authorize(actor, project_id, write=True)
            if manage_transaction:
                await self.session.commit()
            return ConstructionProjectPricingAdjustmentResult(state, True)

    async def _audit(self, actor, state, *, before, trace_id):
        def value(source, key):
            raw = getattr(source, key)
            return str(raw) if isinstance(raw, Decimal) else raw
        await self.audit.record_operation(
            actor=actor,
            event_type="construction_project_pricing_adjustment_created" if before is None else "construction_project_pricing_adjustment_updated",
            entity_type="construction_project_pricing_adjustment", entity_id=state.id,
            operation="create" if before is None else "update", trace_id=trace_id,
            changes=tuple(ConstructionAuditChange(
                key, None if before is None else value(before, key), value(state, key)
            ) for key in EDITABLE_FIELDS if before is None or getattr(before, key) != getattr(state, key)),
            details={"project_id": str(state.project_id), "row_version": state.row_version,
                     "previous_row_version": None if before is None else before.row_version})


def build_construction_project_pricing_adjustment_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_project_pricing_adjustments import ConstructionProjectPricingAdjustmentRepository
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    return ConstructionProjectPricingAdjustmentService(
        session=session, repository=ConstructionProjectPricingAdjustmentRepository(session),
        access_repository=ConstructionAccessRepository(session), event_repository=EventRepository(session),
        serialization=PlatformMembershipSerializationRepository(session), now_provider=now_provider)
