"""Bounded area-name translation through shared project authority and cache batch."""
from dataclasses import dataclass
from uuid import UUID

from services.construction.construction_area_read import (
    ConstructionProjectAreaReadService, ProjectAreaReadItem, project_area_capabilities,
)
from services.construction.construction_areas import ConstructionProjectAreaProjectNotFoundError
from services.construction.construction_content_translation import (
    ContentTranslationSource, ContentFieldKind,
)
from services.construction.construction_project_translation import (
    ConstructionProjectTranslationWorkflow, ProjectTextContext, ProjectTranslatedText,
    ProjectTranslationOperationError as Operation,
    ProjectTranslationValidationError as Validation,
    ProjectTranslationContextChanged as Changed,
)
from services.construction.construction_project_translation_batch import (
    ConstructionProjectTranslationBatchWorkflow,
)
from services.construction.construction_projects import (
    ConstructionProjectNotFoundError, CONSTRUCTION_PROJECT_STATUS_TRANSITIONS,
)

MAX_AREA_TRANSLATIONS = 32


@dataclass(frozen=True)
class _AreaContext(ProjectTextContext):
    area_id: UUID

    def source(self, source_language):
        return ContentTranslationSource(
            entity_type="construction.project_area", entity_id=self.area_id,
            field_name="name", source_language=source_language,
            source_version=self.source_version, original_text=self.original_text,
            field_kind=ContentFieldKind.TEXT)


class _AreaAuthority(ConstructionProjectTranslationWorkflow):
    def __init__(self, *, parent, cache):
        super().__init__(
            session=parent.session, platform_session=parent.platform_session,
            project_service=parent.projects, access_repository=parent.access,
            identity=parent.identity, language_repository=parent.languages,
            serialization=parent.serialization, cache_repository=cache,
            translator=parent.translator, suite_access=parent.suite,
            module_access=parent.module, now_provider=parent.now_provider)
        self.resolver = parent.resolver

    async def _lookup_cache_rows(self, context, transaction, *, locked=False):
        if not isinstance(context, _AreaContext):
            raise Operation("A canonical area context is required.")
        return await self.cache.find_area_name(
            tenant_id=context.tenant_id, area_id=context.area_id,
            target_language=context.target_language,
            source_version=context.source_version,
            source_text_hash=context.text_hash, for_update=locked)


class _Contexts(dict):
    def __init__(self, fields, *, actor, project_id, target_language, project_state, rows):
        super().__init__(fields)
        self.actor = actor
        self.project_id = project_id
        self.target_language = target_language
        self.project_state = project_state
        self.rows = rows
        self.signature = (
            target_language, project_state,
            tuple(tuple(row[key] for key in ProjectAreaReadItem.model_fields) for row in rows),
        )


@dataclass(frozen=True)
class TranslatedAreaName:
    area_id: UUID
    text: ProjectTranslatedText


@dataclass(frozen=True)
class AreaTextBatchResult:
    names: tuple[TranslatedAreaName, ...]
    target_language: str
    capabilities: dict[str, bool]

    @property
    def texts(self):
        return tuple(item.text for item in self.names)


class ConstructionAreaTranslationWorkflow(ConstructionProjectTranslationBatchWorkflow):
    def __init__(self, *, project_workflow, area_service, cache_repository, area_ids=None):
        if (
            not isinstance(project_workflow, ConstructionProjectTranslationWorkflow)
            or not isinstance(area_service, ConstructionProjectAreaReadService)
            or area_service.session is not project_workflow.session
            or getattr(cache_repository, "session", None) is not project_workflow.session
            or not callable(getattr(cache_repository, "find_area_name", None))
        ):
            raise ValueError("Shared-session canonical area collaborators are required.")
        if area_ids is not None and (
            not isinstance(area_ids, tuple) or not 1 <= len(area_ids) <= MAX_AREA_TRANSLATIONS
            or any(not isinstance(value, UUID) for value in area_ids)
            or len(set(area_ids)) != len(area_ids)
        ):
            raise Validation("A bounded tuple of distinct area UUIDs is required.")
        self.areas, self.area_ids = area_service, area_ids
        super().__init__(workflow=_AreaAuthority(
            parent=project_workflow, cache=cache_repository))

    async def _snapshot(self, actor, project_id, transaction, selected, expected_signature=None):
        w = self.workflow
        current, access = await w._authority(actor, project_id, transaction)
        project = await w.projects.get_project(
            actor=current, access_context=access, project_id=project_id)
        w._same(transaction)
        version, status = project.row_version, project.status
        if (
            type(version) is not int or not 1 <= version <= 2**63 - 1
            or status not in CONSTRUCTION_PROJECT_STATUS_TRANSITIONS
        ):
            raise Operation("Canonical parent project state is invalid.")
        parent_state = (project_id, version, status)
        try:
            rows = await self.areas.list_areas(
                actor=current, access_context=access, project_id=project_id)
        except ConstructionProjectAreaProjectNotFoundError:
            raise ConstructionProjectNotFoundError(
                "Project or selected area is unavailable.") from None
        w._same(transaction)
        after, access = await w._authority(actor, project_id, transaction)
        if after.language_code != current.language_code:
            raise Changed("Profile language changed.")
        if not isinstance(rows, list):
            raise Operation("Canonical area list is invalid.")
        models = tuple(ProjectAreaReadItem.model_validate(row) for row in rows)
        if any(item.row_version > 2**63 - 1 for item in models):
            raise Operation("Canonical area version is invalid.")
        canonical_rows = tuple(item.model_dump(mode="json") for item in models)
        context = _Contexts(
            {}, actor=after, project_id=project_id,
            target_language=after.language_code,
            project_state=parent_state, rows=canonical_rows)
        if expected_signature is not None and context.signature != expected_signature:
            raise Changed("Project area list or language changed.")
        ids = tuple(item.id for item in models) if selected is None else selected
        if len(ids) > MAX_AREA_TRANSLATIONS:
            raise Validation("Select at most 32 area IDs for one translation batch.")
        visible = {item.id for item in models}
        if set(ids) - visible:
            raise ConstructionProjectNotFoundError(
                "Project or selected area is unavailable.")
        chosen = set(ids)
        for item in models:
            if item.id in chosen:
                context[item.id] = _AreaContext(
                    actor.tenant_id, actor.user_id, project_id, "name",
                    after.language_code, item.row_version, item.name, item.id)
        return context, access

    async def _contexts(self, actor, project_id, transaction):
        return await self._snapshot(actor, project_id, transaction, self.area_ids)

    async def _check(self, actor, contexts, transaction):
        current, access = await self._snapshot(
            actor, contexts.project_id, transaction, tuple(contexts),
            expected_signature=contexts.signature)
        if current != contexts:
            raise Changed("Canonical area text changed.")
        return access

    def _validate_expected_source(self, expected_source):
        if expected_source is not None and not isinstance(expected_source, list):
            raise Validation("A canonical area list snapshot is required.")

    def _check_expected_source(self, contexts, expected_source):
        if expected_source is not None and list(contexts.rows) != expected_source:
            raise Changed("Area response source changed.")

    def _field_names(self, contexts):
        return tuple(contexts)

    def _batch_result(self, contexts, results, access):
        return AreaTextBatchResult(
            tuple(TranslatedAreaName(key, results[key]) for key in contexts),
            contexts.target_language,
            project_area_capabilities(
                actor=contexts.actor, access_context=access,
                project_id=contexts.project_id, project_status=contexts.project_state[2]))


def build_construction_area_translation_workflow(
    *, session, platform_session, area_ids=None, provider=None, now_provider=None,
):
    from database.repositories.construction.construction_project_content_translations import (
        ConstructionProjectContentTranslationRepository,
    )
    from services.construction.construction_area_read import (
        build_construction_project_area_read_service,
    )
    from services.construction.construction_project_translation import (
        build_construction_project_translation_workflow,
    )
    return ConstructionAreaTranslationWorkflow(
        project_workflow=build_construction_project_translation_workflow(
            session=session, platform_session=platform_session,
            provider=provider, now_provider=now_provider),
        area_service=build_construction_project_area_read_service(session=session),
        cache_repository=ConstructionProjectContentTranslationRepository(session),
        area_ids=area_ids)
