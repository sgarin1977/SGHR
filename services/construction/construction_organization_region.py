"""Organization country/timezone in existing Platform storage; no schema or API registration."""
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.api_identity import ApiActorContext
from services.construction.construction_organization_language import (
    ConstructionOrganizationLanguageAuthorizer, OrganizationLanguageDenied)

MAX_VERSION = 2**63 - 1
REGION_KEY = "regional_settings"


class OrganizationRegionValidationError(ValueError):
    pass


class OrganizationRegionDenied(Exception):
    pass


class OrganizationRegionConfigurationError(Exception):
    pass


class OrganizationRegionConflict(Exception):
    code = "ROW_VERSION_CONFLICT"


class OrganizationRegionOperationError(Exception):
    pass


def validate_region(*, country_code, timezone_code):
    if (type(country_code) is not str or len(country_code) != 2
            or not country_code.isascii() or not country_code.isalpha()
            or country_code != country_code.upper()):
        raise OrganizationRegionValidationError("An exact Platform country code is required.")
    if (type(timezone_code) is not str or not 1 <= len(timezone_code) <= 255
            or timezone_code != timezone_code.strip()
            or any(char.isspace() for char in timezone_code)
            or timezone_code in {"localtime", "posixrules"}
            or timezone_code.startswith(("posix/", "right/"))):
        raise OrganizationRegionValidationError("An explicit IANA timezone is required.")
    try:
        ZoneInfo(timezone_code)
    except (ZoneInfoNotFoundError, ValueError):
        raise OrganizationRegionValidationError("The IANA timezone is unavailable.") from None
    return {"country_code": country_code, "timezone": timezone_code}


def _version(value):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise OrganizationRegionValidationError("A positive BIGINT version is required.")
    return value


@dataclass(frozen=True)
class OrganizationRegionState:
    tenant_id: UUID
    country_code: str | None
    timezone: str | None
    row_version: int
    updated_at: datetime

    @property
    def configured(self):
        return self.country_code is not None and self.timezone is not None


@dataclass(frozen=True)
class OrganizationRegionResult:
    region: OrganizationRegionState
    changed: bool


class ConstructionOrganizationRegionService:
    def __init__(self, *, session, repository, authorizer, events, serialization, now_provider=None):
        collaborators = (
            (repository, ("get_active", "country_is_active", "update_region")),
            (authorizer, ("require",)), (events, ("create_event",)),
            (serialization, ("lock_member",)),
        )
        if (session is None or not hasattr(session, "no_autoflush")
                or any(not callable(getattr(session, name, None)) for name in (
                    "in_transaction", "get_transaction", "get_nested_transaction", "commit", "rollback"))
                or any(getattr(obj, "session", None) is not session
                       or any(not callable(getattr(obj, name, None)) for name in names)
                       for obj, names in collaborators)
                or now_provider is not None and not callable(now_provider)):
            raise ValueError("Region settings require shared-session collaborators.")
        self.session, self.repository = session, repository
        self.authorizer, self.events, self.serialization = authorizer, events, serialization
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _actor(actor):
        if (not isinstance(actor, ApiActorContext) or actor.status != "active"
                or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
            raise OrganizationRegionDenied("Organization settings are unavailable.")

    async def _authorize(self, actor):
        self._actor(actor)
        try:
            current = await self.authorizer.require(actor=actor)
        except OrganizationLanguageDenied:
            raise OrganizationRegionDenied("Organization settings are unavailable.") from None
        self._actor(current)
        if current.user_id != actor.user_id or current.tenant_id != actor.tenant_id:
            raise OrganizationRegionDenied("Organization settings are unavailable.")

    def _same_transaction(self, transaction):
        if (self.session.get_transaction() is not transaction
                or self.session.in_transaction() is not True
                or self.session.get_nested_transaction() is not None):
            raise OrganizationRegionOperationError("Region transaction changed.")

    @asynccontextmanager
    async def _operation(self, actor, *, write):
        self._actor(actor)
        transaction = self.session.get_transaction()
        if (transaction is None or self.session.in_transaction() is not True
                or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise OrganizationRegionOperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                if write:
                    await self.serialization.lock_member(tenant_id=actor.tenant_id, user_id=actor.user_id)
                await self._authorize(actor)
                self._same_transaction(transaction)
                yield transaction
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise OrganizationRegionOperationError("Region rollback failed.") from None
            if isinstance(error, (
                OrganizationRegionValidationError, OrganizationRegionDenied,
                OrganizationRegionConfigurationError, OrganizationRegionConflict,
                OrganizationRegionOperationError,
            )) or not isinstance(error, Exception):
                raise
            raise OrganizationRegionOperationError("Organization region operation failed.") from None

    @staticmethod
    def _state(row, tenant_id):
        if row is None:
            raise OrganizationRegionDenied("Organization settings are unavailable.")
        if (not isinstance(row, Mapping) or set(row) != {
                "tenant_id", "metadata", "updated_at", "row_version"}
                or not isinstance(row["tenant_id"], UUID) or row["tenant_id"] != tenant_id):
            raise OrganizationRegionConfigurationError("Canonical organization context is invalid.")
        try:
            version = _version(row["row_version"])
            instant = row["updated_at"]
            if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError()
            metadata = row["metadata"]
            if not isinstance(metadata, dict):
                raise ValueError()
            country, zone = None, None
            if REGION_KEY in metadata:
                region = metadata[REGION_KEY]
                if not isinstance(region, dict) or set(region) != {"country_code", "timezone"}:
                    raise ValueError()
                values = validate_region(country_code=region["country_code"], timezone_code=region["timezone"])
                country, zone = values["country_code"], values["timezone"]
            return OrganizationRegionState(tenant_id, country, zone, version, instant)
        except (ValueError, TypeError):
            raise OrganizationRegionConfigurationError("Stored region settings are invalid.") from None

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise OrganizationRegionOperationError("Region clock is invalid.")
        return value.astimezone(timezone.utc)

    async def read_for_actor(self, *, actor):
        async with self._operation(actor, write=False) as transaction:
            row = await self.repository.get_active(tenant_id=actor.tenant_id)
            state = self._state(row, actor.tenant_id)
            if state.configured and await self.repository.country_is_active(
                country_code=state.country_code
            ) is not True:
                raise OrganizationRegionConfigurationError("The configured Platform country is inactive.")
            await self._authorize(actor)
            self._same_transaction(transaction)
            return state

    async def update_for_actor(self, *, actor, country_code, timezone_code,
                               expected_row_version, trace_id=None, manage_transaction=True):
        values = validate_region(country_code=country_code, timezone_code=timezone_code)
        version = _version(expected_row_version)
        if (type(manage_transaction) is not bool or trace_id is not None and (
                type(trace_id) is not str or not trace_id.strip() or len(trace_id) > 255)):
            raise OrganizationRegionValidationError("Transaction or audit context is invalid.")
        async with self._operation(actor, write=True) as transaction:
            if await self.repository.country_is_active(
                country_code=values["country_code"], for_share=True
            ) is not True:
                raise OrganizationRegionValidationError("The Platform country is inactive or missing.")
            row = await self.repository.get_active(tenant_id=actor.tenant_id, for_update=True)
            await self._authorize(actor)
            self._same_transaction(transaction)
            before = self._state(row, actor.tenant_id)
            if before.row_version != version:
                raise OrganizationRegionConflict("Organization settings changed.")
            changes = [
                {"field": name, "old_value": getattr(before, name), "new_value": values[name]}
                for name in ("country_code", "timezone") if getattr(before, name) != values[name]
            ]
            if not changes:
                await self._authorize(actor)
                self._same_transaction(transaction)
                if manage_transaction:
                    await self.session.commit()
                return OrganizationRegionResult(before, False)
            if before.row_version == MAX_VERSION:
                raise OrganizationRegionOperationError("Organization version is exhausted.")
            saved = await self.repository.update_region(
                tenant_id=actor.tenant_id, expected_row_version=before.row_version,
                region=values, as_of=self._now())
            if saved is None:
                raise OrganizationRegionConflict("Organization settings changed.")
            after = self._state(saved, actor.tenant_id)
            if (after.row_version != before.row_version + 1
                    or after.country_code != values["country_code"] or after.timezone != values["timezone"]
                    or {k: v for k, v in saved["metadata"].items() if k != REGION_KEY}
                    != {k: v for k, v in row["metadata"].items() if k != REGION_KEY}):
                raise OrganizationRegionOperationError("Saved region settings differ.")
            await self.events.create_event(
                event_type="construction_organization_region_updated",
                tenant_id=actor.tenant_id, user_id=actor.user_id,
                entity_type="tenant", entity_id=actor.tenant_id,
                payload={"changes": changes, "previous_row_version": before.row_version,
                         "row_version": after.row_version},
                platform="api", trace_id=trace_id)
            await self._authorize(actor)
            self._same_transaction(transaction)
            if manage_transaction:
                await self.session.commit()
            return OrganizationRegionResult(after, True)


def build_construction_organization_region_service(*, platform_session, now_provider=None):
    from database.repositories.platform_organization_region import PlatformOrganizationRegionRepository
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    from database.repositories.event import EventRepository
    from services.api_identity import ApiIdentityService
    from services.construction.construction_grants import build_construction_access_context_resolver

    return ConstructionOrganizationRegionService(
        session=platform_session, repository=PlatformOrganizationRegionRepository(platform_session),
        authorizer=ConstructionOrganizationLanguageAuthorizer(
            session=platform_session, identity=ApiIdentityService(platform_session),
            access_resolver=build_construction_access_context_resolver(platform_session)),
        events=EventRepository(platform_session),
        serialization=PlatformMembershipSerializationRepository(platform_session),
        now_provider=now_provider)
