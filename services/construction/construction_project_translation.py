"""Authorized project text translation using the existing versioned cache.

Only name/comment are translatable here. Original project fields are never written.
The workflow owns its clean outer Construction transaction: cache write commits;
read-only results and failures roll back. Platform reads remain caller-owned.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_content_translation import (
    ConstructionContentTranslator,
    ContentFieldKind,
    ContentTranslationKey,
    ContentTranslationRecord,
    ContentTranslationSource,
    ContentTranslationStatus as Status,
    ContentTranslationUnavailable,
    PreparedContentTranslation,
    _language,
    _text,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
    require_construction_access,
)
from services.construction.construction_project_read import ConstructionProjectReadService
from services.construction.construction_projects import (
    ConstructionProjectNotFoundError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError

KEY_FIELDS = (
    "entity_type", "entity_id", "field_name", "source_language",
    "target_language", "source_version", "source_text_hash",
)
ROW_FIELDS = set(KEY_FIELDS) | {
    "id", "tenant_id", "translated_text", "translation_status", "translated_at",
    "reviewed_by", "row_version", "created_at", "updated_at",
}


class ProjectTranslationOperationError(Exception):
    pass


class ProjectTranslationValidationError(ValueError):
    pass


class ProjectTranslationContextChanged(Exception):
    code = "SOURCE_CONTEXT_CHANGED"


class ProjectTranslationLanguageError(ProjectTranslationOperationError):
    code = "LANGUAGE_CONFIGURATION_REQUIRED"


@dataclass(frozen=True)
class ProjectTextContext:
    tenant_id: UUID
    user_id: UUID
    project_id: UUID
    field_name: str
    target_language: str
    source_version: int
    original_text: str | None

    @property
    def text_hash(self):
        if self.original_text is None:
            return None
        return sha256(self.original_text.encode("utf-8")).hexdigest()

    def source(self, source_language):
        return ContentTranslationSource(
            entity_type="construction.project", entity_id=self.project_id,
            field_name=self.field_name, source_language=source_language,
            source_version=self.source_version, original_text=self.original_text,
            field_kind=ContentFieldKind.TEXT)


@dataclass(frozen=True)
class ProjectTranslatedText:
    tenant_id: UUID
    user_id: UUID
    project_id: UUID
    field_name: str
    target_language: str
    source_language: str | None
    source_version: int
    original_text: str | None
    display_text: str | None
    translation_status: Status | None
    translated_at: datetime | None
    reviewed_by: UUID | None
    cache_row_version: int | None
    cache_written: bool


class ConstructionProjectTranslationWorkflow:
    def __init__(
        self, *, session, platform_session, project_service, access_repository,
        identity, language_repository, serialization, cache_repository,
        translator, suite_access, module_access, now_provider=None,
    ):
        collaborators = (
            (access_repository, session, (
                "has_active_platform_membership", "list_active_grants")),
            (identity, platform_session, ("require_actor",)),
            (language_repository, platform_session, ("is_active_language",)),
            (serialization, session, ("lock_member",)),
            (cache_repository, session, (
                "find_project_text", "insert_automatic", "replace_stale_automatic")),
        )
        if (
            not isinstance(project_service, ConstructionProjectReadService)
            or getattr(project_service.repository, "session", None) is not session
            or not callable(getattr(
                project_service.repository, "get_active_project_for_update", None))
            or not isinstance(translator, ConstructionContentTranslator)
            or any(
                getattr(obj, "session", None) is not expected
                or any(not callable(getattr(obj, method, None)) for method in methods)
                for obj, expected, methods in collaborators)
            or any(
                getattr(getattr(gate, "repository", None), "session", None)
                is not platform_session
                or not callable(getattr(gate, "require_enabled", None))
                for gate in (suite_access, module_access))
            or any(
                not hasattr(item, "no_autoflush")
                or not callable(getattr(item, "expire_all", None))
                for item in (session, platform_session))
            or now_provider is not None and not callable(now_provider)
        ):
            raise ValueError("Shared-session project translation collaborators are required.")
        self.session, self.platform_session = session, platform_session
        self.projects, self.access = project_service, access_repository
        self.identity, self.languages = identity, language_repository
        self.serialization, self.cache = serialization, cache_repository
        self.translator = translator
        self.suite, self.module = suite_access, module_access
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(
            repository=access_repository, now_provider=self._now)

    def _now(self):
        value = self.now_provider()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None or value.utcoffset() is None
        ):
            raise ProjectTranslationOperationError("Translation clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor):
        if (
            not isinstance(actor, ApiActorContext) or actor.status != "active"
            or not isinstance(actor.user_id, UUID)
            or not isinstance(actor.tenant_id, UUID)
        ):
            raise ConstructionPermissionDeniedError("Project text identity is unavailable.")

    def _same(self, transaction):
        if (
            self.session.get_transaction() is not transaction
            or self.session.in_transaction() is not True
            or self.session.get_nested_transaction() is not None
            or any(bool(getattr(self.session, key, ()))
                   for key in ("new", "dirty", "deleted"))
        ):
            raise ProjectTranslationOperationError(
                "A clean unchanged outer transaction is required.")

    async def _authority(self, actor, project_id, transaction):
        self._same(transaction)
        platform = self.platform_session
        if any(bool(getattr(platform, key, ())) for key in ("new", "dirty", "deleted")):
            raise ProjectTranslationOperationError("Platform read session is not clean.")
        platform.expire_all()
        try:
            current = await self.identity.require_actor(
                user_id=actor.user_id, tenant_id=actor.tenant_id)
        except ApiIdentityAccessError:
            raise ConstructionPermissionDeniedError(
                "Project text identity is unavailable.") from None
        self._same(transaction)
        self._actor(current)
        if (current.user_id, current.tenant_id) != (actor.user_id, actor.tenant_id):
            raise ConstructionPermissionDeniedError("Project text identity context differs.")
        try:
            _language(current.language_code)
        except ValueError:
            raise ProjectTranslationLanguageError(
                "An active profile language is required.") from None
        if await self.languages.is_active_language(current.language_code) is not True:
            raise ProjectTranslationLanguageError("An active profile language is required.")
        self._same(transaction)
        suite = await self.suite.require_enabled(
            tenant_id=actor.tenant_id, suite_code="construction")
        self._same(transaction)
        module = await self.module.require_enabled(
            tenant_id=actor.tenant_id, module_code="construction.core")
        self._same(transaction)
        if suite is not None or module is not None:
            raise ProjectTranslationOperationError("Module gate response is invalid.")
        self.session.expire_all()
        if await self.access.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id
        ) is not True:
            raise ConstructionPermissionDeniedError("Project membership is unavailable.")
        self._same(transaction)
        access = await self.resolver.resolve(actor=current)
        self._same(transaction)
        require_construction_access(
            actor=current, access_context=access,
            permission_code="construction.projects.read", project_id=project_id)
        return current, access

    async def _read(self, actor, project_id, field_name, transaction):
        current, access = await self._authority(actor, project_id, transaction)
        project = await self.projects.get_project(
            actor=current, access_context=access, project_id=project_id)
        self._same(transaction)
        version = getattr(project, "row_version", None)
        text = getattr(project, field_name, object())
        if type(version) is not int or not 1 <= version <= 2**63 - 1:
            raise ProjectTranslationOperationError("Canonical project version is invalid.")
        if text is not None:
            _text(text)
        elif field_name != "comment":
            raise ProjectTranslationOperationError("Canonical project name is invalid.")
        result = ProjectTextContext(
            actor.tenant_id, actor.user_id, project_id, field_name,
            current.language_code, version, text)
        after, unused = await self._authority(actor, project_id, transaction)
        if after.language_code != result.target_language:
            raise ProjectTranslationContextChanged("Profile language changed.")
        return result

    async def _recheck(self, actor, expected, transaction):
        current = await self._read(
            actor, expected.project_id, expected.field_name, transaction)
        if current != expected:
            raise ProjectTranslationContextChanged("Canonical project text changed.")

    async def _lookup_cache_rows(self, context, transaction, *, locked=False):
        return await self.cache.find_project_text(
            tenant_id=context.tenant_id, project_id=context.project_id,
            field_name=context.field_name, target_language=context.target_language,
            source_version=context.source_version, source_text_hash=context.text_hash,
            for_update=locked)

    async def _row(self, context, transaction, *, locked=False):
        rows = await self._lookup_cache_rows(context, transaction, locked=locked)
        self._same(transaction)
        if not isinstance(rows, tuple) or len(rows) > 1:
            raise ProjectTranslationOperationError("Current source language is ambiguous.")
        if not rows:
            return None, None
        row = rows[0]
        try:
            if (
                not isinstance(row, Mapping) or set(row) != ROW_FIELDS
                or row["tenant_id"] != context.tenant_id
                or not isinstance(row["id"], UUID)
                or type(row["row_version"]) is not int
                or not 1 <= row["row_version"] <= 2**63 - 1
                or any(
                    not isinstance(row[name], datetime)
                    or row[name].tzinfo is None or row[name].utcoffset() is None
                    for name in ("created_at", "updated_at"))
            ):
                raise ValueError()
            key = ContentTranslationKey(**{name: row[name] for name in KEY_FIELDS})
            if (
                key != context.source(key.source_language).key_for(context.target_language)
                or key.source_language == key.target_language
            ):
                raise ValueError()
            record = ContentTranslationRecord(
                key=key, translated_text=row["translated_text"],
                translation_status=Status(row["translation_status"]),
                translated_at=row["translated_at"], reviewed_by=row["reviewed_by"])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProjectTranslationOperationError("Stored project translation is invalid.") from None
        if await self.languages.is_active_language(key.source_language) is not True:
            raise ProjectTranslationLanguageError("Stored source language is unavailable.")
        self._same(transaction)
        return row, record

    @staticmethod
    def _usable(record):
        return record is not None and record.translation_status in (
            Status.VERIFIED, Status.AUTO_TRANSLATED)

    async def _detect(self, text):
        provider = self.translator.provider
        if not callable(getattr(provider, "detect_language", None)):
            raise ContentTranslationUnavailable("Source language detection is unavailable.")
        try:
            language = await provider.detect_language(text=text)
            _language(language)
            if await self.languages.is_active_language(language) is not True:
                raise ValueError()
            return language
        except Exception:
            raise ContentTranslationUnavailable(
                "Source language detection is unavailable.") from None

    async def read_for_actor(self, *, actor, project_id, field_name):
        self._actor(actor)
        if not isinstance(project_id, UUID) or field_name not in ("name", "comment"):
            raise ProjectTranslationValidationError("An exact project text field is required.")
        transaction = self.session.get_transaction()
        if transaction is None:
            raise ProjectTranslationOperationError("An active outer transaction is required.")
        try:
            self._same(transaction)
            with self.session.no_autoflush:
                context = await self._read(actor, project_id, field_name, transaction)
                row, record, source_language, wrote = None, None, None, False
                text = context.original_text
                if text is not None and text.strip():
                    row, record = await self._row(context, transaction)
                    if record is None:
                        source_language = await self._detect(text)
                        self._same(transaction)
                        await self._recheck(actor, context, transaction)
                        # A concurrent request may already have persisted its source.
                        row, record = await self._row(context, transaction)
                    if record is not None:
                        source_language = record.key.source_language
                    source = context.source(source_language)
                    prepared = await self.translator.prepare(
                        source=source, target_language=context.target_language,
                        records=() if record is None else (record,))
                    self._same(transaction)
                    if (
                        not isinstance(prepared, PreparedContentTranslation)
                        or prepared.source != source
                        or prepared.target_language != context.target_language
                    ):
                        raise ProjectTranslationOperationError("Prepared project text differs.")
                    if prepared.pending is not None:
                        pending = prepared.pending
                        if (
                            not isinstance(pending, ContentTranslationRecord)
                            or pending != prepared.record
                            or pending.key != source.key_for(context.target_language)
                            or pending.translation_status is not Status.AUTO_TRANSLATED
                            or pending.reviewed_by is not None
                        ):
                            raise ProjectTranslationOperationError(
                                "Prepared automatic translation differs.")
                        await self._recheck(actor, context, transaction)
                        await self.serialization.lock_member(
                            tenant_id=actor.tenant_id, user_id=actor.user_id)
                        self._same(transaction)
                        await self._recheck(actor, context, transaction)
                        # Serialize cache misses across actors, including different
                        # detected languages. This locks but never changes the project.
                        locked = await self.projects.repository.get_active_project_for_update(
                            tenant_id=actor.tenant_id, project_id=project_id)
                        self._same(transaction)
                        if (
                            locked is None or locked.id != project_id
                            or locked.tenant_id != actor.tenant_id
                            or locked.deleted_at is not None
                        ):
                            raise ConstructionProjectNotFoundError(
                                "Construction project is not available.")
                        await self._recheck(actor, context, transaction)
                        row, record = await self._row(context, transaction, locked=True)
                        if not self._usable(record):
                            if record is not None and record.key != pending.key:
                                raise ProjectTranslationContextChanged(
                                    "Stored source language changed.")
                            if row is None:
                                saved = await self.cache.insert_automatic(
                                    tenant_id=actor.tenant_id, record=pending,
                                    as_of=self._now())
                                expected_version = 1
                            else:
                                saved = await self.cache.replace_stale_automatic(
                                    tenant_id=actor.tenant_id, record=pending,
                                    expected_row_version=row["row_version"],
                                    as_of=self._now())
                                expected_version = row["row_version"] + 1
                            self._same(transaction)
                            observed, record = await self._row(
                                context, transaction, locked=True)
                            if saved is not None:
                                if (
                                    observed != saved or record != pending
                                    or observed["row_version"] != expected_version
                                    or row is not None and (
                                        observed["id"] != row["id"]
                                        or observed["created_at"] != row["created_at"])
                                ):
                                    raise ProjectTranslationOperationError(
                                        "Saved project translation differs.")
                                wrote = True
                            row = observed
                            if not self._usable(record):
                                raise ProjectTranslationContextChanged(
                                    "Translation cache changed; retry.")
                        source_language = record.key.source_language
                    else:
                        record = prepared.record
                await self._recheck(actor, context, transaction)
                result = ProjectTranslatedText(
                    tenant_id=context.tenant_id, user_id=context.user_id,
                    project_id=project_id, field_name=field_name,
                    target_language=context.target_language,
                    source_language=source_language,
                    source_version=context.source_version,
                    original_text=context.original_text,
                    display_text=context.original_text if record is None else record.translated_text,
                    translation_status=None if record is None else record.translation_status,
                    translated_at=None if record is None else record.translated_at,
                    reviewed_by=None if record is None else record.reviewed_by,
                    cache_row_version=None if record is None else row["row_version"],
                    cache_written=wrote)
                if wrote:
                    await self.session.commit()
                else:
                    await self.session.rollback()
                return result
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ProjectTranslationOperationError("Translation rollback failed.") from None
            if not isinstance(error, Exception) or isinstance(error, (
                ProjectTranslationOperationError, ProjectTranslationValidationError,
                ProjectTranslationContextChanged, ContentTranslationUnavailable,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionProjectNotFoundError, ModuleDisabledError, SuiteDisabledError,
            )):
                raise
            raise ProjectTranslationOperationError("Project translation failed.") from None


def build_construction_project_translation_workflow(
    *, session, platform_session, provider=None, now_provider=None,
):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_project_translation import (
        ConstructionProjectTranslationRepository,
    )
    from database.repositories.platform_catalog_translations import (
        PlatformCatalogTranslationRepository,
    )
    from database.repositories.platform_membership_serialization import (
        PlatformMembershipSerializationRepository,
    )
    from services.api_identity import ApiIdentityService
    from services.construction.construction_project_read import (
        build_construction_project_read_service,
    )
    from services.module_access import (
        build_tenant_suite_access_service, build_tenant_module_access_service,
    )
    from services.translation import LibreTranslateProvider
    return ConstructionProjectTranslationWorkflow(
        session=session, platform_session=platform_session,
        project_service=build_construction_project_read_service(session),
        access_repository=ConstructionAccessRepository(session),
        identity=ApiIdentityService(platform_session),
        language_repository=PlatformCatalogTranslationRepository(platform_session),
        serialization=PlatformMembershipSerializationRepository(session),
        cache_repository=ConstructionProjectTranslationRepository(session),
        translator=ConstructionContentTranslator(
            provider=provider if provider is not None else LibreTranslateProvider(),
            now_provider=now_provider),
        suite_access=build_tenant_suite_access_service(platform_session),
        module_access=build_tenant_module_access_service(platform_session),
        now_provider=now_provider)

# CON_020B_AREA_LOCALIZED_READ_V1
