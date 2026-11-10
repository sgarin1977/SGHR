"""Settings-authorized reads of immutable organization template versions."""
import base64
import re
from dataclasses import asdict
from typing import Literal
from uuid import UUID

from fastapi import Depends, Path, Query, Request, Response
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictStr,
    model_validator,
)

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session,
    require_construction_permission,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_organization_template_definitions import (
    OrganizationDefinition,
    ValidatedOrganizationTemplateDefinition,
)
from services.construction.construction_organization_templates import (
    OrganizationTemplateCursor as Cursor,
    OrganizationTemplatePage as Page,
    OrganizationTemplateSummary as Summary,
    OrganizationTemplateVersionState as VersionState,
    OrganizationTemplateSectionReference as SectionReference,
    OrganizationTemplateQuestionReference as QuestionReference,
    ConstructionOrganizationTemplateValidationError as Validation,
    ConstructionOrganizationTemplateNotFoundError as NotFound,
    build_construction_organization_template_service,
)

from api.dependencies import get_api_session
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_content_translation import (
    ContentTranslationUnavailable,
)
from services.construction.construction_template_translation_source import (
    TemplateTranslationSourceValidationError,
    template_label_field,
    TemplateTranslationLanguageError,
    TemplateTranslationLanguageChanged,
)
from services.construction.construction_template_translation_workflow import (
    TemplateTranslatedText,
    TemplateTranslationContextChanged,
    TemplateTranslationCacheConflict,
    build_construction_template_translation_workflow,
)

CURSOR_MAGIC = b"OT\x01"
CURSOR_LENGTH = 68


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationTemplateSummaryData(Closed):
    tenant_id: UUID
    template_id: UUID
    code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    active: StrictBool
    template_row_version: SettingsVersion
    version_id: UUID
    version: int = Field(strict=True, ge=1, le=2**31 - 1)
    version_row_version: SettingsVersion
    source_starter_version_id: UUID | None
    definition_schema_version: int = Field(strict=True, ge=1, le=1)
    source_language: StrictStr = Field(pattern=r"^[A-Za-z0-9_-]{1,10}$")
    title: StrictStr = Field(min_length=1, max_length=120)
    description: StrictStr | None = Field(max_length=120)
    content_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    published_at: AwareDatetime


class OrganizationTemplateQuestionReferenceData(Closed):
    id: UUID
    code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    row_version: SettingsVersion


class OrganizationTemplateSectionReferenceData(Closed):
    id: UUID
    code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    row_version: SettingsVersion
    questions: list[OrganizationTemplateQuestionReferenceData] = Field(
        min_length=1, max_length=2000)


class OrganizationTemplateVersionData(OrganizationTemplateSummaryData):
    definition: OrganizationDefinition
    section_references: list[OrganizationTemplateSectionReferenceData] = Field(
        min_length=1, max_length=100)


class OrganizationTemplatePagination(Closed):
    next_cursor: StrictStr | None = Field(
        min_length=CURSOR_LENGTH, max_length=CURSOR_LENGTH)


class OrganizationTemplateListMeta(Closed):
    pagination: OrganizationTemplatePagination


class OrganizationTemplateListEnvelope(Closed):
    data: list[OrganizationTemplateSummaryData]
    meta: OrganizationTemplateListMeta
    request_id: StrictStr = Field(min_length=1)


class OrganizationTemplateVersionEnvelope(Closed):
    data: OrganizationTemplateVersionData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def encode_cursor(cursor, *, actor):
    if cursor is None:
        return None
    if (
        not isinstance(cursor, Cursor)
        or (cursor.tenant_id, cursor.user_id) != (actor.tenant_id, actor.user_id)
    ):
        raise ValueError("Template continuation context differs.")
    raw = (
        CURSOR_MAGIC + cursor.tenant_id.bytes + cursor.user_id.bytes
        + cursor.after_template_id.bytes
    )
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(value, *, actor):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or re.fullmatch(
            r"[A-Za-z0-9_-]{68}", value
        ) is None:
            raise ValueError()
        raw = base64.b64decode(value, altchars=b"-_", validate=True)
        if (
            len(raw) != 51 or raw[:3] != CURSOR_MAGIC
            or base64.urlsafe_b64encode(raw).decode("ascii") != value
        ):
            raise ValueError()
        result = Cursor(
            UUID(bytes=raw[3:19]), UUID(bytes=raw[19:35]),
            UUID(bytes=raw[35:51]))
        if (result.tenant_id, result.user_id) != (actor.tenant_id, actor.user_id):
            raise ValueError()
        return result
    except Exception:
        raise Validation("Template cursor is invalid.") from None


def public_summary(state, *, actor):
    if not isinstance(state, Summary) or state.tenant_id != actor.tenant_id:
        raise ValueError("Template response context differs.")
    return OrganizationTemplateSummaryData(**{
        name: getattr(state, name)
        for name in OrganizationTemplateSummaryData.model_fields
    })


def public_page(result, *, actor, limit, after):
    if (
        not isinstance(result, Page) or not isinstance(result.items, tuple)
        or len(result.items) > limit
    ):
        raise ValueError("Template page differs.")
    previous = None if after is None else after.after_template_id
    codes = set()
    data = []
    for state in result.items:
        item = public_summary(state, actor=actor)
        if (
            previous is not None and item.template_id <= previous
            or item.code in codes
        ):
            raise ValueError("Template ordering or identity differs.")
        data.append(item.model_dump(mode="json"))
        previous = item.template_id
        codes.add(item.code)
    cursor = result.next_cursor
    if cursor is not None and (
        not isinstance(cursor, Cursor) or not data or len(data) != limit
        or cursor.after_template_id != result.items[-1].template_id
    ):
        raise ValueError("Template continuation differs.")
    return data, {"pagination": {
        "next_cursor": encode_cursor(cursor, actor=actor)}}


def public_version(state, *, actor, version_id):
    if (
        not isinstance(state, VersionState) or state.version_id != version_id
        or not isinstance(state.definition, ValidatedOrganizationTemplateDefinition)
        or state.definition.source_language != state.source_language
        or state.definition.content_hash != state.content_hash
        or not isinstance(state.sections, tuple)
    ):
        raise ValueError("Template version response differs.")
    summary = public_summary(state, actor=actor)
    payload = state.definition.payload()
    definition = OrganizationDefinition.model_validate(payload)
    if definition.model_dump(mode="json") != payload:
        raise ValueError("Template definition normalization differs.")
    labels = payload["labels"][state.source_language]
    description_key = payload["description_key"]
    if (
        labels[payload["title_key"]] != state.title
        or (None if description_key is None else labels[description_key])
            != state.description
        or len(state.sections) != len(payload["sections"])
    ):
        raise ValueError("Template labels or sections differ.")

    sections = []
    section_ids = set()
    question_ids = set()
    for reference, section in zip(state.sections, payload["sections"]):
        if (
            not isinstance(reference, SectionReference)
            or not isinstance(reference.questions, tuple)
            or reference.code != section["code"]
            or reference.id in section_ids
            or len(reference.questions) != len(section["questions"])
        ):
            raise ValueError("Template section references differ.")
        section_ids.add(reference.id)
        questions = []
        for question, body in zip(reference.questions, section["questions"]):
            if (
                not isinstance(question, QuestionReference)
                or question.code != body["code"]
                or question.id in question_ids
            ):
                raise ValueError("Template question references differ.")
            question_ids.add(question.id)
            questions.append(OrganizationTemplateQuestionReferenceData(
                id=question.id, code=question.code, row_version=question.row_version))
        sections.append(OrganizationTemplateSectionReferenceData(
            id=reference.id, code=reference.code,
            row_version=reference.row_version, questions=questions))
    return OrganizationTemplateVersionData(
        **summary.model_dump(), definition=definition,
        section_references=sections)


def operation_error(error):
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(
            status_code=403, code="forbidden",
            message="Organization template access is unavailable.")
    if isinstance(error, NotFound):
        return ApiHttpError(
            status_code=404, code="not_found",
            message="Organization template version is unavailable.")
    if isinstance(error, Validation):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Organization template request is invalid.")
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Organization template read failed.")


def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def get_organization_template_service(
    session=Depends(get_construction_scoped_session),
):
    return build_construction_organization_template_service(session=session)


router = ConstructionAPIRouter(
    prefix="/construction/settings/measurement-templates",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: version unavailable"},
        422: {"description": "validation_error: invalid selector or cursor"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized read failure"},
    },
)
permission = require_construction_permission("construction.settings.manage")


@router.get(
    "", response_model=OrganizationTemplateListEnvelope,
    dependencies=[Depends(permission)],
    description=(
        "Latest visible published version of each organization template. "
        "Includes inactive templates with an explicit active flag. "
        "Metadata is in source_language; cursor is a position, not authority."
    ),
)
async def list_organization_templates(
    request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=CURSOR_LENGTH),
    actor=Depends(get_current_actor),
    service=Depends(get_organization_template_service),
):
    try:
        if (
            set(request.query_params) - {"limit", "cursor"}
            or any(len(request.query_params.getlist(name)) != 1
                   for name in request.query_params)
        ):
            raise Validation("Invalid template query.")
        after = decode_cursor(cursor, actor=actor)
        result = await service.list_templates(actor=actor, limit=limit, after=after)
        data, meta = public_page(result, actor=actor, limit=limit, after=after)
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data, meta=meta, request_id=request.state.request_id)


@router.get(
    "/versions/{version_id}", response_model=OrganizationTemplateVersionEnvelope,
    dependencies=[Depends(permission)],
    description=(
        "Exact visible published organization version, its immutable definition "
        "and local section/question references. Does not follow source updates."
    ),
)
async def read_organization_template_version(
    version_id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_organization_template_service),
):
    try:
        if request.query_params:
            raise Validation("Version reads accept no query selectors.")
        state = await service.get_version(actor=actor, version_id=version_id)
        data = public_version(state, actor=actor, version_id=version_id)
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)



class TemplateTranslatedTextData(Closed):
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
    original_text: StrictStr | None = Field(max_length=1048576)
    display_text: StrictStr | None = Field(max_length=1048576)
    translation_status: Literal["VERIFIED", "AUTO_TRANSLATED"] | None
    origin: Literal["ORIGINAL", "PUBLISHED_LABEL", "CACHE", "GENERATED"]
    cache_row_version: SettingsVersion | None
    cache_written: StrictBool

    @model_validator(mode="after")
    def coherent_text(self):
        absent = self.source_version is None
        if absent:
            if (
                self.field_name != "description"
                or self.source_language is not None
                or self.original_text is not None or self.display_text is not None
                or self.origin != "ORIGINAL"
            ):
                raise ValueError("Absent original text metadata differs.")
        elif self.source_language is None or self.original_text is None or self.display_text is None:
            raise ValueError("Original text metadata is incomplete.")

        if self.origin == "ORIGINAL":
            if (
                self.display_text != self.original_text
                or self.translation_status is not None
                or self.cache_row_version is not None or self.cache_written
                or not absent and self.source_language != self.target_language
                and bool(self.original_text.strip())
            ):
                raise ValueError("Original text projection differs.")
        elif self.origin == "PUBLISHED_LABEL":
            if (
                absent or self.source_language == self.target_language
                or self.translation_status is not None
                or self.cache_row_version is not None or self.cache_written
            ):
                raise ValueError("Published label metadata differs.")
        elif (
            absent or self.source_language == self.target_language
            or self.cache_row_version is None
            or self.translation_status is None
        ):
            raise ValueError("Cached translation metadata differs.")
        elif self.origin == "CACHE" and self.cache_written:
            raise ValueError("A cache read cannot claim a new write.")
        elif self.origin == "GENERATED" and (
            not self.cache_written or self.translation_status != "AUTO_TRANSLATED"
        ):
            raise ValueError("Generated translation metadata differs.")
        return self


class TemplateTranslatedTextEnvelope(Closed):
    data: TemplateTranslatedTextData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def get_template_translation_workflow(
    session=Depends(get_construction_scoped_session),
    platform_session=Depends(get_api_session),
):
    try:
        return build_construction_template_translation_workflow(
            session=session, platform_session=platform_session)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Content translation runtime is unavailable.") from None


def translation_operation_error(error):
    if isinstance(error, (ModuleDisabledError, SuiteDisabledError)):
        return ApiHttpError(
            status_code=403, code="MODULE_DISABLED",
            message="Construction module is unavailable.")
    if isinstance(error, TemplateTranslationLanguageChanged):
        return ApiHttpError(
            status_code=409, code="LANGUAGE_CONTEXT_CHANGED",
            message="Profile language changed. Reload the text.")
    if isinstance(error, TemplateTranslationLanguageError):
        return ApiHttpError(
            status_code=409, code="LANGUAGE_CONFIGURATION_REQUIRED",
            message="An active Platform profile language is required.")
    if isinstance(error, TemplateTranslationContextChanged):
        return ApiHttpError(
            status_code=409, code="SOURCE_CONTEXT_CHANGED",
            message="Source text context changed. Reload the text.")
    if isinstance(error, TemplateTranslationCacheConflict):
        return ApiHttpError(
            status_code=409, code="TRANSLATION_CACHE_CONFLICT",
            message="Translation cache changed. Retry the read.")
    if isinstance(error, ContentTranslationUnavailable):
        return ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Content translation is unavailable.")
    if isinstance(error, TemplateTranslationSourceValidationError):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Template text request is invalid.")
    return operation_error(error)


def public_translated_text(result, *, actor, version_id, field_name):
    if (
        not isinstance(result, TemplateTranslatedText)
        or result.tenant_id != actor.tenant_id
        or result.user_id != actor.user_id
        or result.version_id != version_id or result.field_name != field_name
    ):
        raise ValueError("Translated text response context differs.")
    return TemplateTranslatedTextData.model_validate(asdict(result))


@router.get(
    "/versions/{version_id}/texts/{field_name}",
    response_model=TemplateTranslatedTextEnvelope,
    dependencies=[Depends(permission)],
    responses={
        409: {"description": (
            "LANGUAGE_CONTEXT_CHANGED, LANGUAGE_CONFIGURATION_REQUIRED, "
            "SOURCE_CONTEXT_CHANGED or TRANSLATION_CACHE_CONFLICT")},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
    },
    description=(
        "Read title or description of an exact published template in the fresh "
        "Platform profile language, preserving original text and source metadata. "
        "No language, tenant, user or source-text selectors are accepted. "
        "Uses canonical published labels or exact version-bound cache, and may "
        "create an automatic cache entry through the existing Platform provider. "
        "No Idempotency-Key is required. The published definition is unchanged."
    ),
)
async def read_organization_template_text(
    version_id: UUID, field_name: Literal["title", "description"],
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    workflow=Depends(get_template_translation_workflow),
):
    try:
        if request.query_params or await request.body():
            raise TemplateTranslationSourceValidationError(
                "Template text reads accept no query selectors or body.")
        result = await workflow.read_for_actor(
            actor=actor, version_id=version_id, field_name=field_name)
        data = public_translated_text(
            result, actor=actor, version_id=version_id, field_name=field_name)
    except Exception as error:
        raise translation_operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)



class TemplateTranslatedLabelData(TemplateTranslatedTextData):
    field_name: StrictStr = Field(
        pattern=r"^label[.][0-9a-f]{64}$", min_length=70, max_length=70)
    label_key: StrictStr = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$",
        min_length=1, max_length=128)


class TemplateTranslatedLabelEnvelope(Closed):
    data: TemplateTranslatedLabelData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def public_translated_label(result, *, actor, version_id, label_key):
    expected_field = template_label_field(label_key)
    if (
        not isinstance(result, TemplateTranslatedText)
        or result.tenant_id != actor.tenant_id
        or result.user_id != actor.user_id
        or result.version_id != version_id
        or result.field_name != expected_field
    ):
        raise ValueError("Translated label response context differs.")
    return TemplateTranslatedLabelData.model_validate(
        asdict(result) | {"label_key": label_key})


@router.get(
    "/versions/{version_id}/labels/{label_key}",
    response_model=TemplateTranslatedLabelEnvelope,
    dependencies=[Depends(permission)],
    responses={
        409: {"description": (
            "LANGUAGE_CONTEXT_CHANGED, LANGUAGE_CONFIGURATION_REQUIRED, "
            "SOURCE_CONTEXT_CHANGED or TRANSLATION_CACHE_CONFLICT")},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
    },
    description=(
        "Read an exact label from an immutable published template version in the "
        "fresh Platform profile language. Covers section/question labels, option "
        "labels and help text referenced by the shared definition. Keys are exact "
        "and case-sensitive. Only the canonical label text is sent for translation. "
        "Formulae, units, option values, show_if, schemas and media references "
        "remain unchanged. No caller language, tenant, source text or replay key."
    ),
)
async def read_organization_template_label(
    version_id: UUID, request: Request, response: Response,
    label_key: StrictStr = Path(
        ..., min_length=1, max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"),
    actor=Depends(get_current_actor),
    workflow=Depends(get_template_translation_workflow),
):
    try:
        if request.query_params or await request.body():
            raise TemplateTranslationSourceValidationError(
                "Template label reads accept no query selectors or body.")
        result = await workflow.read_for_actor(
            actor=actor, version_id=version_id,
            field_name=template_label_field(label_key))
        data = public_translated_label(
            result, actor=actor, version_id=version_id, label_key=label_key)
    except Exception as error:
        raise translation_operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


def install_organization_template_openapi_precision(application):
    """Preserve exact BIGINT bounds through FastAPI's OpenAPI serialization."""
    previous = application.openapi
    if getattr(previous, "_organization_template_exact_counters", False):
        return
    targets = {
        "OrganizationTemplateSummaryData": (
            "template_row_version", "version_row_version"),
        "OrganizationTemplateVersionData": (
            "template_row_version", "version_row_version"),
        "OrganizationTemplateSectionReferenceData": ("row_version",),
        "OrganizationTemplateQuestionReferenceData": ("row_version",),
        "TemplateTranslatedTextData": ("source_version", "cache_row_version"),
        "TemplateTranslatedLabelData": ("source_version", "cache_row_version"),
    }

    def exact_openapi():
        document = previous()
        for schema in document.get("components", {}).get("schemas", {}).values():
            for name in targets.get(schema.get("title"), ()):
                field = schema["properties"][name]
                branches = [field] if field.get("type") == "integer" else field.get("anyOf", [])
                integers = [branch for branch in branches if branch.get("type") == "integer"]
                if len(integers) != 1:
                    raise ValueError("Template counter schema differs.")
                integers[0].update(minimum=1, maximum=2**63 - 1, format="int64")
        return document

    exact_openapi._organization_template_exact_counters = True
    application.openapi = exact_openapi


# CON_020B_TEMPLATE_TRANSLATION_BATCH_HTTP_V1
from typing import Annotated
from services.construction.construction_template_translation_batch import (
    ConstructionTemplateTranslationBatchWorkflow,
    MAX_BATCH_FIELDS,
)

TemplateTranslationBatchLabelKey = Annotated[
    StrictStr,
    Field(
        min_length=1, max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"),
]


class TemplateTranslatedBatchEnvelope(Closed):
    data: list[TemplateTranslatedLabelData | TemplateTranslatedTextData] = Field(
        min_length=1, max_length=MAX_BATCH_FIELDS)
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def get_template_translation_batch_workflow(
    workflow=Depends(get_template_translation_workflow),
):
    try:
        return ConstructionTemplateTranslationBatchWorkflow(workflow=workflow)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Template translation runtime is unavailable.") from None


def template_translation_batch_fields(fields, label_keys):
    roots = tuple(fields or ())
    keys = tuple(label_keys or ())
    if (
        any(value not in ("title", "description") for value in roots)
        or not 1 <= len(roots) + len(keys) <= MAX_BATCH_FIELDS
    ):
        raise TemplateTranslationSourceValidationError(
            "A bounded selection of canonical text fields is required.")
    requests = (
        tuple((field, None) for field in roots)
        + tuple((template_label_field(key), key) for key in keys)
    )
    if len({field for field, _ in requests}) != len(requests):
        raise TemplateTranslationSourceValidationError(
            "Duplicate text fields are not allowed.")
    return requests


def public_translated_batch(results, *, actor, version_id, requests):
    if not isinstance(results, tuple) or len(results) != len(requests):
        raise ValueError("Translated batch size differs.")
    data = []
    for result, (field, key) in zip(results, requests):
        if key is None:
            item = public_translated_text(
                result, actor=actor, version_id=version_id, field_name=field)
        else:
            item = public_translated_label(
                result, actor=actor, version_id=version_id, label_key=key)
        data.append(item)

    first = data[0]
    if any(
        item.template_id != first.template_id
        or item.target_language != first.target_language
        for item in data
    ):
        raise ValueError("Translated batch context differs.")
    source_versions = {
        (item.source_language, item.source_version)
        for item in data if item.source_version is not None
    }
    if len(source_versions) > 1:
        raise ValueError("Translated batch source versions differ.")
    return data


async def template_translation_batch_response(
    *, version_id, fields, label_keys, request, response,
    actor, workflow, error_mapper,
):
    try:
        if (
            set(request.query_params) - {"fields", "label_keys"}
            or await request.body()
        ):
            raise TemplateTranslationSourceValidationError(
                "Batch reads accept only canonical field and label selectors.")
        requests = template_translation_batch_fields(fields, label_keys)
        results = await workflow.read_for_actor(
            actor=actor, version_id=version_id,
            field_names=tuple(field for field, _ in requests))
        data = public_translated_batch(
            results, actor=actor, version_id=version_id, requests=requests)
    except Exception as error:
        raise error_mapper(error) from None

    no_store(response)
    return success_envelope(
        data=[item.model_dump(mode="json") for item in data],
        request_id=request.state.request_id)


@router.get(
    "/versions/{version_id}/translated-texts",
    response_model=TemplateTranslatedBatchEnvelope,
    dependencies=[Depends(permission)],
    responses={
        409: {"description": (
            "LANGUAGE_CONTEXT_CHANGED, LANGUAGE_CONFIGURATION_REQUIRED, "
            "SOURCE_CONTEXT_CHANGED or TRANSLATION_CACHE_CONFLICT")},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
    },
    description=(
        "Read up to 32 distinct texts from one exact published template version. "
        "Repeat fields for title/description and label_keys for exact canonical "
        "section/question/option/help keys. Results preserve fields order first, "
        "then label_keys order. Language comes from the fresh Platform profile. "
        "No caller tenant, language, source text or body. One transaction covers "
        "all derived cache writes. No Idempotency-Key is required. "
        "The published definition and machine values remain unchanged."
    ),
)
async def read_organization_template_translated_batch(
    version_id: UUID, request: Request, response: Response,
    fields: list[Literal["title", "description"]] | None = Query(
        default=None, max_length=2),
    label_keys: list[TemplateTranslationBatchLabelKey] | None = Query(
        default=None, max_length=MAX_BATCH_FIELDS),
    actor=Depends(get_current_actor),
    workflow=Depends(get_template_translation_batch_workflow),
):
    return await template_translation_batch_response(
        version_id=version_id, fields=fields, label_keys=label_keys,
        request=request, response=response, actor=actor, workflow=workflow,
        error_mapper=translation_operation_error)
