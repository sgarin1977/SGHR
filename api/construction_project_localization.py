"""Optional localized representation of the existing project GET."""
from contextlib import asynccontextmanager
from datetime import timezone
from typing import Literal
from uuid import UUID

from fastapi import Depends, Query
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictStr,
    field_validator, model_validator,
)

from api.construction_settings_contract import SettingsVersion
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.routes.construction import get_construction_scoped_session
from services.construction.construction_content_translation import (
    ContentTranslationUnavailable, _text,
)
from services.construction.construction_project_translation import (
    ProjectTranslatedText, ProjectTranslationOperationError,
    ProjectTranslationValidationError, ProjectTranslationContextChanged,
    ProjectTranslationLanguageError, build_construction_project_translation_workflow,
)
from services.construction.construction_project_translation_batch import (
    ConstructionProjectTranslationBatchWorkflow, ProjectTextBatchResult,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError

LOCALIZATION_ERRORS = (
    ProjectTranslationOperationError, ProjectTranslationValidationError,
    ProjectTranslationContextChanged, ContentTranslationUnavailable,
    ModuleDisabledError, SuiteDisabledError,
)


class ProjectTranslatedField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_text: StrictStr | None
    display_text: StrictStr | None
    source_language: StrictStr | None = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    translation_status: Literal["VERIFIED", "AUTO_TRANSLATED"] | None
    translated_at: AwareDatetime | None
    reviewed_by: UUID | None
    cache_row_version: SettingsVersion | None
    cache_written: StrictBool

    @field_validator("original_text", "display_text")
    @classmethod
    def bounded_text(cls, value):
        return None if value is None else _text(value)

    @field_validator("translated_at")
    @classmethod
    def utc_time(cls, value):
        return None if value is None else value.astimezone(timezone.utc)


class ProjectTranslationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    source_version: SettingsVersion
    name: ProjectTranslatedField
    comment: ProjectTranslatedField

    @model_validator(mode="after")
    def coherent_translation(self):
        if self.name.original_text is None:
            raise ValueError("Project name cannot be absent.")
        for item in (self.name, self.comment):
            nonempty = item.original_text is not None and bool(item.original_text.strip())
            if item.translation_status is None:
                if (
                    item.display_text != item.original_text
                    or item.translated_at is not None or item.reviewed_by is not None
                    or item.cache_row_version is not None or item.cache_written
                    or nonempty and item.source_language != self.target_language
                    or not nonempty and item.source_language is not None
                ):
                    raise ValueError("Original text metadata differs.")
            elif (
                not nonempty or item.display_text is None or not item.display_text.strip()
                or item.source_language is None
                or item.source_language == self.target_language
                or item.translated_at is None or item.cache_row_version is None
                or item.translation_status == "AUTO_TRANSLATED" and item.reviewed_by is not None
                or item.cache_written and item.translation_status != "AUTO_TRANSLATED"
            ):
                raise ValueError("Translation metadata differs.")
        return self


class PlainProjectMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: dict[str, StrictBool]


class LocalizedProjectMeta(PlainProjectMeta):
    translations: ProjectTranslationMetadata


async def get_project_localization_platform_session(
    include_translations: Literal["false", "true"] = Query(default="false"),
):
    if include_translations == "false":
        yield None
        return
    async with asynccontextmanager(get_api_session)() as session:
        yield session


def get_optional_project_localizer(
    platform_session=Depends(get_project_localization_platform_session),
    session=Depends(get_construction_scoped_session),
):
    if platform_session is None:
        return None
    try:
        workflow = build_construction_project_translation_workflow(
            session=session, platform_session=platform_session)
        return ConstructionProjectTranslationBatchWorkflow(workflow=workflow)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Project translation runtime is unavailable.") from None


async def validate_project_localization_request(request, *, localized):
    if len(request.query_params.getlist("include_translations")) > 1:
        raise ProjectTranslationValidationError("Duplicate presentation selector.")
    if localized and (
        set(request.query_params) - {"include_translations"} or await request.body()
    ):
        raise ProjectTranslationValidationError(
            "Localized reads accept no language, tenant, user or source selectors.")


def public_project_translations(result, *, actor, project_id, project_data):
    if not isinstance(result, ProjectTextBatchResult) or len(result.texts) != 2:
        raise ValueError("Project translation batch differs.")
    fields = {}
    languages = set()
    for item in result.texts:
        if (
            not isinstance(item, ProjectTranslatedText)
            or item.field_name not in ("name", "comment") or item.field_name in fields
            or item.tenant_id != actor.tenant_id or item.user_id != actor.user_id
            or item.project_id != project_id
            or item.source_version != project_data["row_version"]
            or item.original_text != project_data[item.field_name]
        ):
            raise ValueError("Project translation response context differs.")
        languages.add(item.target_language)
        fields[item.field_name] = {
            name: getattr(item, name) for name in ProjectTranslatedField.model_fields
        }
    if set(fields) != {"name", "comment"} or len(languages) != 1:
        raise ValueError("Project translation language context differs.")
    return ProjectTranslationMetadata.model_validate({
        "target_language": next(iter(languages)),
        "source_version": project_data["row_version"],
        **fields,
    })


def project_localization_error(error):
    if isinstance(error, (ModuleDisabledError, SuiteDisabledError)):
        return ApiHttpError(
            status_code=403, code="MODULE_DISABLED",
            message="Construction module is unavailable.")
    if isinstance(error, ProjectTranslationContextChanged):
        return ApiHttpError(
            status_code=409, code="SOURCE_CONTEXT_CHANGED",
            message="Project text or profile language changed. Reload the project.")
    if isinstance(error, ProjectTranslationLanguageError):
        return ApiHttpError(
            status_code=409, code="LANGUAGE_CONFIGURATION_REQUIRED",
            message="An active Platform language configuration is required.")
    if isinstance(error, ProjectTranslationValidationError):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Project presentation request is invalid.")
    if isinstance(error, ContentTranslationUnavailable):
        return ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Content translation is unavailable.")
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Project translation failed.")


def install_project_localization_openapi_precision(application):
    if getattr(application.state, "_project_localization_precision", False):
        return
    previous = application.openapi
    targets = {
        "ProjectTranslationMetadata": "source_version",
        "ProjectTranslatedField": "cache_row_version",
    }
    def exact_openapi():
        document = previous()
        for schema in document.get("components", {}).get("schemas", {}).values():
            name = targets.get(schema.get("title"))
            if name is None:
                continue
            field = schema["properties"][name]
            branches = [field] if field.get("type") == "integer" else field.get("anyOf", [])
            integers = [item for item in branches if item.get("type") == "integer"]
            if len(integers) != 1:
                raise ValueError("Project translation counter schema differs.")
            integers[0].update(minimum=1, maximum=2**63 - 1, format="int64")
        return document
    application.openapi = exact_openapi
    application.state._project_localization_precision = True
