"""CON-020E: authorized metadata of an exact version-bound translation cache.

Reads the canonical source and fresh profile language through the existing
workflow. Never calls the translator or changes cache/source/business records.
Requires a clean active Construction transaction and ends it with rollback.
The Platform read session remains caller-owned.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from services.construction.construction_content_translation import (
    ContentTranslationStatus as Status,
)
from services.construction.construction_template_translation_source import (
    TemplateTranslationSourceError,
    TemplateTranslationSourceValidationError,
    is_template_text_field,
)
from services.construction.construction_template_translation_workflow import (
    ConstructionTemplateTranslationWorkflow,
    TemplateTranslationWorkflowError,
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


@dataclass(frozen=True)
class TemplateTranslationCacheState:
    tenant_id: UUID
    user_id: UUID
    template_id: UUID
    version_id: UUID
    field_name: str
    target_language: str
    source_language: str | None
    source_version: int | None
    source_text_hash: str | None
    cache_id: UUID | None
    translation_status: Status | None
    translated_at: datetime | None
    reviewed_by: UUID | None
    cache_row_version: int | None
    cache_present: bool
    usable: bool


class TemplateTranslationStateError(TemplateTranslationWorkflowError):
    pass


class ConstructionTemplateTranslationStateService:
    def __init__(self, *, workflow):
        if not isinstance(workflow, ConstructionTemplateTranslationWorkflow):
            raise ValueError("The canonical shared translation workflow is required.")
        self.workflow = workflow
        self.session = workflow.session

    @staticmethod
    def _state(context, row, cached):
        source = context.source
        record = None if cached is None else cached[0]
        return TemplateTranslationCacheState(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            template_id=context.template_id,
            version_id=context.version_id,
            field_name=context.field_name,
            target_language=context.target_language,
            source_language=None if source is None else source.source_language,
            source_version=None if source is None else source.source_version,
            source_text_hash=(
                None if source is None else
                source.key_for(context.target_language).source_text_hash),
            cache_id=None if row is None else row["id"],
            translation_status=None if record is None else record.translation_status,
            translated_at=None if record is None else record.translated_at,
            reviewed_by=None if record is None else record.reviewed_by,
            cache_row_version=None if cached is None else cached[1],
            cache_present=record is not None,
            usable=record is not None and record.translation_status in (
                Status.VERIFIED, Status.AUTO_TRANSLATED),
        )

    async def read_for_actor(self, *, actor, version_id, field_name):
        w = self.workflow
        w.sources._actor(actor)
        if not isinstance(version_id, UUID) or not is_template_text_field(field_name):
            raise TemplateTranslationSourceValidationError(
                "An exact published version and canonical text field are required.")

        transaction = self.session.get_transaction()
        if (
            transaction is None
            or self.session.in_transaction() is not True
            or self.session.get_nested_transaction() is not None
            or any(
                bool(getattr(self.session, name, ()))
                for name in ("new", "dirty", "deleted"))
        ):
            raise TemplateTranslationStateError(
                "A clean active Construction transaction is required.")

        try:
            with self.session.no_autoflush:
                context = await w._read(
                    actor, version_id, field_name, transaction)
                row, cached = None, None
                if (
                    context.source is not None
                    and context.source.source_language != context.target_language
                ):
                    # Exact tenant, entity, field, languages, source version/hash.
                    # Existing decoder validates every returned cache column.
                    row, cached = await w._row(context, transaction)

                await w._recheck(context, actor, transaction)
                result = self._state(context, row, cached)
                await self.session.rollback()
                return result
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise TemplateTranslationStateError(
                    "Translation state rollback failed.") from None

            if not isinstance(error, Exception):
                raise
            if isinstance(error, (
                TemplateTranslationSourceError,
                TemplateTranslationSourceValidationError,
                ConstructionOnboardingOperationError,
                ConstructionOnboardingStateConflictError,
                ConstructionOnboardingValidationError,
                ConstructionPermissionDeniedError,
                ConstructionScopeDeniedError,
                ModuleDisabledError,
                SuiteDisabledError,
            )):
                raise
            raise TemplateTranslationStateError(
                "Translation state read failed.") from None
