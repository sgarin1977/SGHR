"""CON-017 pricing configuration, not a document calculation engine.

Dedicated trusted tenant session; shared membership barrier before row locks.
Default writes commit once; manage_transaction=False prepares an outer workflow.
No automatic rates, module activation, price calculation or historical rewrites.
"""
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy.exc import IntegrityError

from services.construction.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_access)

MAX_VERSION = 2**63 - 1
LEVELS = ("ECONOMY", "STANDARD", "PREMIUM")
EDITABLE_FIELDS = ("work_coefficient", "material_markup_percent")
ALL_FIELDS = ("code",) + EDITABLE_FIELDS


class ConstructionPricingProfileValidationError(ValueError):
    pass


class ConstructionPricingProfileNotFoundError(Exception):
    pass


class ConstructionPricingProfileConflictError(Exception):
    pass


class ConstructionPricingProfileOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionPricingProfileState:
    id: UUID
    tenant_id: UUID
    code: str
    work_coefficient: Decimal
    material_markup_percent: Decimal
    row_version: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConstructionPricingProfileResult:
    profile: ConstructionPricingProfileState
    changed: bool


def validated_fields(values, *, complete=False):
    allowed = ALL_FIELDS if complete else EDITABLE_FIELDS
    if (not isinstance(values, Mapping) or not set(values).issubset(allowed)
            or (complete and set(values) != set(ALL_FIELDS))):
        raise ConstructionPricingProfileValidationError("Explicit valid pricing fields are required.")
    result = dict(values)
    if complete and (not isinstance(result["code"], str) or result["code"] not in LEVELS):
        raise ConstructionPricingProfileValidationError("An explicit price level is required.")
    for name in EDITABLE_FIELDS:
        if name not in result:
            continue
        value = result[name]
        if (not isinstance(value, Decimal) or not value.is_finite()
                or (name == "work_coefficient" and value <= 0)
                or (name == "material_markup_percent" and not Decimal(0) <= value <= Decimal(100))):
            raise ConstructionPricingProfileValidationError("A finite Decimal within the pricing bounds is required.")
    return result


def positive_version(value):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ConstructionPricingProfileValidationError("A positive BIGINT version is required.")
    return value


def _duplicate_level(error):
    current = error.orig if isinstance(error, IntegrityError) else None
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        diagnostic = getattr(current, "diag", None)
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        name = getattr(current, "constraint_name", None) or getattr(diagnostic, "constraint_name", None)
        if code == "23505" and name == "uq_construction_pricing_profiles_tenant_code":
            return True
        current = getattr(current, "__cause__", None)
    return False


class ConstructionPricingProfileService:
    def __init__(self, *, session, repository, access_repository, event_repository,
                 serialization, now_provider=None):
        repositories = (
            (repository, ("get_profile", "get_profile_by_code", "list_profiles", "create_profile", "update_profile")),
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
            raise ValueError("Pricing profiles require shared-session transaction wiring.")
        self.session, self.repository, self.serialization = session, repository, serialization
        self.access_repository = access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionPricingProfileOperationError("Pricing clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor):
        if (getattr(actor, "status", None) != "active"
                or not isinstance(getattr(actor, "tenant_id", None), UUID)
                or not isinstance(getattr(actor, "user_id", None), UUID)):
            raise ConstructionPermissionDeniedError("Pricing identity is unavailable.")

    async def _authorize(self, actor, permission):
        self._actor(actor)
        self.session.expire_all()
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id
        ) is not True:
            raise ConstructionPermissionDeniedError("Pricing membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        require_construction_access(actor=actor, access_context=context, permission_code=permission)

    @asynccontextmanager
    async def _operation(self, *, actor, write):
        self._actor(actor)
        if (self.session.in_transaction() is not True
                or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionPricingProfileOperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                if write:
                    await self.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self._authorize(actor, "construction.pricing.edit" if write else "construction.pricing.read")
                yield
        except BaseException as error:
            try:
                await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionPricingProfileOperationError("Pricing rollback failed.") from None
            if isinstance(error, (ConstructionPricingProfileValidationError,
                                  ConstructionPricingProfileNotFoundError, ConstructionPricingProfileConflictError,
                                  ConstructionPricingProfileOperationError, ConstructionRowVersionConflictError,
                                  ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
                raise
            if _duplicate_level(error):
                raise ConstructionPricingProfileConflictError("This organization price level already exists.") from None
            if not isinstance(error, Exception):
                raise
            raise ConstructionPricingProfileOperationError("Pricing profile operation failed.") from None

    @staticmethod
    def _state(row, *, tenant_id, profile_id):
        if row is None:
            raise ConstructionPricingProfileNotFoundError("Pricing profile is unavailable.")
        try:
            state = ConstructionPricingProfileState(**row)
            values = {key: getattr(state, key) for key in ALL_FIELDS}
            if (state.id != profile_id or state.tenant_id != tenant_id
                    or not all(isinstance(getattr(state, key), UUID) for key in ("id", "tenant_id", "created_by"))
                    or positive_version(state.row_version) != state.row_version
                    or validated_fields(values, complete=True) != values
                    or any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
                           for value in (state.created_at, state.updated_at))):
                raise ValueError()
            return state
        except (TypeError, ValueError):
            raise ConstructionPricingProfileOperationError("Stored pricing profile is invalid.") from None

    async def get_profile(self, *, actor, profile_id):
        if not isinstance(profile_id, UUID):
            raise ConstructionPricingProfileValidationError("A profile UUID is required.")
        async with self._operation(actor=actor, write=False):
            row = await self.repository.get_profile(tenant_id=actor.tenant_id, profile_id=profile_id)
            await self._authorize(actor, "construction.pricing.read")
            return self._state(row, tenant_id=actor.tenant_id, profile_id=profile_id)

    async def list_profiles(self, *, actor):
        async with self._operation(actor=actor, write=False):
            rows = await self.repository.list_profiles(tenant_id=actor.tenant_id)
            await self._authorize(actor, "construction.pricing.read")
            if not isinstance(rows, (list, tuple)) or len(rows) > len(LEVELS):
                raise ConstructionPricingProfileOperationError("Pricing list is invalid.")
            states = []
            for row in rows:
                if not isinstance(row, Mapping) or not isinstance(row.get("id"), UUID):
                    raise ConstructionPricingProfileOperationError("Pricing list is invalid.")
                states.append(self._state(row, tenant_id=actor.tenant_id, profile_id=row["id"]))
            codes = [state.code for state in states]
            if len(set(codes)) != len(codes) or codes != sorted(codes, key=LEVELS.index):
                raise ConstructionPricingProfileOperationError("Pricing order is invalid.")
            return tuple(states)

    async def create_profile(self, *, actor, code, work_coefficient, material_markup_percent,
                             trace_id=None, manage_transaction=True):
        if type(manage_transaction) is not bool:
            raise ConstructionPricingProfileValidationError("Transaction mode is invalid.")
        fields = validated_fields(dict(code=code, work_coefficient=work_coefficient,
                                       material_markup_percent=material_markup_percent), complete=True)
        async with self._operation(actor=actor, write=True):
            existing = await self.repository.get_profile_by_code(tenant_id=actor.tenant_id, code=code)
            await self._authorize(actor, "construction.pricing.edit")
            if existing is not None:
                existing_state = self._state(existing, tenant_id=actor.tenant_id, profile_id=existing.get("id"))
                if existing_state.code != code:
                    raise ConstructionPricingProfileOperationError("Stored pricing context is invalid.")
                raise ConstructionPricingProfileConflictError("This organization price level already exists.")
            profile_id = uuid4()
            row = await self.repository.create_profile(
                tenant_id=actor.tenant_id, profile_id=profile_id, created_by=actor.user_id,
                fields=fields, as_of=self._now())
            if row is None:
                raise ConstructionPricingProfileOperationError("Created pricing profile is invalid.")
            state = self._state(row, tenant_id=actor.tenant_id, profile_id=profile_id)
            if (state.row_version != 1 or state.created_by != actor.user_id
                    or any(getattr(state, key) != value for key, value in fields.items())):
                raise ConstructionPricingProfileOperationError("Created pricing profile is invalid.")
            await self._audit(actor, state, before=None, trace_id=trace_id)
            await self._authorize(actor, "construction.pricing.edit")
            if manage_transaction:
                await self.session.commit()
            return ConstructionPricingProfileResult(state, True)

    async def update_profile(self, *, actor, profile_id, expected_row_version, fields,
                             trace_id=None, manage_transaction=True):
        if not isinstance(profile_id, UUID) or type(manage_transaction) is not bool:
            raise ConstructionPricingProfileValidationError("Profile context or transaction mode is invalid.")
        expected_row_version, fields = positive_version(expected_row_version), validated_fields(fields)
        async with self._operation(actor=actor, write=True):
            before = self._state(await self.repository.get_profile(
                tenant_id=actor.tenant_id, profile_id=profile_id, for_update=True
            ), tenant_id=actor.tenant_id, profile_id=profile_id)
            await self._authorize(actor, "construction.pricing.edit")
            require_expected_row_version(actual_row_version=before.row_version,
                                         expected_row_version=expected_row_version)
            changes = {key: value for key, value in fields.items() if getattr(before, key) != value}
            if not changes:
                if manage_transaction:
                    await self.session.commit()
                return ConstructionPricingProfileResult(before, False)
            if before.row_version == MAX_VERSION:
                raise ConstructionPricingProfileValidationError("Pricing profile version is exhausted.")
            row = await self.repository.update_profile(
                tenant_id=actor.tenant_id, profile_id=profile_id, expected_row_version=before.row_version,
                fields=changes, as_of=self._now())
            if row is None:
                raise ConstructionRowVersionConflictError()
            state = self._state(row, tenant_id=actor.tenant_id, profile_id=profile_id)
            if (state.row_version != before.row_version + 1 or state.code != before.code
                    or state.created_by != before.created_by or state.created_at != before.created_at
                    or any(getattr(state, key) != changes.get(key, getattr(before, key)) for key in EDITABLE_FIELDS)):
                raise ConstructionPricingProfileOperationError("Conditional pricing result is invalid.")
            await self._audit(actor, state, before=before, trace_id=trace_id)
            await self._authorize(actor, "construction.pricing.edit")
            if manage_transaction:
                await self.session.commit()
            return ConstructionPricingProfileResult(state, True)

    async def _audit(self, actor, state, *, before, trace_id):
        def value(source, key):
            raw = getattr(source, key)
            return str(raw) if isinstance(raw, Decimal) else raw
        await self.audit.record_operation(
            actor=actor,
            event_type="construction_pricing_profile_created" if before is None else "construction_pricing_profile_updated",
            entity_type="construction_pricing_profile", entity_id=state.id,
            operation="create" if before is None else "update", trace_id=trace_id,
            changes=tuple(ConstructionAuditChange(
                key, None if before is None else value(before, key), value(state, key)
            ) for key in ALL_FIELDS if before is None or getattr(before, key) != getattr(state, key)),
            details={"code": state.code, "row_version": state.row_version,
                     "previous_row_version": None if before is None else before.row_version})


def build_construction_pricing_profile_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_pricing_profiles import ConstructionPricingProfileRepository
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    return ConstructionPricingProfileService(
        session=session, repository=ConstructionPricingProfileRepository(session),
        access_repository=ConstructionAccessRepository(session), event_repository=EventRepository(session),
        serialization=PlatformMembershipSerializationRepository(session), now_provider=now_provider)
