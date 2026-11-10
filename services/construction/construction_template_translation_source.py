"""Authorized published-template text for version-bound translation.

The target language comes from a fresh canonical Platform profile.
The original language and text come from the exact published template version.
No provider calls, cache writes, commits, module activation or new permissions.
The calling API/workflow retains the shared Suite/Core and rate-limit boundary.
"""
from dataclasses import dataclass
from uuid import UUID
from hashlib import sha256
import re
from services.construction.construction_starter_template_definitions import _key

from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_content_translation import (
    ContentTranslationSource, ContentFieldKind,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError,
    ConstructionOnboardingStateConflictError,
    ConstructionOnboardingValidationError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateService,
    ConstructionOrganizationTemplateNotFoundError,
    OrganizationTemplateVersionState,
)
from services.construction.construction_organization_template_definitions import (
    ValidatedOrganizationTemplateDefinition,
)


class TemplateTranslationSourceError(Exception):
    pass


class TemplateTranslationSourceValidationError(ValueError):
    pass


class TemplateTranslationSourceDenied(ConstructionPermissionDeniedError):
    pass


class TemplateTranslationLanguageError(TemplateTranslationSourceError):
    code = "LANGUAGE_CONFIGURATION_REQUIRED"


class TemplateTranslationLanguageChanged(TemplateTranslationLanguageError):
    code = "LANGUAGE_CONTEXT_CHANGED"


def template_label_field(label_key):
    try:
        key = _key(label_key)
    except (ValueError, TypeError):
        raise TemplateTranslationSourceValidationError(
            "An exact canonical label key is required.") from None
    # Stable lower-case field identity fits the already applied cache contract.
    # The original key remains unchanged in the template and API response.
    return "label." + sha256(key.encode("ascii")).hexdigest()


def is_template_text_field(value):
    return isinstance(value, str) and (
        value in ("title", "description")
        or re.fullmatch(r"label[.][0-9a-f]{64}", value) is not None)


@dataclass(frozen=True)
class AuthorizedTemplateTranslationSource:
    tenant_id: UUID
    user_id: UUID
    template_id: UUID
    version_id: UUID
    field_name: str
    target_language: str
    content_hash: str
    source: ContentTranslationSource | None
    supplied_text: str | None


class ConstructionTemplateTranslationSourceService:
    def __init__(
        self, *, session, platform_session, template_service,
        identity, language_repository,
    ):
        if (
            not isinstance(template_service, ConstructionOrganizationTemplateService)
            or template_service.session is not session
            or template_service.authorizer.session is not session
            or getattr(identity, "session", None) is not platform_session
            or not callable(getattr(identity, "require_actor", None))
            or getattr(language_repository, "session", None) is not platform_session
            or not callable(getattr(language_repository, "is_active_language", None))
            or not hasattr(platform_session, "no_autoflush")
            or not callable(getattr(platform_session, "expire_all", None))
        ):
            raise ValueError("Canonical shared-session source collaborators are required.")
        self.session = session
        self.platform_session = platform_session
        self.templates = template_service
        self.identity = identity
        self.languages = language_repository

    @staticmethod
    def _actor(actor):
        if (
            not isinstance(actor, ApiActorContext)
            or actor.status != "active"
            or not isinstance(actor.user_id, UUID)
            or not isinstance(actor.tenant_id, UUID)
        ):
            raise TemplateTranslationSourceDenied("Template text identity is unavailable.")

    async def _current(self, actor):
        self._actor(actor)
        if any(bool(getattr(self.platform_session, name, ()))
               for name in ("new", "dirty", "deleted")):
            raise TemplateTranslationSourceError(
                "A clean Platform identity read session is required.")
        with self.platform_session.no_autoflush:
            self.platform_session.expire_all()
            try:
                current = await self.identity.require_actor(
                    user_id=actor.user_id, tenant_id=actor.tenant_id)
            except ApiIdentityAccessError:
                raise TemplateTranslationSourceDenied(
                    "Template text identity is unavailable.") from None
            self._actor(current)
            if (
                current.user_id != actor.user_id
                or current.tenant_id != actor.tenant_id
            ):
                raise TemplateTranslationSourceDenied(
                    "Template text identity context differs.")
            if (
                not isinstance(current.language_code, str)
                or not current.language_code
                or await self.languages.is_active_language(
                    current.language_code) is not True
            ):
                raise TemplateTranslationLanguageError(
                    "An active profile language is required.")
            return current

    @staticmethod
    def _source(state, *, actor, version_id, field_name, target_language):
        if (
            not isinstance(state, OrganizationTemplateVersionState)
            or state.tenant_id != actor.tenant_id
            or state.version_id != version_id
            or not isinstance(state.definition, ValidatedOrganizationTemplateDefinition)
            or state.content_hash != state.definition.content_hash
            or state.source_language != state.definition.source_language
        ):
            raise TemplateTranslationSourceError("Canonical template context differs.")

        definition = state.definition.payload()
        source_labels = definition["labels"][state.source_language]
        if field_name in ("title", "description"):
            label_key = definition[
                "title_key" if field_name == "title" else "description_key"]
            original = None if label_key is None else source_labels[label_key]
            if original != getattr(state, field_name):
                raise TemplateTranslationSourceError("Canonical template text differs.")
        else:
            matches = [
                key for key in source_labels
                if template_label_field(key) == field_name
            ]
            if not matches:
                raise ConstructionOrganizationTemplateNotFoundError(
                    "Published template label is unavailable.")
            if len(matches) != 1:
                raise TemplateTranslationSourceError("Canonical label identity is ambiguous.")
            label_key = matches[0]
            original = source_labels[label_key]

        source = None if original is None else ContentTranslationSource(
            entity_type="construction.measurement_template_version",
            entity_id=version_id,
            field_name=field_name,
            source_language=state.source_language,
            source_version=state.version,
            original_text=original,
            field_kind=ContentFieldKind.TEXT)

        supplied = (
            None if label_key is None
            else definition["labels"].get(target_language, {}).get(label_key))
        # Supplied text is canonical content of this published version.
        # This reader does not create a VERIFIED cache record or reviewer identity.
        return AuthorizedTemplateTranslationSource(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            template_id=state.template_id,
            version_id=version_id,
            field_name=field_name,
            target_language=target_language,
            content_hash=state.content_hash,
            source=source,
            supplied_text=supplied)

    async def read_for_actor(self, *, actor, version_id, field_name):
        self._actor(actor)
        if (
            not isinstance(version_id, UUID)
            or not is_template_text_field(field_name)
        ):
            raise TemplateTranslationSourceValidationError(
                "An exact published version and supported text field are required.")
        authorizer = self.templates.authorizer
        transaction = self.session.get_transaction()
        if (
            transaction is None or self.session.in_transaction() is not True
            or self.session.get_nested_transaction() is not None
            or any(bool(getattr(self.session, name, ()))
                   for name in ("new", "dirty", "deleted"))
        ):
            raise TemplateTranslationSourceError(
                "A clean active Construction transaction is required.")

        try:
            first = await self._current(actor)
            authorizer._same_transaction(transaction)
            state = await self.templates.get_version(
                actor=first, version_id=version_id)
            authorizer._same_transaction(transaction)

            second = await self._current(actor)
            authorizer._same_transaction(transaction)
            if second.language_code != first.language_code:
                raise TemplateTranslationLanguageChanged(
                    "Profile language changed while reading template text.")

            await authorizer._authorize(second)
            authorizer._same_transaction(transaction)
            return self._source(
                state, actor=second, version_id=version_id,
                field_name=field_name, target_language=second.language_code)
        except (
            TemplateTranslationSourceError,
            TemplateTranslationSourceValidationError,
            ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError,
            ConstructionOnboardingOperationError,
            ConstructionOnboardingStateConflictError,
            ConstructionOnboardingValidationError,
        ):
            raise
        except Exception:
            raise TemplateTranslationSourceError(
                "Canonical template text read failed.") from None


def build_construction_template_translation_source_service(
    *, session, platform_session, now_provider=None,
):
    from database.repositories.platform_catalog_translations import (
        PlatformCatalogTranslationRepository,
    )
    from services.api_identity import ApiIdentityService
    from services.construction.construction_organization_templates import (
        build_construction_organization_template_service,
    )
    return ConstructionTemplateTranslationSourceService(
        session=session,
        platform_session=platform_session,
        template_service=build_construction_organization_template_service(
            session=session, now_provider=now_provider),
        identity=ApiIdentityService(platform_session),
        language_repository=PlatformCatalogTranslationRepository(platform_session))
