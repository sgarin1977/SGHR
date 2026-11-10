"""Global starter source adapter using the shared tenant translation workflow.

Only published visible starter versions are read. The global catalog is never
written and organization copies are never created. Cache rows remain scoped to
the authenticated organization through the existing translation repository.
"""
from services.construction.construction_content_translation import (
    ContentTranslationSource, ContentFieldKind, ConstructionContentTranslator,
)
from services.construction.construction_template_translation_source import (
    ConstructionTemplateTranslationSourceService,
    AuthorizedTemplateTranslationSource,
    TemplateTranslationSourceError,
    template_label_field,
)
from services.construction.construction_starter_templates import (
    ConstructionStarterTemplateService,
    ConstructionStarterTemplateVersionState,
    ConstructionStarterTemplateNotFoundError,
)
from services.construction.construction_starter_template_definitions import (
    ValidatedStarterDefinition,
)


class ConstructionStarterTranslationSourceService(
    ConstructionTemplateTranslationSourceService
):
    def __init__(
        self, *, session, platform_session, starter_service,
        identity, language_repository,
    ):
        # Specialize the canonical source type. The inherited read operation
        # retains fresh identity, profile language and settings authorization.
        if (
            not isinstance(starter_service, ConstructionStarterTemplateService)
            or starter_service.session is not session
            or starter_service.authorizer.session is not session
            or getattr(identity, "session", None) is not platform_session
            or not callable(getattr(identity, "require_actor", None))
            or getattr(language_repository, "session", None) is not platform_session
            or not callable(getattr(language_repository, "is_active_language", None))
            or not hasattr(platform_session, "no_autoflush")
            or not callable(getattr(platform_session, "expire_all", None))
        ):
            raise ValueError("Canonical starter source collaborators are required.")
        self.session = session
        self.platform_session = platform_session
        self.templates = starter_service
        self.identity = identity
        self.languages = language_repository

    @staticmethod
    def _source(state, *, actor, version_id, field_name, target_language):
        if (
            not isinstance(state, ConstructionStarterTemplateVersionState)
            or state.version_id != version_id
            or not isinstance(state.definition, ValidatedStarterDefinition)
            or state.content_hash != state.definition.content_hash
            or state.source_language != state.definition.source_language
        ):
            raise TemplateTranslationSourceError("Canonical starter context differs.")

        definition = state.definition.payload()
        labels = definition["labels"][state.source_language]
        if field_name in ("title", "description"):
            key = definition[
                "title_key" if field_name == "title" else "description_key"]
        else:
            matches = [
                key for key in labels
                if template_label_field(key) == field_name
            ]
            if not matches:
                raise ConstructionStarterTemplateNotFoundError(
                    "Published starter label is unavailable.")
            if len(matches) != 1:
                raise TemplateTranslationSourceError(
                    "Canonical starter label identity is ambiguous.")
            key = matches[0]
        original = None if key is None else labels[key]
        source = None if original is None else ContentTranslationSource(
            entity_type="construction.starter_measurement_template_version",
            entity_id=version_id, field_name=field_name,
            source_language=state.source_language,
            source_version=state.version, original_text=original,
            field_kind=ContentFieldKind.TEXT)
        supplied = (
            None if key is None
            else definition["labels"].get(target_language, {}).get(key))
        return AuthorizedTemplateTranslationSource(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
            template_id=state.template_id, version_id=version_id,
            field_name=field_name, target_language=target_language,
            content_hash=state.content_hash, source=source,
            supplied_text=supplied)


def build_construction_starter_translation_workflow(
    *, session, platform_session, provider=None, now_provider=None,
):
    from database.repositories.construction.construction_content_translations import (
        ConstructionContentTranslationRepository,
    )
    from database.repositories.platform_catalog_translations import (
        PlatformCatalogTranslationRepository,
    )
    from services.api_identity import ApiIdentityService
    from services.construction.construction_starter_templates import (
        build_construction_starter_template_service,
    )
    from services.construction.construction_template_translation_workflow import (
        ConstructionTemplateTranslationWorkflow,
    )
    from services.module_access import (
        build_tenant_suite_access_service, build_tenant_module_access_service,
    )
    from services.translation import LibreTranslateProvider

    source = ConstructionStarterTranslationSourceService(
        session=session, platform_session=platform_session,
        starter_service=build_construction_starter_template_service(
            session=session, now_provider=now_provider),
        identity=ApiIdentityService(platform_session),
        language_repository=PlatformCatalogTranslationRepository(platform_session))
    return ConstructionTemplateTranslationWorkflow(
        session=session, source_service=source,
        cache_repository=ConstructionContentTranslationRepository(session),
        translator=ConstructionContentTranslator(
            provider=provider if provider is not None else LibreTranslateProvider(),
            now_provider=now_provider),
        suite_access=build_tenant_suite_access_service(platform_session),
        module_access=build_tenant_module_access_service(platform_session))
