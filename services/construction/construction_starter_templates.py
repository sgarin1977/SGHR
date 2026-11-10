"""Authorized starter reads; no publication, copying, translation or computation."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from uuid import UUID

from services.construction.construction_onboarding import (
    ConstructionOnboardingService,
    ConstructionOnboardingValidationError,
    ConstructionOnboardingOperationError,
    ConstructionOnboardingStateConflictError,
)
from services.construction.construction_starter_template_definitions import (
    ValidatedStarterDefinition, validate_starter_definition,
)


class ConstructionStarterTemplateValidationError(ConstructionOnboardingValidationError):
    pass


class ConstructionStarterTemplateOperationError(ConstructionOnboardingOperationError):
    pass


class ConstructionStarterTemplateNotFoundError(ConstructionOnboardingStateConflictError):
    pass


@dataclass(frozen=True)
class ConstructionStarterTemplateVersionState:
    version_id: UUID
    template_id: UUID
    code: str
    version: int
    definition_schema_version: int
    source_language: str
    definition: ValidatedStarterDefinition
    content_hash: str
    published_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class ConstructionStarterTemplateCursor:
    tenant_id: UUID
    user_id: UUID
    after_template_id: UUID

    def __post_init__(self):
        if not all(isinstance(value, UUID) for value in (
            self.tenant_id, self.user_id, self.after_template_id,
        )):
            raise ConstructionStarterTemplateValidationError("Catalog cursor requires UUIDs.")


@dataclass(frozen=True)
class ConstructionStarterTemplatePage:
    items: tuple[ConstructionStarterTemplateVersionState, ...]
    next_cursor: ConstructionStarterTemplateCursor | None


class ConstructionStarterTemplateService:
    def __init__(self, *, session, repository, authorizer):
        if (
            not isinstance(authorizer, ConstructionOnboardingService)
            or authorizer.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None))
                   for name in ("get_version", "list_latest"))
        ):
            raise ValueError("Starter reads require shared-session settings authority.")
        self.session, self.repository, self.authorizer = session, repository, authorizer

    @staticmethod
    def _state(row, *, as_of, requested_id=None):
        if row is None:
            raise ConstructionStarterTemplateNotFoundError("Starter version is unavailable.")
        try:
            if not isinstance(row, Mapping) or set(row) != {
                "version_id", "template_id", "code", "active", "version",
                "definition_schema_version", "source_language", "definition",
                "content_hash", "published_at", "created_at",
            }:
                raise ValueError()
            if (
                not isinstance(row["version_id"], UUID)
                or not isinstance(row["template_id"], UUID)
                or requested_id is not None and row["version_id"] != requested_id
                or row["active"] is not True
                or not isinstance(row["code"], str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", row["code"]) is None
                or type(row["version"]) is not int
                or not 1 <= row["version"] <= 2**31 - 1
                or type(row["definition_schema_version"]) is not int
                or row["definition_schema_version"] != 1
                or any(
                    not isinstance(row[name], datetime)
                    or row[name].tzinfo is None or row[name].utcoffset() is None
                    for name in ("published_at", "created_at")
                )
                or row["published_at"] > as_of
            ):
                raise ValueError()
            definition = validate_starter_definition(
                row["definition"], source_language=row["source_language"])
            if row["content_hash"] != definition.content_hash:
                raise ValueError()
            return ConstructionStarterTemplateVersionState(
                row["version_id"], row["template_id"], row["code"], row["version"],
                row["definition_schema_version"], row["source_language"],
                definition, row["content_hash"],
                row["published_at"].astimezone(timezone.utc),
                row["created_at"].astimezone(timezone.utc))
        except (ValueError, TypeError):
            raise ConstructionStarterTemplateOperationError(
                "Stored starter version is invalid.") from None

    async def get_version(self, *, actor, version_id):
        if not isinstance(version_id, UUID):
            raise ConstructionStarterTemplateValidationError("A version UUID is required.")
        async with self.authorizer._operation(actor, write=False) as transaction:
            as_of = self.authorizer._now()
            row = await self.repository.get_version(version_id=version_id, as_of=as_of)
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            return self._state(row, as_of=as_of, requested_id=version_id)

    async def list_latest(self, *, actor, limit=20, after=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConstructionStarterTemplateValidationError("Catalog limit must be from 1 to 100.")
        if after is not None and (
            not isinstance(after, ConstructionStarterTemplateCursor)
            or (after.tenant_id, after.user_id) != (actor.tenant_id, actor.user_id)
        ):
            raise ConstructionStarterTemplateValidationError("Catalog cursor context differs.")
        async with self.authorizer._operation(actor, write=False) as transaction:
            as_of = self.authorizer._now()
            rows = await self.repository.list_latest(
                limit=limit + 1, as_of=as_of,
                after_template_id=None if after is None else after.after_template_id)
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            if not isinstance(rows, (list, tuple)) or len(rows) > limit + 1:
                raise ConstructionStarterTemplateOperationError("Catalog page is invalid.")
            states, codes = [], set()
            previous = None if after is None else after.after_template_id
            for row in rows:
                state = self._state(row, as_of=as_of)
                if (
                    previous is not None and state.template_id <= previous
                    or state.code in codes
                ):
                    raise ConstructionStarterTemplateOperationError("Catalog page identity/order differs.")
                states.append(state)
                codes.add(state.code)
                previous = state.template_id
            items = tuple(states[:limit])
            cursor = None
            if len(states) > limit:
                cursor = ConstructionStarterTemplateCursor(
                    actor.tenant_id, actor.user_id, items[-1].template_id)
            return ConstructionStarterTemplatePage(items, cursor)


def build_construction_starter_template_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_onboarding import ConstructionOnboardingRepository
    from database.repositories.construction.construction_starter_templates import ConstructionStarterTemplateRepository
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership_serialization import PlatformMembershipSerializationRepository
    authorizer = ConstructionOnboardingService(
        session=session, repository=ConstructionOnboardingRepository(session),
        access_repository=ConstructionAccessRepository(session),
        event_repository=EventRepository(session),
        serialization=PlatformMembershipSerializationRepository(session),
        now_provider=now_provider)
    return ConstructionStarterTemplateService(
        session=session, repository=ConstructionStarterTemplateRepository(session),
        authorizer=authorizer)
