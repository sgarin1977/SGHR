"""Atomic name/comment translation using the existing authorized workflow."""
from dataclasses import dataclass
from uuid import UUID

from services.construction.construction_content_translation import (
    ContentTranslationRecord, ContentTranslationStatus as Status,
    PreparedContentTranslation, ContentTranslationUnavailable, _text,
)
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
)
from services.construction.construction_projects import ConstructionProjectNotFoundError
from services.construction.construction_project_translation import (
    ConstructionProjectTranslationWorkflow, ProjectTextContext, ProjectTranslatedText,
    ProjectTranslationOperationError as Operation,
    ProjectTranslationValidationError as Validation,
    ProjectTranslationContextChanged as Changed,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError

FIELDS = ("name", "comment")


@dataclass(frozen=True)
class ProjectTextBatchResult:
    texts: tuple[ProjectTranslatedText, ...]
    access_context: ConstructionAccessContext


class ConstructionProjectTranslationBatchWorkflow:
    def __init__(self, *, workflow):
        if not isinstance(workflow, ConstructionProjectTranslationWorkflow):
            raise ValueError("The canonical project translation workflow is required.")
        self.workflow = workflow
        self.session = workflow.session

    async def _contexts(self, actor, project_id, transaction):
        w = self.workflow
        current, access = await w._authority(actor, project_id, transaction)
        project = await w.projects.get_project(
            actor=current, access_context=access, project_id=project_id)
        w._same(transaction)
        version = getattr(project, "row_version", None)
        if type(version) is not int or not 1 <= version <= 2**63 - 1:
            raise Operation("Canonical project version is invalid.")
        contexts = {}
        for field in FIELDS:
            text = getattr(project, field, object())
            if text is not None:
                _text(text)
            elif field != "comment":
                raise Operation("Canonical project name is invalid.")
            contexts[field] = ProjectTextContext(
                actor.tenant_id, actor.user_id, project_id, field,
                current.language_code, version, text)
        after, access = await w._authority(actor, project_id, transaction)
        if after.language_code != current.language_code:
            raise Changed("Profile language changed.")
        return contexts, access

    async def _check(self, actor, contexts, transaction):
        current, access = await self._contexts(
            actor, contexts["name"].project_id, transaction)
        if current != contexts:
            raise Changed("Project text or profile language changed.")
        return access

    async def _prepare(self, actor, contexts, field, transaction):
        w, context = self.workflow, contexts[field]
        if context.original_text is None or not context.original_text.strip():
            return {"language": None, "pending": None}
        row, record = await w._row(context, transaction)
        if record is None:
            language = await w._detect(context.original_text)
            w._same(transaction)
            row, record = await w._row(context, transaction)
        else:
            language = record.key.source_language
        if record is not None:
            language = record.key.source_language
        if w._usable(record) or language == context.target_language:
            return {"language": language, "pending": None}

        await self._check(actor, contexts, transaction)
        source = context.source(language)
        prepared = await w.translator.prepare(
            source=source, target_language=context.target_language,
            records=() if record is None else (record,))
        w._same(transaction)
        if (
            not isinstance(prepared, PreparedContentTranslation)
            or prepared.source != source
            or prepared.target_language != context.target_language
            or not isinstance(prepared.pending, ContentTranslationRecord)
            or prepared.pending != prepared.record
            or prepared.pending.key != source.key_for(context.target_language)
            or prepared.pending.translation_status is not Status.AUTO_TRANSLATED
            or prepared.pending.reviewed_by is not None
        ):
            raise Operation("Prepared project translation differs.")
        return {"language": language, "pending": prepared.pending}

    async def _finish(self, context, plan, transaction, *, locked):
        w = self.workflow
        row, record, wrote = None, None, False
        if context.original_text is not None and context.original_text.strip():
            row, record = await w._row(context, transaction, locked=locked)
            if not w._usable(record):
                pending = plan["pending"]
                if pending is None:
                    if row is not None or plan["language"] != context.target_language:
                        raise Changed("Translation cache changed; retry.")
                else:
                    previous = row
                    if record is not None and record.key != pending.key:
                        raise Changed("Stored source language changed.")
                    if row is None:
                        saved = await w.cache.insert_automatic(
                            tenant_id=context.tenant_id, record=pending, as_of=w._now())
                        expected_version = 1
                    else:
                        saved = await w.cache.replace_stale_automatic(
                            tenant_id=context.tenant_id, record=pending,
                            expected_row_version=row["row_version"], as_of=w._now())
                        expected_version = row["row_version"] + 1
                    w._same(transaction)
                    row, record = await w._row(context, transaction, locked=True)
                    if saved is not None:
                        if (
                            row != saved or record != pending
                            or row["row_version"] != expected_version
                            or previous is not None and (
                                row["id"] != previous["id"]
                                or row["created_at"] != previous["created_at"])
                        ):
                            raise Operation("Saved project translation differs.")
                        wrote = True
                    if not w._usable(record):
                        raise Changed("Translation cache changed; retry.")

        return ProjectTranslatedText(
            tenant_id=context.tenant_id, user_id=context.user_id,
            project_id=context.project_id, field_name=context.field_name,
            target_language=context.target_language,
            source_language=plan["language"] if record is None else record.key.source_language,
            source_version=context.source_version,
            original_text=context.original_text,
            display_text=context.original_text if record is None else record.translated_text,
            translation_status=None if record is None else record.translation_status,
            translated_at=None if record is None else record.translated_at,
            reviewed_by=None if record is None else record.reviewed_by,
            cache_row_version=None if record is None else row["row_version"],
            cache_written=wrote)

    def _validate_expected_source(self, expected_source):
        if expected_source is not None and (
            not isinstance(expected_source, tuple) or len(expected_source) != 3
            or type(expected_source[0]) is not int
        ):
            raise Validation("A canonical project source snapshot is required.")

    def _check_expected_source(self, contexts, expected_source):
        observed = (
            contexts["name"].source_version,
            contexts["name"].original_text,
            contexts["comment"].original_text,
        )
        if expected_source is not None and observed != expected_source:
            raise Changed("Project response source changed.")

    def _field_names(self, contexts):
        return FIELDS

    def _batch_result(self, contexts, results, access):
        return ProjectTextBatchResult(
            tuple(results[field] for field in self._field_names(contexts)), access)

    async def read_for_actor(self, *, actor, project_id, expected_source=None):
        w = self.workflow
        w._actor(actor)
        if not isinstance(project_id, UUID):
            raise Validation("An exact project UUID is required.")
        self._validate_expected_source(expected_source)
        transaction = self.session.get_transaction()
        if transaction is None:
            raise Operation("An active outer transaction is required.")
        try:
            w._same(transaction)
            with self.session.no_autoflush:
                contexts, access = await self._contexts(actor, project_id, transaction)
                self._check_expected_source(contexts, expected_source)
                fields = self._field_names(contexts)

                plans = {}
                # Complete all provider work before membership/project/cache locks.
                for field in fields:
                    plans[field] = await self._prepare(
                        actor, contexts, field, transaction)
                    await self._check(actor, contexts, transaction)

                pending = any(plan["pending"] is not None for plan in plans.values())
                if pending:
                    await w.serialization.lock_member(
                        tenant_id=actor.tenant_id, user_id=actor.user_id)
                    w._same(transaction)
                    await self._check(actor, contexts, transaction)
                    project = await w.projects.repository.get_active_project_for_update(
                        tenant_id=actor.tenant_id, project_id=project_id)
                    w._same(transaction)
                    if (
                        project is None or project.id != project_id
                        or project.tenant_id != actor.tenant_id
                        or project.deleted_at is not None
                    ):
                        raise ConstructionProjectNotFoundError(
                            "Construction project is not available.")
                    await self._check(actor, contexts, transaction)

                results = {}
                for field in sorted(fields):
                    results[field] = await self._finish(
                        contexts[field], plans[field], transaction, locked=pending)
                access = await self._check(actor, contexts, transaction)
                result = self._batch_result(contexts, results, access)
                if any(item.cache_written for item in result.texts):
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
                raise Operation("Project translation rollback failed.") from None
            if not isinstance(error, Exception) or isinstance(error, (
                Operation, Validation, Changed, ContentTranslationUnavailable,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionProjectNotFoundError, ModuleDisabledError, SuiteDisabledError,
            )):
                raise
            raise Operation("Project translation batch failed.") from None

# CON_020B_AREA_LOCALIZED_READ_V1
