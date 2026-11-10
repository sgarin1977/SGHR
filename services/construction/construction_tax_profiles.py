"""CON-015 tax profiles. Mutations own their final commit unless manage_transaction=False prepares an outer workflow.

Use a dedicated Construction session with its trusted tenant RLS context already
set. Suite/Core and transport rate limits belong to the future HTTP boundary.
No rates, default-profile assignment or historical document rewrites occur here.
"""
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from services.construction.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_access,
)

MAX_VERSION = 2**63 - 1
EDITABLE_FIELDS = ("name", "client_price_tax_mode", "material_cost_basis")
CLIENT_MODES = frozenset({"TAX_EXCLUSIVE", "TAX_INCLUSIVE"})
MATERIAL_BASES = frozenset({"NET", "GROSS"})


class ConstructionTaxProfileValidationError(ValueError):
    pass


class ConstructionTaxProfileNotFoundError(Exception):
    pass


class ConstructionTaxProfileOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionTaxProfileState:
    id: UUID
    tenant_id: UUID
    name: str
    client_price_tax_mode: str
    material_cost_basis: str
    row_version: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConstructionTaxProfileResult:
    profile: ConstructionTaxProfileState
    changed: bool


@dataclass(frozen=True)
class ConstructionTaxProfileCursor:
    tenant_id: UUID
    user_id: UUID
    after_id: UUID

    def __post_init__(self):
        if not all(isinstance(getattr(self, name), UUID)
                   for name in ("tenant_id", "user_id", "after_id")):
            raise ConstructionTaxProfileValidationError("Tax profile cursor requires UUIDs.")


@dataclass(frozen=True)
class ConstructionTaxProfilePage:
    items: tuple[ConstructionTaxProfileState, ...]
    next_cursor: ConstructionTaxProfileCursor | None


def validated_fields(values, *, complete=False):
    if not isinstance(values, Mapping) or not set(values).issubset(EDITABLE_FIELDS):
        raise ConstructionTaxProfileValidationError("Unknown tax profile field.")
    if complete and set(values) != set(EDITABLE_FIELDS):
        raise ConstructionTaxProfileValidationError("Name and both tax modes are required.")
    result = dict(values)
    if "name" in result:
        value = result["name"]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 255 or "\x00" in value:
            raise ConstructionTaxProfileValidationError("Tax profile name is invalid.")
        result["name"] = value.strip()
    for key, allowed in (("client_price_tax_mode", CLIENT_MODES), ("material_cost_basis", MATERIAL_BASES)):
        if key in result and (not isinstance(result[key], str) or result[key] not in allowed):
            raise ConstructionTaxProfileValidationError("An explicit valid tax mode is required.")
    return result


def positive_version(value):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ConstructionTaxProfileValidationError("A positive BIGINT version is required.")
    return value


class ConstructionTaxProfileService:
    def __init__(self, *, session, repository, access_repository, event_repository,
                 serialization, now_provider=None):
        repositories = (
            (repository, ("get_profile", "create_profile", "update_profile")),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
            (event_repository, ("create_event",)),
            (serialization, ("lock_member",)),
        )
        if (
            session is None
            or any(not callable(getattr(session, name, None)) for name in
                   ("in_transaction", "get_nested_transaction", "expire_all", "commit", "rollback"))
            or not hasattr(session, "no_autoflush")
            or any(getattr(repo, "session", None) is not session
                   or any(not callable(getattr(repo, name, None)) for name in names)
                   for repo, names in repositories)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Tax profiles require shared-session transaction wiring.")
        self.session, self.repository, self.serialization = session, repository, serialization
        self.access_repository = access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionTaxProfileOperationError("Tax profile clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor):
        if (getattr(actor, "status", None) != "active"
                or not isinstance(getattr(actor, "tenant_id", None), UUID)
                or not isinstance(getattr(actor, "user_id", None), UUID)):
            raise ConstructionPermissionDeniedError("Tax profile identity is unavailable.")

    async def _authorize(self, actor, permission):
        self._actor(actor)
        # Drop cached ORM grant/user state before every fresh authorization query.
        self.session.expire_all()
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Tax profile membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        # No project/document scope is supplied: this is an organization resource.
        require_construction_access(actor=actor, access_context=context, permission_code=permission)

    @asynccontextmanager
    async def _operation(self, *, actor, write):
        self._actor(actor)
        # Refuse a nested or already-dirty session before taking transaction ownership.
        if (self.session.in_transaction() is not True
                or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionTaxProfileOperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                if write:
                    # Shared actor membership/ownership barrier ALWAYS precedes the profile row lock.
                    await self.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self._authorize(actor, "construction.tax.edit" if write else "construction.tax.read")
                yield
        except BaseException as error:
            try:
                await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionTaxProfileOperationError("Tax profile rollback failed.") from None
            if isinstance(error, (ConstructionTaxProfileValidationError, ConstructionTaxProfileNotFoundError,
                                  ConstructionTaxProfileOperationError, ConstructionRowVersionConflictError,
                                  ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
                raise
            if not isinstance(error, Exception):  # Cancellation must also roll back and propagate.
                raise
            raise ConstructionTaxProfileOperationError("Tax profile operation failed.") from None

    @staticmethod
    def _state(row, *, tenant_id, profile_id):
        if row is None:
            raise ConstructionTaxProfileNotFoundError("Tax profile is unavailable.")
        try:
            state = ConstructionTaxProfileState(**row)
            fields = {key: getattr(state, key) for key in EDITABLE_FIELDS}
            if (state.id != profile_id or state.tenant_id != tenant_id
                    or not all(isinstance(getattr(state, key), UUID) for key in ("id", "tenant_id", "created_by"))
                    or positive_version(state.row_version) != state.row_version
                    or validated_fields(fields, complete=True) != fields
                    or any(not isinstance(value, datetime) or value.tzinfo is None
                           or value.utcoffset() is None for value in (state.created_at, state.updated_at))):
                raise ValueError()
            return state
        except (TypeError, ValueError):
            raise ConstructionTaxProfileOperationError("Stored tax profile is invalid.") from None

    async def get_profile(self, *, actor, profile_id, lock_for_review=False):
        if not isinstance(profile_id, UUID):
            raise ConstructionTaxProfileValidationError("A profile UUID is required.")
        if type(lock_for_review) is not bool:
            raise ConstructionTaxProfileValidationError("Profile read mode is invalid.")
        async with self._operation(actor=actor, write=False):
            options = {"for_share": True} if lock_for_review else {}
            row = await self.repository.get_profile(
                tenant_id=actor.tenant_id, profile_id=profile_id, **options)
            await self._authorize(actor, "construction.tax.read")
            return self._state(row, tenant_id=actor.tenant_id, profile_id=profile_id)

    async def list_profiles(self, *, actor, limit=20, after=None):
        self._actor(actor)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConstructionTaxProfileValidationError("Tax profile limit must be from 1 to 100.")
        if after is not None:
            if not isinstance(after, ConstructionTaxProfileCursor):
                raise ConstructionTaxProfileValidationError("Invalid tax profile cursor.")
            after = ConstructionTaxProfileCursor(after.tenant_id, after.user_id, after.after_id)
            if (after.tenant_id, after.user_id) != (actor.tenant_id, actor.user_id):
                raise ConstructionTaxProfileValidationError("Tax profile cursor context differs.")
        async with self._operation(actor=actor, write=False):
            rows = await self.repository.list_profiles(
                tenant_id=actor.tenant_id, limit=limit + 1,
                after_id=None if after is None else after.after_id)
            await self._authorize(actor, "construction.tax.read")
            if not isinstance(rows, (tuple, list)) or len(rows) > limit + 1:
                raise ConstructionTaxProfileOperationError("Tax profile page is invalid.")
            states = []
            previous = None if after is None else after.after_id
            for row in rows:
                if not isinstance(row, Mapping) or not isinstance(row.get("id"), UUID):
                    raise ConstructionTaxProfileOperationError("Tax profile row is invalid.")
                state = self._state(row, tenant_id=actor.tenant_id, profile_id=row["id"])
                if previous is not None and state.id <= previous:
                    raise ConstructionTaxProfileOperationError("Tax profile order is invalid.")
                states.append(state)
                previous = state.id
            items = tuple(states[:limit])
            cursor = None
            if len(states) > limit:
                cursor = ConstructionTaxProfileCursor(actor.tenant_id, actor.user_id, items[-1].id)
            return ConstructionTaxProfilePage(items, cursor)

    async def create_profile(self, *, actor, name, client_price_tax_mode, material_cost_basis, trace_id=None, manage_transaction=True):
        if type(manage_transaction) is not bool:
            raise ConstructionTaxProfileValidationError("Transaction management flag is invalid.")
        fields = validated_fields(dict(name=name, client_price_tax_mode=client_price_tax_mode,
                                       material_cost_basis=material_cost_basis), complete=True)
        async with self._operation(actor=actor, write=True):
            profile_id, instant = uuid4(), self._now()
            state = self._state(await self.repository.create_profile(
                tenant_id=actor.tenant_id, profile_id=profile_id, created_by=actor.user_id,
                fields=fields, as_of=instant,
            ), tenant_id=actor.tenant_id, profile_id=profile_id)
            if (state.row_version != 1 or state.created_by != actor.user_id
                    or any(getattr(state, key) != value for key, value in fields.items())):
                raise ConstructionTaxProfileOperationError("Created tax profile is invalid.")
            await self._audit(actor, state, before=None, trace_id=trace_id)
            await self._authorize(actor, "construction.tax.edit")
            if manage_transaction:
                await self.session.commit()
            return ConstructionTaxProfileResult(state, True)

    async def update_profile(self, *, actor, profile_id, expected_row_version, fields, trace_id=None, manage_transaction=True):
        if type(manage_transaction) is not bool:
            raise ConstructionTaxProfileValidationError("Transaction management flag is invalid.")
        if not isinstance(profile_id, UUID):
            raise ConstructionTaxProfileValidationError("A profile UUID is required.")
        expected_row_version, fields = positive_version(expected_row_version), validated_fields(fields)
        async with self._operation(actor=actor, write=True):
            before = self._state(await self.repository.get_profile(
                tenant_id=actor.tenant_id, profile_id=profile_id, for_update=True,
            ), tenant_id=actor.tenant_id, profile_id=profile_id)
            await self._authorize(actor, "construction.tax.edit")  # Refresh after the row-lock wait.
            require_expected_row_version(actual_row_version=before.row_version,
                                         expected_row_version=expected_row_version)
            changes = {key: value for key, value in fields.items() if getattr(before, key) != value}
            if not changes:
                if manage_transaction:
                    await self.session.commit()  # Release locks; no UPDATE or audit.
                return ConstructionTaxProfileResult(before, False)
            if before.row_version == MAX_VERSION:
                raise ConstructionTaxProfileValidationError("Tax profile version is exhausted.")
            row = await self.repository.update_profile(
                tenant_id=actor.tenant_id, profile_id=profile_id, expected_row_version=before.row_version,
                fields=changes, as_of=self._now(),
            )
            if row is None:
                raise ConstructionRowVersionConflictError()
            state = self._state(row, tenant_id=actor.tenant_id, profile_id=profile_id)
            if (state.row_version != before.row_version + 1
                    or state.created_by != before.created_by or state.created_at != before.created_at
                    or any(getattr(state, key) != changes.get(key, getattr(before, key)) for key in EDITABLE_FIELDS)):
                raise ConstructionTaxProfileOperationError("Conditional tax profile result is invalid.")
            await self._audit(actor, state, before=before, trace_id=trace_id)
            await self._authorize(actor, "construction.tax.edit")
            if manage_transaction:
                await self.session.commit()
            return ConstructionTaxProfileResult(state, True)

    async def _audit(self, actor, state, *, before, trace_id):
        await self.audit.record_operation(
            actor=actor, event_type="construction_tax_profile_created" if before is None else "construction_tax_profile_updated",
            entity_type="construction_tax_profile", entity_id=state.id,
            operation="create" if before is None else "update", trace_id=trace_id,
            changes=tuple(ConstructionAuditChange(key, None if before is None else getattr(before, key),
                                                  getattr(state, key))
                          for key in EDITABLE_FIELDS if before is None or getattr(before, key) != getattr(state, key)),
            details={"row_version": state.row_version, "previous_row_version": None if before is None else before.row_version},
        )


def build_construction_tax_profile_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_tax_profiles import ConstructionTaxProfileRepository
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    return ConstructionTaxProfileService(
        session=session, repository=ConstructionTaxProfileRepository(session),
        access_repository=ConstructionAccessRepository(session), event_repository=EventRepository(session),
        serialization=PlatformMembershipSerializationRepository(session), now_provider=now_provider,
    )
