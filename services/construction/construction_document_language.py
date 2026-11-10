"""CON-020D: explicit document languages independent of interface language.

Read-only preparation using the existing canonical Platform language context.
The caller owns its transaction and must separately authorize the actual
document operation. A language plan grants no document or Construction access.
Document publication must persist its selected languages and translated content
in the exact document version; this preparation does not publish that version.
"""
from dataclasses import dataclass
from uuid import UUID

from services.api_identity import ApiActorContext
from services.construction.construction_content_translation import (
    ContentTranslationContractError,
    _language,
)
from services.construction.construction_language_context import (
    ConstructionLanguageContext,
    ConstructionLanguageContextService,
    ConstructionLanguageOption,
    ConstructionLanguageAccessError,
    ConstructionLanguageConfigurationError,
    ConstructionLanguageOperationError,
    build_construction_language_context_service,
)


class ConstructionDocumentLanguageValidationError(ValueError):
    pass


class ConstructionDocumentLanguageAccessError(ConstructionLanguageAccessError):
    pass


class ConstructionDocumentLanguageConfigurationError(
    ConstructionLanguageConfigurationError
):
    pass


class ConstructionDocumentLanguageOperationError(ConstructionLanguageOperationError):
    pass


class ConstructionDocumentLanguageContextChanged(Exception):
    code = "DOCUMENT_LANGUAGE_CONTEXT_CHANGED"


def validate_document_languages(value):
    if not isinstance(value, tuple) or not 1 <= len(value) <= 2:
        raise ConstructionDocumentLanguageValidationError(
            "Explicitly select one or two document languages.")
    try:
        for code in value:
            _language(code)
    except ContentTranslationContractError:
        raise ConstructionDocumentLanguageValidationError(
            "Exact canonical language codes are required.") from None
    if len(set(value)) != len(value):
        raise ConstructionDocumentLanguageValidationError(
            "Document languages must be distinct.")
    return value


@dataclass(frozen=True)
class ConstructionDocumentLanguagePlan:
    tenant_id: UUID
    user_id: UUID
    interface_language: str
    organization_default_language: str
    organization_row_version: int
    document_languages: tuple[str, ...]

    def __post_init__(self):
        if (
            not isinstance(self.tenant_id, UUID)
            or not isinstance(self.user_id, UUID)
            or type(self.organization_row_version) is not int
            or not 1 <= self.organization_row_version <= 2**63 - 1
        ):
            raise ConstructionDocumentLanguageValidationError(
                "Canonical language plan context is required.")
        validate_document_languages(self.document_languages)
        try:
            _language(self.interface_language)
            _language(self.organization_default_language)
        except ContentTranslationContractError:
            raise ConstructionDocumentLanguageValidationError(
                "Canonical profile and organization languages are required.") from None

    @property
    def bilingual(self):
        return len(self.document_languages) == 2


class ConstructionDocumentLanguageService:
    def __init__(self, *, session, language_context):
        if (
            not isinstance(language_context, ConstructionLanguageContextService)
            or language_context.session is not session
            or getattr(language_context.repository, "session", None) is not session
            or getattr(language_context.identity, "session", None) is not session
            or not hasattr(session, "no_autoflush")
            or not callable(getattr(session, "expire_all", None))
        ):
            raise ValueError(
                "Document languages require the shared canonical Platform reader.")
        self.session = session
        self.language_context = language_context

    @staticmethod
    def _actor(actor):
        if (
            not isinstance(actor, ApiActorContext)
            or actor.status != "active"
            or not isinstance(actor.user_id, UUID)
            or not isinstance(actor.tenant_id, UUID)
        ):
            raise ConstructionDocumentLanguageAccessError(
                "Document language context is unavailable.")

    def _clean(self):
        if any(
            bool(getattr(self.session, name, ()))
            for name in ("new", "dirty", "deleted")
        ):
            raise ConstructionDocumentLanguageOperationError(
                "A clean Platform read session is required.")

    @staticmethod
    def _context(state, actor):
        if (
            not isinstance(state, ConstructionLanguageContext)
            or state.tenant_id != actor.tenant_id
            or state.user_id != actor.user_id
            or type(state.row_version) is not int
            or not 1 <= state.row_version <= 2**63 - 1
            or not isinstance(state.languages, tuple)
            or not state.languages
            or any(
                not isinstance(option, ConstructionLanguageOption)
                for option in state.languages)
        ):
            raise ConstructionDocumentLanguageOperationError(
                "Canonical language response context differs.")
        try:
            _language(state.interface_language)
            _language(state.organization_default_language)
        except ContentTranslationContractError:
            raise ConstructionDocumentLanguageConfigurationError(
                "Canonical language configuration is required.") from None
        by_code = {option.code: option for option in state.languages}
        if (
            len(by_code) != len(state.languages)
            or state.interface_language not in by_code
            or state.organization_default_language not in by_code
            or by_code[state.organization_default_language].enabled_for_organization
                is not True
        ):
            raise ConstructionDocumentLanguageConfigurationError(
                "Canonical language configuration is required.")
        return state

    async def _read(self, actor):
        self._clean()
        self.session.expire_all()
        state = await self.language_context.read_for_actor(actor=actor)
        self._clean()
        return self._context(state, actor)

    async def prepare_for_actor(self, *, actor, document_languages):
        self._actor(actor)
        selected = validate_document_languages(document_languages)
        try:
            with self.session.no_autoflush:
                first = await self._read(actor)
                available = {option.code for option in first.languages}
                if any(code not in available for code in selected):
                    raise ConstructionDocumentLanguageValidationError(
                        "Selected document language is not active in Platform.")

                # Refresh identity, membership and the active catalog again.
                second = await self._read(actor)
                if second != first:
                    raise ConstructionDocumentLanguageContextChanged(
                        "Language context changed. Reload the selection.")

                return ConstructionDocumentLanguagePlan(
                    tenant_id=second.tenant_id,
                    user_id=second.user_id,
                    interface_language=second.interface_language,
                    organization_default_language=second.organization_default_language,
                    organization_row_version=second.row_version,
                    document_languages=selected)
        except ConstructionLanguageAccessError:
            raise ConstructionDocumentLanguageAccessError(
                "Document language context is unavailable.") from None
        except ConstructionLanguageConfigurationError:
            raise ConstructionDocumentLanguageConfigurationError(
                "Canonical Platform language configuration is required.") from None
        except (
            ConstructionDocumentLanguageValidationError,
            ConstructionDocumentLanguageContextChanged,
        ):
            raise
        except ConstructionLanguageOperationError:
            raise ConstructionDocumentLanguageOperationError(
                "Document language preparation failed.") from None
        except Exception:
            raise ConstructionDocumentLanguageOperationError(
                "Document language preparation failed.") from None


def build_construction_document_language_service(*, platform_session):
    return ConstructionDocumentLanguageService(
        session=platform_session,
        language_context=build_construction_language_context_service(
            platform_session=platform_session))
