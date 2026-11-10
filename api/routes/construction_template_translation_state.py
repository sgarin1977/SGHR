"""Authorized exact-version translation cache metadata; no provider calls."""
from dataclasses import asdict
from typing import Literal
from uuid import UUID

from fastapi import Depends, Path, Request, Response
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field,
    StrictBool, StrictStr, model_validator,
)

from api.auth import get_current_actor, require_suite, require_module
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, require_construction_permission,
)
from api.routes.construction_organization_templates import (
    get_template_translation_workflow as get_org_workflow,
    translation_operation_error,
)
from api.routes.construction_starter_templates import (
    get_starter_translation_workflow as get_starter_workflow,
    starter_translation_error,
)
from services.construction.construction_template_translation_source import (
    TemplateTranslationSourceValidationError, template_label_field,
)
from services.construction.construction_template_translation_state import (
    ConstructionTemplateTranslationStateService,
    TemplateTranslationCacheState,
)


class TranslationStateData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    user_id: UUID
    template_id: UUID
    version_id: UUID
    field_name: Literal["title", "description"]
    target_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    source_language: StrictStr | None = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    source_version: SettingsVersion | None
    source_text_hash: StrictStr | None = Field(pattern=r"^[0-9a-f]{64}$")
    cache_id: UUID | None
    translation_status: Literal["VERIFIED", "AUTO_TRANSLATED", "STALE"] | None
    translated_at: AwareDatetime | None
    reviewed_by: UUID | None
    cache_row_version: SettingsVersion | None
    cache_present: StrictBool
    usable: StrictBool

    @model_validator(mode="after")
    def coherent_state(self):
        absent_source = self.source_version is None
        if absent_source:
            if (
                self.field_name != "description"
                or self.source_language is not None
                or self.source_text_hash is not None
            ):
                raise ValueError("Absent source metadata differs.")
        elif self.source_language is None or self.source_text_hash is None:
            raise ValueError("Source metadata is incomplete.")

        if self.cache_present:
            if (
                absent_source or self.source_language == self.target_language
                or self.cache_id is None or self.translation_status is None
                or self.translated_at is None or self.cache_row_version is None
                or self.usable is not (self.translation_status != "STALE")
            ):
                raise ValueError("Cache state metadata differs.")
        elif (
            any(value is not None for value in (
                self.cache_id, self.translation_status, self.translated_at,
                self.reviewed_by, self.cache_row_version))
            or self.usable
        ):
            raise ValueError("Absent cache cannot claim stored translation metadata.")
        return self


class LabelTranslationStateData(TranslationStateData):
    field_name: StrictStr = Field(
        pattern=r"^label[.][0-9a-f]{64}$", min_length=70, max_length=70)
    label_key: StrictStr = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$",
        min_length=1, max_length=128)


class TranslationStateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: TranslationStateData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


class LabelTranslationStateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: LabelTranslationStateData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def _service(workflow):
    try:
        return ConstructionTemplateTranslationStateService(workflow=workflow)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Translation state runtime is unavailable.") from None


def get_org_translation_state_service(workflow=Depends(get_org_workflow)):
    return _service(workflow)


def get_starter_translation_state_service(workflow=Depends(get_starter_workflow)):
    return _service(workflow)


def public_state(result, *, actor, version_id, field_name, label_key):
    if (
        not isinstance(result, TemplateTranslationCacheState)
        or result.tenant_id != actor.tenant_id
        or result.user_id != actor.user_id
        or result.version_id != version_id
        or result.field_name != field_name
    ):
        raise ValueError("Translation state response context differs.")
    values = asdict(result)
    if label_key is None:
        return TranslationStateData.model_validate(values)
    if template_label_field(label_key) != field_name:
        raise ValueError("Translation label identity differs.")
    return LabelTranslationStateData.model_validate(
        values | {"label_key": label_key})


async def state_response(
    *, actor, version_id, field_name, label_key,
    request, response, service, error_mapper,
):
    try:
        if request.query_params or await request.body():
            raise TemplateTranslationSourceValidationError(
                "Translation state reads accept no query selectors or body.")
        expected = (
            field_name if label_key is None else template_label_field(label_key))
        result = await service.read_for_actor(
            actor=actor, version_id=version_id, field_name=expected)
        data = public_state(
            result, actor=actor, version_id=version_id,
            field_name=expected, label_key=label_key)
    except Exception as error:
        raise error_mapper(error) from None

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


router = ConstructionAPIRouter(
    prefix="/construction/settings",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={
        401: {"description": "Platform authentication required"},
        403: {"description": "Membership, permission, scope or module unavailable"},
        404: {"description": "Exact published version or label unavailable"},
        409: {"description": "Language configuration or source context changed"},
        422: {"description": "Invalid selector; no query or body accepted"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        503: {"description": "Translation state runtime unavailable"},
        500: {"description": "Sanitized state read failure"},
    },
)
permission = require_construction_permission("construction.settings.manage")
ORG = "/measurement-templates/versions/{version_id}"
STARTER = "/starter-measurement-templates/versions/{version_id}"
DESCRIPTION = (
    "Read metadata of the exact tenant-scoped translation cache record for a "
    "published source version and the fresh Platform profile language. Returns "
    "persisted VERIFIED/AUTO_TRANSLATED/STALE, date and reviewer when present. "
    "Missing cache has null metadata; STALE has usable=false. Does not return "
    "translated text, call a provider, verify a translation or mutate history."
)


@router.get(
    ORG + "/texts/{field_name}/translation-state",
    response_model=TranslationStateEnvelope,
    dependencies=[Depends(permission)],
    description=DESCRIPTION,
)
async def read_org_text_translation_state(
    version_id: UUID, field_name: Literal["title", "description"],
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_org_translation_state_service),
):
    return await state_response(
        actor=actor, version_id=version_id, field_name=field_name, label_key=None,
        request=request, response=response, service=service,
        error_mapper=translation_operation_error)


@router.get(
    ORG + "/labels/{label_key}/translation-state",
    response_model=LabelTranslationStateEnvelope,
    dependencies=[Depends(permission)],
    description=DESCRIPTION,
)
async def read_org_label_translation_state(
    version_id: UUID, request: Request, response: Response,
    label_key: StrictStr = Path(
        ..., min_length=1, max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"),
    actor=Depends(get_current_actor),
    service=Depends(get_org_translation_state_service),
):
    return await state_response(
        actor=actor, version_id=version_id, field_name=None, label_key=label_key,
        request=request, response=response, service=service,
        error_mapper=translation_operation_error)


@router.get(
    STARTER + "/texts/{field_name}/translation-state",
    response_model=TranslationStateEnvelope,
    dependencies=[Depends(permission)],
    description=DESCRIPTION,
)
async def read_starter_text_translation_state(
    version_id: UUID, field_name: Literal["title", "description"],
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_starter_translation_state_service),
):
    return await state_response(
        actor=actor, version_id=version_id, field_name=field_name, label_key=None,
        request=request, response=response, service=service,
        error_mapper=starter_translation_error)


@router.get(
    STARTER + "/labels/{label_key}/translation-state",
    response_model=LabelTranslationStateEnvelope,
    dependencies=[Depends(permission)],
    description=DESCRIPTION,
)
async def read_starter_label_translation_state(
    version_id: UUID, request: Request, response: Response,
    label_key: StrictStr = Path(
        ..., min_length=1, max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"),
    actor=Depends(get_current_actor),
    service=Depends(get_starter_translation_state_service),
):
    return await state_response(
        actor=actor, version_id=version_id, field_name=None, label_key=label_key,
        request=request, response=response, service=service,
        error_mapper=starter_translation_error)


def install_translation_state_openapi_precision(application):
    previous = application.openapi
    if getattr(previous, "_translation_state_exact_counters", False):
        return

    def precise():
        document = previous()
        for schema in document.get("components", {}).get("schemas", {}).values():
            if schema.get("title") not in (
                "TranslationStateData", "LabelTranslationStateData",
            ):
                continue
            for name in ("source_version", "cache_row_version"):
                branches = schema["properties"][name].get("anyOf", [])
                integers = [item for item in branches if item.get("type") == "integer"]
                if len(integers) != 1:
                    raise ValueError("Translation state counter schema differs.")
                integers[0].update(minimum=1, maximum=2**63 - 1, format="int64")
        return document

    precise._translation_state_exact_counters = True
    application.openapi = precise
