"""CON-016 tax rules: trusted tenant session, fresh authority, atomic audit.

The default mutation commits once. manage_transaction=False prepares an outer
workflow; failure/cancellation still rolls back. Transport module/rate gates
and idempotency belong to the later HTTP/workflow boundary.
"""
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy.exc import IntegrityError

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
CATEGORIES = frozenset({"WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER"})
EDITABLE_FIELDS = ("category", "rate_percent")


class ConstructionTaxRuleValidationError(ValueError):
    pass


class ConstructionTaxRuleNotFoundError(Exception):
    pass


class ConstructionTaxRuleOperationError(Exception):
    pass


class ConstructionTaxRuleConflictError(Exception):
    pass


def _category_unique_violation(error):
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
        if code == "23505" and name == "uq_construction_tax_rules_profile_category":
            return True
        pending.extend([
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ])
    return False


@dataclass(frozen=True)
class ConstructionTaxRuleState:
    id: UUID
    tenant_id: UUID
    tax_profile_id: UUID
    category: str
    rate_percent: Decimal
    row_version: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConstructionTaxRuleResult:
    rule: ConstructionTaxRuleState
    changed: bool


@dataclass(frozen=True)
class ConstructionTaxRuleCursor:
    tenant_id: UUID
    user_id: UUID
    tax_profile_id: UUID
    after_id: UUID

    def __post_init__(self):
        if not all(isinstance(getattr(self, key), UUID) for key in
                   ("tenant_id", "user_id", "tax_profile_id", "after_id")):
            raise ConstructionTaxRuleValidationError("Tax rule cursor requires UUIDs.")


@dataclass(frozen=True)
class ConstructionTaxRulePage:
    items: tuple[ConstructionTaxRuleState, ...]
    next_cursor: ConstructionTaxRuleCursor | None


def validated_fields(values, *, complete=False):
    if not isinstance(values, Mapping) or not set(values).issubset(EDITABLE_FIELDS):
        raise ConstructionTaxRuleValidationError("Unknown tax rule field.")
    if complete and set(values) != set(EDITABLE_FIELDS):
        raise ConstructionTaxRuleValidationError("Category and rate are required.")
    result = dict(values)
    if "category" in result:
        value = result["category"]
        if not isinstance(value, str) or value not in CATEGORIES:
            raise ConstructionTaxRuleValidationError("An explicit tax category is required.")
    if "rate_percent" in result:
        value = result["rate_percent"]
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or not Decimal(0) <= value <= Decimal(100)
        ):
            raise ConstructionTaxRuleValidationError(
                "A finite Decimal tax rate from 0 to 100 is required."
            )
    return result


def positive_version(value):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ConstructionTaxRuleValidationError("A positive BIGINT version is required.")
    return value


class ConstructionTaxRuleService:
    def __init__(
        self, *, session, repository, access_repository, event_repository,
        serialization, now_provider=None,
    ):
        repositories = (
            (repository, ("get_profile_id", "get_rule", "get_rule_id_by_category", "create_rule", "update_rule")),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
            (event_repository, ("create_event",)),
            (serialization, ("lock_member",)),
        )
        if (
            session is None
            or any(not callable(getattr(session, name, None)) for name in (
                "in_transaction", "get_nested_transaction",
                "expire_all", "commit", "rollback",
            ))
            or not hasattr(session, "no_autoflush")
            or any(
                getattr(repo, "session", None) is not session
                or any(not callable(getattr(repo, name, None)) for name in names)
                for repo, names in repositories
            )
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Tax rules require shared-session transaction wiring.")
        self.session = session
        self.repository = repository
        self.serialization = serialization
        self.access_repository = access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(
            repository=access_repository, now_provider=self._now,
        )
        self.audit = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ConstructionTaxRuleOperationError("Tax rule clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(getattr(actor, "user_id", None), UUID)
        ):
            raise ConstructionPermissionDeniedError("Tax rule identity is unavailable.")

    async def _authorize(self, actor, permission):
        self._actor(actor)
        self.session.expire_all()
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Tax rule membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        require_construction_access(
            actor=actor, access_context=context, permission_code=permission,
        )

    @asynccontextmanager
    async def _operation(self, *, actor, write):
        self._actor(actor)
        if (
            self.session.in_transaction() is not True
            or self.session.get_nested_transaction() is not None
            or any(bool(getattr(self.session, name, ()))
                   for name in ("new", "dirty", "deleted"))
        ):
            raise ConstructionTaxRuleOperationError(
                "A clean active outer transaction is required."
            )
        try:
            with self.session.no_autoflush:
                if write:
                    await self.serialization.lock_member(
                        tenant_id=actor.tenant_id, user_id=actor.user_id,
                    )
                await self._authorize(
                    actor, "construction.tax.edit" if write else "construction.tax.read",
                )
                yield
        except BaseException as error:
            try:
                await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionTaxRuleOperationError(
                    "Tax rule rollback failed."
                ) from None
            if isinstance(error, (
                ConstructionTaxRuleValidationError,
                ConstructionTaxRuleNotFoundError,
                ConstructionTaxRuleConflictError,
                ConstructionTaxRuleOperationError,
                ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError,
                ConstructionScopeDeniedError,
            )):
                raise
            if _category_unique_violation(error):
                raise ConstructionTaxRuleConflictError(
                    "A rule for this tax category already exists."
                ) from None
            if not isinstance(error, Exception):
                raise
            raise ConstructionTaxRuleOperationError(
                "Tax rule operation failed."
            ) from None

    @staticmethod
    def _inputs(profile_id, rule_id=None, manage_transaction=True):
        if (
            not isinstance(profile_id, UUID)
            or (rule_id is not None and not isinstance(rule_id, UUID))
            or type(manage_transaction) is not bool
        ):
            raise ConstructionTaxRuleValidationError(
                "UUID context and transaction mode are invalid."
            )

    async def _profile(self, actor, profile_id, *, write):
        found = await self.repository.get_profile_id(
            tenant_id=actor.tenant_id, profile_id=profile_id, for_update=write,
        )
        await self._authorize(
            actor, "construction.tax.edit" if write else "construction.tax.read",
        )
        if found is None:
            raise ConstructionTaxRuleNotFoundError(
                "Tax profile or rule is unavailable."
            )
        if not isinstance(found, UUID) or found != profile_id:
            raise ConstructionTaxRuleOperationError(
                "Tax profile projection is invalid."
            )

    @staticmethod
    def _state(row, *, tenant_id, profile_id, rule_id):
        if row is None:
            raise ConstructionTaxRuleNotFoundError("Tax profile or rule is unavailable.")
        try:
            state = ConstructionTaxRuleState(**row)
            fields = {key: getattr(state, key) for key in EDITABLE_FIELDS}
            if (
                state.id != rule_id
                or state.tenant_id != tenant_id
                or state.tax_profile_id != profile_id
                or not all(
                    isinstance(getattr(state, key), UUID)
                    for key in ("id", "tenant_id", "tax_profile_id", "created_by")
                )
                or positive_version(state.row_version) != state.row_version
                or validated_fields(fields, complete=True) != fields
                or any(
                    not isinstance(value, datetime)
                    or value.tzinfo is None
                    or value.utcoffset() is None
                    for value in (state.created_at, state.updated_at)
                )
            ):
                raise ValueError()
            return state
        except (TypeError, ValueError):
            raise ConstructionTaxRuleOperationError(
                "Stored tax rule is invalid."
            ) from None

    async def get_rule(self, *, actor, profile_id, rule_id):
        self._inputs(profile_id, rule_id)
        if rule_id is None:
            raise ConstructionTaxRuleValidationError("A rule UUID is required.")
        async with self._operation(actor=actor, write=False):
            await self._profile(actor, profile_id, write=False)
            row = await self.repository.get_rule(
                tenant_id=actor.tenant_id, profile_id=profile_id, rule_id=rule_id,
            )
            await self._authorize(actor, "construction.tax.read")
            return self._state(
                row, tenant_id=actor.tenant_id,
                profile_id=profile_id, rule_id=rule_id,
            )

    async def _category_available(self, actor, profile_id, category, *, exclude_id=None):
        found = await self.repository.get_rule_id_by_category(
            tenant_id=actor.tenant_id, profile_id=profile_id, category=category)
        await self._authorize(actor, "construction.tax.edit")
        if found is not None:
            if not isinstance(found, UUID):
                raise ConstructionTaxRuleOperationError("Tax category projection is invalid.")
            if found != exclude_id:
                raise ConstructionTaxRuleConflictError(
                    "A rule for this tax category already exists.")

    async def create_rule(
        self, *, actor, profile_id, category, rate_percent,
        trace_id=None, manage_transaction=True,
    ):
        self._inputs(profile_id, manage_transaction=manage_transaction)
        fields = validated_fields(
            dict(category=category, rate_percent=rate_percent), complete=True,
        )
        async with self._operation(actor=actor, write=True):
            # Membership barrier -> parent profile -> rule.
            await self._profile(actor, profile_id, write=True)
            await self._category_available(actor, profile_id, fields["category"])
            rule_id = uuid4()
            state = self._state(
                await self.repository.create_rule(
                    tenant_id=actor.tenant_id, profile_id=profile_id,
                    rule_id=rule_id, created_by=actor.user_id,
                    fields=fields, as_of=self._now(),
                ),
                tenant_id=actor.tenant_id,
                profile_id=profile_id, rule_id=rule_id,
            )
            if (
                state.row_version != 1
                or state.created_by != actor.user_id
                or any(getattr(state, key) != value for key, value in fields.items())
            ):
                raise ConstructionTaxRuleOperationError("Created tax rule is invalid.")
            await self._audit(actor, state, before=None, trace_id=trace_id)
            await self._authorize(actor, "construction.tax.edit")
            if manage_transaction:
                await self.session.commit()
            return ConstructionTaxRuleResult(state, True)

    async def update_rule(
        self, *, actor, profile_id, rule_id, expected_row_version,
        fields, trace_id=None, manage_transaction=True,
    ):
        self._inputs(profile_id, rule_id, manage_transaction)
        if rule_id is None:
            raise ConstructionTaxRuleValidationError("A rule UUID is required.")
        expected_row_version = positive_version(expected_row_version)
        fields = validated_fields(fields)
        async with self._operation(actor=actor, write=True):
            await self._profile(actor, profile_id, write=True)
            before = self._state(
                await self.repository.get_rule(
                    tenant_id=actor.tenant_id, profile_id=profile_id,
                    rule_id=rule_id, for_update=True,
                ),
                tenant_id=actor.tenant_id,
                profile_id=profile_id, rule_id=rule_id,
            )
            await self._authorize(actor, "construction.tax.edit")
            require_expected_row_version(
                actual_row_version=before.row_version,
                expected_row_version=expected_row_version,
            )
            changes = {
                key: value for key, value in fields.items()
                if getattr(before, key) != value
            }
            if not changes:
                if manage_transaction:
                    await self.session.commit()
                return ConstructionTaxRuleResult(before, False)
            if before.row_version == MAX_VERSION:
                raise ConstructionTaxRuleValidationError(
                    "Tax rule version is exhausted."
                )
            if "category" in changes:
                await self._category_available(
                    actor, profile_id, changes["category"], exclude_id=before.id)
            row = await self.repository.update_rule(
                tenant_id=actor.tenant_id, profile_id=profile_id, rule_id=rule_id,
                expected_row_version=before.row_version,
                fields=changes, as_of=self._now(),
            )
            if row is None:
                raise ConstructionRowVersionConflictError()
            state = self._state(
                row, tenant_id=actor.tenant_id,
                profile_id=profile_id, rule_id=rule_id,
            )
            if (
                state.row_version != before.row_version + 1
                or state.created_by != before.created_by
                or state.created_at != before.created_at
                or any(
                    getattr(state, key) != changes.get(key, getattr(before, key))
                    for key in EDITABLE_FIELDS
                )
            ):
                raise ConstructionTaxRuleOperationError(
                    "Conditional tax rule result is invalid."
                )
            await self._audit(actor, state, before=before, trace_id=trace_id)
            await self._authorize(actor, "construction.tax.edit")
            if manage_transaction:
                await self.session.commit()
            return ConstructionTaxRuleResult(state, True)

    async def list_rules(self, *, actor, profile_id, limit=20, after=None):
        self._actor(actor)
        self._inputs(profile_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConstructionTaxRuleValidationError("Tax rule limit must be from 1 to 100.")
        if after is not None:
            if not isinstance(after, ConstructionTaxRuleCursor):
                raise ConstructionTaxRuleValidationError("Invalid tax rule cursor.")
            after = ConstructionTaxRuleCursor(
                after.tenant_id, after.user_id, after.tax_profile_id, after.after_id,
            )
            if (after.tenant_id, after.user_id, after.tax_profile_id) != (
                actor.tenant_id, actor.user_id, profile_id,
            ):
                raise ConstructionTaxRuleValidationError("Tax rule cursor context differs.")
        async with self._operation(actor=actor, write=False):
            await self._profile(actor, profile_id, write=False)
            rows = await self.repository.list_rules(
                tenant_id=actor.tenant_id, profile_id=profile_id,
                limit=limit + 1, after_id=None if after is None else after.after_id,
            )
            await self._authorize(actor, "construction.tax.read")
            if not isinstance(rows, (tuple, list)) or len(rows) > limit + 1:
                raise ConstructionTaxRuleOperationError("Tax rule page is invalid.")
            states = []
            previous = None if after is None else after.after_id
            for row in rows:
                if not isinstance(row, Mapping) or not isinstance(row.get("id"), UUID):
                    raise ConstructionTaxRuleOperationError("Tax rule page row is invalid.")
                state = self._state(
                    row, tenant_id=actor.tenant_id, profile_id=profile_id, rule_id=row["id"],
                )
                if previous is not None and state.id <= previous:
                    raise ConstructionTaxRuleOperationError("Tax rule page order is invalid.")
                states.append(state)
                previous = state.id
            items = tuple(states[:limit])
            next_cursor = None
            if len(states) > limit:
                next_cursor = ConstructionTaxRuleCursor(
                    actor.tenant_id, actor.user_id, profile_id, items[-1].id,
                )
            return ConstructionTaxRulePage(items, next_cursor)

    async def _audit(self, actor, state, *, before, trace_id):
        def value(item, key):
            raw = getattr(item, key)
            return str(raw) if isinstance(raw, Decimal) else raw

        await self.audit.record_operation(
            actor=actor,
            event_type=(
                "construction_tax_rule_created"
                if before is None else "construction_tax_rule_updated"
            ),
            entity_type="construction_tax_rule", entity_id=state.id,
            operation="create" if before is None else "update",
            trace_id=trace_id,
            changes=tuple(
                ConstructionAuditChange(
                    key,
                    None if before is None else value(before, key),
                    value(state, key),
                )
                for key in EDITABLE_FIELDS
                if before is None or getattr(before, key) != getattr(state, key)
            ),
            details={
                "tax_profile_id": str(state.tax_profile_id),
                "row_version": state.row_version,
                "previous_row_version": None if before is None else before.row_version,
            },
        )


def build_construction_tax_rule_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_tax_rules import (
        ConstructionTaxRuleRepository,
    )
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import (
        PlatformMembershipSerializationRepository,
    )
    return ConstructionTaxRuleService(
        session=session, repository=ConstructionTaxRuleRepository(session),
        access_repository=ConstructionAccessRepository(session),
        event_repository=EventRepository(session),
        serialization=PlatformMembershipSerializationRepository(session),
        now_provider=now_provider,
    )

# con016_canonical_categories_unique_v1
