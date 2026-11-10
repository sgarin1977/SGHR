"""Bounded template text batches using the existing source/cache contract.

The caller supplies one exact published version and canonical field identities.
Target language, source text and access come from the existing trusted readers.
Provider work finishes before membership/cache locks. One final commit covers
all cache writes; a read-only result ends with rollback.
"""
from uuid import UUID

from services.construction.construction_content_translation import (
    PreparedContentTranslation,
    ContentTranslationRecord,
    ContentTranslationStatus as Status,
    ContentTranslationUnavailable,
)
from services.construction.construction_template_translation_source import (
    TemplateTranslationSourceError,
    TemplateTranslationSourceValidationError,
    TemplateTranslationLanguageChanged,
    is_template_text_field,
    template_label_field,
)
from services.construction.construction_template_translation_workflow import (
    ConstructionTemplateTranslationWorkflow,
    TemplateTranslationWorkflowError,
    TemplateTranslationContextChanged,
    TemplateTranslationCacheConflict,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError,
    ConstructionOnboardingStateConflictError,
    ConstructionOnboardingValidationError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError

MAX_BATCH_FIELDS = 32

KNOWN_ERRORS = (
    TemplateTranslationSourceError,
    TemplateTranslationSourceValidationError,
    ContentTranslationUnavailable,
    ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
    ConstructionOnboardingOperationError,
    ConstructionOnboardingStateConflictError,
    ConstructionOnboardingValidationError,
    ModuleDisabledError,
    SuiteDisabledError,
)


class ConstructionTemplateTranslationBatchWorkflow:
    def __init__(self, *, workflow):
        if not isinstance(workflow, ConstructionTemplateTranslationWorkflow):
            raise ValueError("The existing canonical translation workflow is required.")
        self.workflow = workflow
        self.session = workflow.session

    @staticmethod
    def _fields(values):
        if (
            not isinstance(values, tuple)
            or not 1 <= len(values) <= MAX_BATCH_FIELDS
            or any(not is_template_text_field(value) for value in values)
            or len(set(values)) != len(values)
        ):
            raise TemplateTranslationSourceValidationError(
                "A bounded tuple of distinct canonical text fields is required.")
        return values

    @staticmethod
    def _coherent(contexts):
        first = next(iter(contexts.values()))
        for context in contexts.values():
            if context.target_language != first.target_language:
                raise TemplateTranslationLanguageChanged(
                    "Profile language changed while reading the batch.")
            if (
                context.tenant_id != first.tenant_id
                or context.user_id != first.user_id
                or context.template_id != first.template_id
                or context.version_id != first.version_id
                or context.content_hash != first.content_hash
            ):
                raise TemplateTranslationContextChanged(
                    "The batch contains different canonical contexts.")
        source_versions = {
            (
                context.source.entity_type,
                context.source.source_language,
                context.source.source_version,
            )
            for context in contexts.values()
            if context.source is not None
        }
        if len(source_versions) > 1:
            raise TemplateTranslationContextChanged(
                "The batch contains different source versions.")

    async def _recheck_all(self, contexts, actor, transaction):
        for field in sorted(contexts):
            await self.workflow._recheck(
                contexts[field], actor, transaction)

    async def _prepare(self, context, cached, transaction):
        w = self.workflow
        prepared = await w.translator.prepare(
            source=context.source,
            target_language=context.target_language,
            records=() if cached is None else (cached[0],))
        w._same(transaction)
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
            raise TemplateTranslationWorkflowError(
                "Prepared batch translation differs from its canonical source.")
        return prepared.pending

    async def _persist(self, context, record, transaction):
        w = self.workflow
        row, cached = await w._row(context, transaction, locked=True)
        choice = w._choice(context, cached)
        if choice is not None:
            return choice, False

        instant = w.authorizer._now()
        if row is None:
            saved = await w.cache.insert_automatic(
                tenant_id=context.tenant_id,
                record=record, as_of=instant)
            expected_version = 1
        else:
            saved = await w.cache.replace_stale_automatic(
                tenant_id=context.tenant_id,
                record=record,
                expected_row_version=cached[1],
                as_of=instant)
            expected_version = cached[1] + 1
        w._same(transaction)

        observed, observed_cache = await w._row(
            context, transaction, locked=True)
        wrote = saved is not None
        if wrote and (
            observed != saved
            or observed_cache != (record, expected_version)
            or row is not None and (
                saved["id"] != row["id"]
                or saved["created_at"] != row["created_at"])
        ):
            raise TemplateTranslationWorkflowError(
                "Persisted batch translation differs.")

        choice = w._choice(context, observed_cache)
        if choice is None:
            raise TemplateTranslationCacheConflict(
                "Translation cache changed; retry with fresh context.")
        if wrote:
            choice = choice[:2] + ("GENERATED", choice[3])
        return choice, wrote

    async def read_labels_for_actor(self, *, actor, version_id, label_keys):
        if (
            not isinstance(label_keys, tuple)
            or not 1 <= len(label_keys) <= MAX_BATCH_FIELDS
        ):
            raise TemplateTranslationSourceValidationError(
                "A bounded tuple of exact label keys is required.")
        fields = tuple(template_label_field(key) for key in label_keys)
        return await self.read_for_actor(
            actor=actor, version_id=version_id, field_names=fields)

    async def read_for_actor(self, *, actor, version_id, field_names):
        w = self.workflow
        w.sources._actor(actor)
        fields = self._fields(field_names)
        if not isinstance(version_id, UUID):
            raise TemplateTranslationSourceValidationError(
                "An exact published version UUID is required.")

        transaction = self.session.get_transaction()
        if (
            transaction is None
            or self.session.in_transaction() is not True
            or self.session.get_nested_transaction() is not None
            or any(
                bool(getattr(self.session, name, ()))
                for name in ("new", "dirty", "deleted"))
        ):
            raise TemplateTranslationWorkflowError(
                "A clean active Construction transaction is required.")

        try:
            with self.session.no_autoflush:
                contexts, choices, cached_rows = {}, {}, {}
                pending, written = {}, set()

                # Resolve every field through the canonical authorized reader.
                for field in sorted(fields):
                    context = await w._read(
                        actor, version_id, field, transaction)
                    contexts[field] = context
                    self._coherent(contexts)

                    cached = None
                    if (
                        context.source is not None
                        and context.source.source_language != context.target_language
                    ):
                        _, cached = await w._row(context, transaction)
                    cached_rows[field] = cached
                    choices[field] = w._choice(context, cached)

                # No provider call is made while holding a batch write lock.
                for field in sorted(fields):
                    if choices[field] is None:
                        pending[field] = await self._prepare(
                            contexts[field], cached_rows[field], transaction)
                        await w._recheck(contexts[field], actor, transaction)

                await self._recheck_all(contexts, actor, transaction)

                if pending:
                    await w.authorizer.serialization.lock_member(
                        tenant_id=actor.tenant_id, user_id=actor.user_id)
                    w._same(transaction)
                    await self._recheck_all(contexts, actor, transaction)

                    # Stable field order also covers overlapping batches.
                    for field in sorted(pending):
                        choices[field], changed = await self._persist(
                            contexts[field], pending[field], transaction)
                        if changed:
                            written.add(field)

                await self._recheck_all(contexts, actor, transaction)
                results = tuple(
                    w._result(contexts[field], choices[field], field in written)
                    for field in fields
                )
                if written:
                    await self.session.commit()
                else:
                    await self.session.rollback()
                return results
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise TemplateTranslationWorkflowError(
                    "Batch translation rollback failed.") from None
            if not isinstance(error, Exception) or isinstance(error, KNOWN_ERRORS):
                raise
            raise TemplateTranslationWorkflowError(
                "Template batch translation failed.") from None


def build_construction_template_translation_batch_workflow(
    *, session, platform_session, provider=None, now_provider=None,
):
    from services.construction.construction_template_translation_workflow import (
        build_construction_template_translation_workflow,
    )
    return ConstructionTemplateTranslationBatchWorkflow(
        workflow=build_construction_template_translation_workflow(
            session=session, platform_session=platform_session,
            provider=provider, now_provider=now_provider))


def build_construction_starter_translation_batch_workflow(
    *, session, platform_session, provider=None, now_provider=None,
):
    from services.construction.construction_starter_translation import (
        build_construction_starter_translation_workflow,
    )
    return ConstructionTemplateTranslationBatchWorkflow(
        workflow=build_construction_starter_translation_workflow(
            session=session, platform_session=platform_session,
            provider=provider, now_provider=now_provider))
