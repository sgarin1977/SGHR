"""Localized names in the existing universal-area list."""
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.routes.construction import get_construction_scoped_session
from api.construction_project_localization import (
    PlainProjectMeta, ProjectTranslatedField,
    get_project_localization_platform_session,
    LOCALIZATION_ERRORS, project_localization_error,
)
from services.construction.construction_project_translation import (
    ProjectTranslatedText, ProjectTranslationValidationError,
)
from services.construction.construction_projects import ConstructionProjectNotFoundError
from services.construction.construction_area_translation import (
    MAX_AREA_TRANSLATIONS, AreaTextBatchResult,
    build_construction_area_translation_workflow,
)

AREA_LOCALIZATION_ERRORS = LOCALIZATION_ERRORS + (ConstructionProjectNotFoundError,)


class AreaNameTranslation(ProjectTranslatedField):
    area_id: UUID
    source_version: SettingsVersion
    target_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    original_text: StrictStr
    display_text: StrictStr

    @model_validator(mode="after")
    def coherent_name(self):
        if not self.original_text.strip() or not self.display_text.strip():
            raise ValueError("An area name is required.")
        if self.translation_status is None:
            if (
                self.display_text != self.original_text
                or self.source_language != self.target_language
                or self.translated_at is not None or self.reviewed_by is not None
                or self.cache_row_version is not None or self.cache_written
            ):
                raise ValueError("Original area name metadata differs.")
        elif (
            self.source_language is None or self.source_language == self.target_language
            or self.translated_at is None or self.cache_row_version is None
            or self.translation_status == "AUTO_TRANSLATED" and self.reviewed_by is not None
            or self.cache_written and self.translation_status != "AUTO_TRANSLATED"
        ):
            raise ValueError("Translated area name metadata differs.")
        return self


class AreaTranslationsMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    items: list[AreaNameTranslation] = Field(max_length=MAX_AREA_TRANSLATIONS)

    @model_validator(mode="after")
    def coherent_items(self):
        if (
            len({item.area_id for item in self.items}) != len(self.items)
            or any(item.target_language != self.target_language for item in self.items)
        ):
            raise ValueError("Area translation batch differs.")
        return self


class LocalizedAreaMeta(PlainProjectMeta):
    translations: AreaTranslationsMetadata


@dataclass(frozen=True)
class AreaLocalizationRequest:
    workflow: object
    area_ids: tuple[UUID, ...] | None


def get_optional_area_localizer(
    translation_area_ids: list[UUID] | None = Query(
        default=None, max_length=MAX_AREA_TRANSLATIONS),
    platform_session=Depends(get_project_localization_platform_session),
    session=Depends(get_construction_scoped_session),
):
    ids = None if translation_area_ids is None else tuple(translation_area_ids)
    if ids is not None and (
        not ids or len(set(ids)) != len(ids) or platform_session is None
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Distinct translation area IDs require include_translations=true.")
    if platform_session is None:
        return None
    try:
        workflow = build_construction_area_translation_workflow(
            session=session, platform_session=platform_session, area_ids=ids)
        return AreaLocalizationRequest(workflow, ids)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Area translation runtime is unavailable.") from None


async def validate_area_localization_request(request, *, localized):
    if (
        set(request.query_params) - {"include_translations", "translation_area_ids"}
        or len(request.query_params.getlist("include_translations")) > 1
        or localized and await request.body()
    ):
        raise ProjectTranslationValidationError("Area presentation selectors are invalid.")


def public_area_translations(result, *, actor, project_id, rows, selected_ids):
    if not isinstance(result, AreaTextBatchResult):
        raise ValueError("Area translation result differs.")
    originals = {UUID(row["id"]): row for row in rows}
    expected = set(originals) if selected_ids is None else set(selected_ids)
    seen, items = set(), []
    for value in result.names:
        text = value.text
        if (
            value.area_id not in expected or value.area_id in seen
            or not isinstance(text, ProjectTranslatedText)
            or text.tenant_id != actor.tenant_id or text.user_id != actor.user_id
            or text.project_id != project_id or text.field_name != "name"
            or value.area_id not in originals
            or text.source_version != originals[value.area_id]["row_version"]
            or text.original_text != originals[value.area_id]["name"]
            or text.target_language != result.target_language
        ):
            raise ValueError("Area translation response context differs.")
        seen.add(value.area_id)
        items.append({
            "area_id": value.area_id, "source_version": text.source_version,
            "target_language": text.target_language,
            **{key: getattr(text, key) for key in ProjectTranslatedField.model_fields},
        })
    if seen != expected:
        raise ValueError("Area translation selection differs.")
    return AreaTranslationsMetadata.model_validate({
        "target_language": result.target_language, "items": items})


def area_localization_error(error):
    if isinstance(error, ConstructionProjectNotFoundError):
        return ApiHttpError(
            status_code=404, code="not_found",
            message="Project or selected area is unavailable.")
    return project_localization_error(error)


def install_area_localization_openapi_precision(application):
    if getattr(application.state, "_area_localization_precision", False):
        return
    previous = application.openapi
    def exact_openapi():
        document = previous()
        for schema in document.get("components", {}).get("schemas", {}).values():
            if schema.get("title") != "AreaNameTranslation":
                continue
            for name in ("source_version", "cache_row_version"):
                field = schema["properties"][name]
                branches = [field] if field.get("type") == "integer" else field.get("anyOf", [])
                integers = [item for item in branches if item.get("type") == "integer"]
                if len(integers) != 1:
                    raise ValueError("Area translation counter schema differs.")
                integers[0].update(minimum=1, maximum=2**63 - 1, format="int64")
        return document
    application.openapi = exact_openapi
    application.state._area_localization_precision = True
