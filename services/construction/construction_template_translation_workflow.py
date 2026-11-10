"""Authorized template translation and exact tenant-scoped cache workflow.

Requires a clean active Construction transaction. The workflow ends it:
one commit for a cache write, rollback for a read-only result or failure.
The Platform session is used for fresh reads only and remains caller-owned.
Provider work happens before membership/cache locks. A bounded provider from
the existing Platform runtime is used; no new provider or identity model.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from services.construction.construction_content_translation import (
    ConstructionContentTranslator,
    PreparedContentTranslation,
    ContentTranslationKey,
    ContentTranslationRecord,
    ContentTranslationStatus as Status,
    ContentTranslationUnavailable,
)
from services.construction.construction_template_translation_source import (
    ConstructionTemplateTranslationSourceService,
    AuthorizedTemplateTranslationSource,
    TemplateTranslationSourceError,
    TemplateTranslationSourceValidationError,
    TemplateTranslationLanguageChanged,
    is_template_text_field,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError,
    ConstructionOnboardingStateConflictError,
    ConstructionOnboardingValidationError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError

KEY_FIELDS = (
    "entity_type", "entity_id", "field_name",
    "source_language", "target_language", "source_version", "source_text_hash",
)
ROW_FIELDS = set(KEY_FIELDS) | {
    "id", "tenant_id", "translated_text", "translation_status",
    "translated_at", "reviewed_by", "row_version", "created_at", "updated_at",
}


class TemplateTranslationWorkflowError(ConstructionOnboardingOperationError):
    pass


class TemplateTranslationContextChanged(ConstructionOnboardingStateConflictError):
    code = "SOURCE_CONTEXT_CHANGED"


class TemplateTranslationCacheConflict(ConstructionOnboardingStateConflictError):
    code = "TRANSLATION_CACHE_CONFLICT"


@dataclass(frozen=True)
class TemplateTranslatedText:
    tenant_id: UUID
    user_id: UUID
    template_id: UUID
    version_id: UUID
    field_name: str
    target_language: str
    source_language: str | None
    source_version: int | None
    original_text: str | None
    display_text: str | None
    translation_status: Status | None
    origin: str
    cache_row_version: int | None
    cache_written: bool


class ConstructionTemplateTranslationWorkflow:
    def __init__(
        self, *, session, source_service, cache_repository, translator,
        suite_access, module_access,
    ):
        if (
            not isinstance(source_service, ConstructionTemplateTranslationSourceService)
            or source_service.session is not session
            or getattr(cache_repository, "session", None) is not session
            or any(not callable(getattr(cache_repository, name, None)) for name in (
                "get_exact", "insert_automatic", "replace_stale_automatic"))
            or not isinstance(translator, ConstructionContentTranslator)
            or any(
                getattr(getattr(gate, "repository", None), "session", None)
                is not source_service.platform_session
                or not callable(getattr(gate, "require_enabled", None))
                for gate in (suite_access, module_access)
            )
        ):
            raise ValueError("Translation workflow requires shared-session server collaborators.")
        self.session = session
        self.sources = source_service
        self.cache = cache_repository
        self.translator = translator
        self.suite = suite_access
        self.module = module_access
        self.authorizer = source_service.templates.authorizer

    def _same(self, transaction):
        self.authorizer._same_transaction(transaction)

    async def _gates(self, actor, transaction):
        platform = self.sources.platform_session
        if any(bool(getattr(platform, name, ())) for name in ("new", "dirty", "deleted")):
            raise TemplateTranslationWorkflowError("Platform read session is not clean.")
        platform.expire_all()
        suite_result = await self.suite.require_enabled(
            tenant_id=actor.tenant_id, suite_code="construction")
        self._same(transaction)
        module_result = await self.module.require_enabled(
            tenant_id=actor.tenant_id, module_code="construction.core")
        self._same(transaction)
        if suite_result is not None or module_result is not None:
            raise TemplateTranslationWorkflowError("Module gate response is invalid.")

    async def _read(self, actor, version_id, field_name, transaction):
        await self._gates(actor, transaction)
        context = await self.sources.read_for_actor(
            actor=actor, version_id=version_id, field_name=field_name)
        self._same(transaction)
        if (
            not isinstance(context, AuthorizedTemplateTranslationSource)
            or context.tenant_id != actor.tenant_id or context.user_id != actor.user_id
            or context.version_id != version_id or context.field_name != field_name
            or context.source is None and field_name != "description"
            or context.source is not None and (
                context.source.entity_id != version_id
                or context.source.field_name != field_name)
        ):
            raise TemplateTranslationWorkflowError("Translation source context differs.")
        return context

    async def _recheck(self, expected, actor, transaction):
        current = await self._read(
            actor, expected.version_id, expected.field_name, transaction)
        if current.target_language != expected.target_language:
            raise TemplateTranslationLanguageChanged(
                "Profile language changed during translation.")
        if current != expected:
            raise TemplateTranslationContextChanged(
                "Canonical text or source version changed during translation.")
        return current

    @staticmethod
    def _cached(row, context):
        if row is None:
            return None
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
            if key != context.source.key_for(context.target_language):
                raise ValueError()
            record = ContentTranslationRecord(
                key=key, translated_text=row["translated_text"],
                translation_status=Status(row["translation_status"]),
                translated_at=row["translated_at"], reviewed_by=row["reviewed_by"])
            return record, row["row_version"]
        except (ValueError, TypeError, KeyError, AttributeError):
            raise TemplateTranslationWorkflowError("Stored translation context is invalid.") from None

    @staticmethod
    def _choice(context, cached):
        source = context.source
        if source is None:
            return None, None, "ORIGINAL", None
        if source.source_language == context.target_language or not source.original_text.strip():
            return source.original_text, None, "ORIGINAL", None
        if cached is not None and cached[0].translation_status is Status.VERIFIED:
            return cached[0].translated_text, Status.VERIFIED, "CACHE", cached[1]
        if context.supplied_text is not None:
            # This is content already supplied in the exact published version.
            # Do not invent VERIFIED metadata or a reviewer for it.
            return context.supplied_text, None, "PUBLISHED_LABEL", None
        if cached is not None and cached[0].translation_status is Status.AUTO_TRANSLATED:
            return cached[0].translated_text, Status.AUTO_TRANSLATED, "CACHE", cached[1]
        return None

    async def _row(self, context, transaction, *, locked=False):
        row = await self.cache.get_exact(
            tenant_id=context.tenant_id,
            key=context.source.key_for(context.target_language),
            for_update=locked)
        self._same(transaction)
        return row, self._cached(row, context)

    @staticmethod
    def _result(context, choice, wrote):
        text, status, origin, version = choice
        source = context.source
        return TemplateTranslatedText(
            tenant_id=context.tenant_id, user_id=context.user_id,
            template_id=context.template_id, version_id=context.version_id,
            field_name=context.field_name, target_language=context.target_language,
            source_language=None if source is None else source.source_language,
            source_version=None if source is None else source.source_version,
            original_text=None if source is None else source.original_text,
            display_text=text, translation_status=status, origin=origin,
            cache_row_version=version, cache_written=wrote)

    async def read_for_actor(self, *, actor, version_id, field_name):
        self.sources._actor(actor)
        if not isinstance(version_id, UUID) or not is_template_text_field(field_name):
            raise TemplateTranslationSourceValidationError("Exact version and text field are required.")
        transaction = self.session.get_transaction()
        if (
            transaction is None or self.session.in_transaction() is not True
            or self.session.get_nested_transaction() is not None
            or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))
        ):
            raise TemplateTranslationWorkflowError("A clean active Construction transaction is required.")
        try:
            with self.session.no_autoflush:
                context = await self._read(actor, version_id, field_name, transaction)
                row, cached = None, None
                if context.source is not None and (
                    context.source.source_language != context.target_language
                ):
                    row, cached = await self._row(context, transaction)
                choice = self._choice(context, cached)
                wrote = False

                if choice is None:
                    prepared = await self.translator.prepare(
                        source=context.source, target_language=context.target_language,
                        records=() if cached is None else (cached[0],))
                    self._same(transaction)
                    if (
                        not isinstance(prepared, PreparedContentTranslation)
                        or prepared.source != context.source
                        or prepared.target_language != context.target_language
                        or not isinstance(prepared.pending, ContentTranslationRecord)
                        or prepared.pending != prepared.record
                        or prepared.pending.key != context.source.key_for(context.target_language)
                        or prepared.pending.translation_status is not Status.AUTO_TRANSLATED
                        or prepared.pending.reviewed_by is not None
                    ):
                        raise TemplateTranslationWorkflowError("Prepared automatic translation differs.")

                    await self._recheck(context, actor, transaction)
                    # Existing PostgreSQL serialization enforces lock order and
                    # READ COMMITTED. No SQLite/no-lock runtime fallback.
                    await self.authorizer.serialization.lock_member(
                        tenant_id=actor.tenant_id, user_id=actor.user_id)
                    self._same(transaction)
                    await self._recheck(context, actor, transaction)
                    row, cached = await self._row(context, transaction, locked=True)
                    choice = self._choice(context, cached)

                    if choice is None:
                        instant = self.authorizer._now()
                        if row is None:
                            saved = await self.cache.insert_automatic(
                                tenant_id=actor.tenant_id,
                                record=prepared.pending, as_of=instant)
                            expected_version = 1
                        else:
                            saved = await self.cache.replace_stale_automatic(
                                tenant_id=actor.tenant_id, record=prepared.pending,
                                expected_row_version=cached[1], as_of=instant)
                            expected_version = cached[1] + 1
                        self._same(transaction)
                        observed, observed_cache = await self._row(
                            context, transaction, locked=True)
                        if saved is not None:
                            if (
                                observed != saved or observed_cache is None
                                or observed_cache != (prepared.pending, expected_version)
                                or row is not None and (
                                    saved["id"] != row["id"]
                                    or saved["created_at"] != row["created_at"])
                            ):
                                raise TemplateTranslationWorkflowError("Saved automatic translation differs.")
                            wrote = True
                        choice = self._choice(context, observed_cache)
                        if choice is None:
                            raise TemplateTranslationCacheConflict(
                                "Translation cache changed; retry with fresh context.")
                        if wrote:
                            choice = choice[:2] + ("GENERATED", choice[3])

                await self._recheck(context, actor, transaction)
                result = self._result(context, choice, wrote)
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
                raise TemplateTranslationWorkflowError("Translation rollback failed.") from None
            if not isinstance(error, Exception):
                raise
            if isinstance(error, (
                TemplateTranslationWorkflowError, TemplateTranslationContextChanged,
                TemplateTranslationCacheConflict, TemplateTranslationSourceError,
                TemplateTranslationSourceValidationError, ContentTranslationUnavailable,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionOnboardingOperationError, ConstructionOnboardingStateConflictError,
                ConstructionOnboardingValidationError, ModuleDisabledError, SuiteDisabledError,
            )):
                raise
            raise TemplateTranslationWorkflowError("Template translation workflow failed.") from None


def build_construction_template_translation_workflow(
    *, session, platform_session, provider=None, now_provider=None,
):
    from database.repositories.construction.construction_content_translations import (
        ConstructionContentTranslationRepository,
    )
    from services.construction.construction_template_translation_source import (
        build_construction_template_translation_source_service,
    )
    from services.module_access import (
        build_tenant_suite_access_service, build_tenant_module_access_service,
    )
    from services.translation import LibreTranslateProvider
    return ConstructionTemplateTranslationWorkflow(
        session=session,
        source_service=build_construction_template_translation_source_service(
            session=session, platform_session=platform_session,
            now_provider=now_provider),
        cache_repository=ConstructionContentTranslationRepository(session),
        translator=ConstructionContentTranslator(
            provider=provider if provider is not None else LibreTranslateProvider(),
            now_provider=now_provider),
        suite_access=build_tenant_suite_access_service(platform_session),
        module_access=build_tenant_module_access_service(platform_session))
