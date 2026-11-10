"""CON-O002 read-only starter catalog over the shared Construction API."""
import base64
import re
from uuid import UUID

from fastapi import Depends, Path, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session,
    require_construction_permission,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_starter_templates import (
    ConstructionStarterTemplateCursor as Cursor,
    ConstructionStarterTemplatePage as Page,
    ConstructionStarterTemplateVersionState as VersionState,
    ConstructionStarterTemplateValidationError as Validation,
    ConstructionStarterTemplateNotFoundError as NotFound,
    build_construction_starter_template_service,
)

from typing import Literal
from api.dependencies import get_api_session
from api.routes.construction_organization_templates import (
    TemplateTranslatedTextEnvelope,
    TemplateTranslatedLabelEnvelope,
    public_translated_text, public_translated_label,
    translation_operation_error,
)
from services.construction.construction_template_translation_source import (
    TemplateTranslationSourceValidationError, template_label_field,
)
from services.construction.construction_starter_translation import (
    build_construction_starter_translation_workflow,
)

CURSOR_LENGTH = 68
CURSOR_MAGIC = b"ST\x01"


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StarterTemplateSummary(Closed):
    template_id: UUID
    version_id: UUID
    code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    version: int = Field(strict=True, ge=1, le=2**31 - 1)
    definition_schema_version: int = Field(strict=True, ge=1, le=1)
    source_language: StrictStr = Field(pattern=r"^[A-Za-z0-9_-]{1,10}$")
    content_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: AwareDatetime
    created_at: AwareDatetime
    title: StrictStr = Field(min_length=1, max_length=120)
    description: StrictStr | None = Field(default=None, max_length=120)


class StarterTemplateVersionData(StarterTemplateSummary):
    definition: dict


class StarterTemplatePagination(Closed):
    next_cursor: StrictStr | None = Field(
        min_length=CURSOR_LENGTH, max_length=CURSOR_LENGTH)


class StarterTemplateListMeta(Closed):
    pagination: StarterTemplatePagination


class StarterTemplateListEnvelope(Closed):
    data: list[StarterTemplateSummary]
    meta: StarterTemplateListMeta
    request_id: StrictStr = Field(min_length=1)


class StarterTemplateVersionEnvelope(Closed):
    data: StarterTemplateVersionData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def encode_cursor(cursor, *, actor):
    if cursor is None:
        return None
    if (
        not isinstance(cursor, Cursor)
        or not all(isinstance(value, UUID) for value in (
            cursor.tenant_id, cursor.user_id, cursor.after_template_id))
        or (cursor.tenant_id, cursor.user_id) != (actor.tenant_id, actor.user_id)
    ):
        raise ValueError("Catalog continuation context differs.")
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
        cursor = Cursor(
            UUID(bytes=raw[3:19]), UUID(bytes=raw[19:35]),
            UUID(bytes=raw[35:51]))
        if (cursor.tenant_id, cursor.user_id) != (actor.tenant_id, actor.user_id):
            raise ValueError()
        return cursor
    except Exception:
        raise Validation("Catalog cursor is invalid.") from None


def public_summary(state):
    if (
        not isinstance(state, VersionState)
        or state.definition.source_language != state.source_language
        or state.definition.content_hash != state.content_hash
    ):
        raise ValueError("Catalog version response differs.")
    payload = state.definition.payload()
    labels = payload["labels"][state.source_language]
    description_key = payload["description_key"]
    return StarterTemplateSummary(
        template_id=state.template_id, version_id=state.version_id,
        code=state.code, version=state.version,
        definition_schema_version=state.definition_schema_version,
        source_language=state.source_language, content_hash=state.content_hash,
        published_at=state.published_at, created_at=state.created_at,
        title=labels[payload["title_key"]],
        description=None if description_key is None else labels[description_key])


def public_page(result, *, actor, limit, after):
    if (
        not isinstance(result, Page) or not isinstance(result.items, tuple)
        or len(result.items) > limit
    ):
        raise ValueError("Catalog page response differs.")
    data = []
    previous = None if after is None else after.after_template_id
    codes = set()
    for state in result.items:
        item = public_summary(state)
        if (
            previous is not None and item.template_id <= previous
            or item.code in codes
        ):
            raise ValueError("Catalog page order or identity differs.")
        data.append(item.model_dump(mode="json"))
        previous = item.template_id
        codes.add(item.code)
    cursor = result.next_cursor
    if cursor is not None and (
        not isinstance(cursor, Cursor) or len(data) != limit or not data
        or cursor.after_template_id != result.items[-1].template_id
    ):
        raise ValueError("Catalog continuation differs.")
    return data, {"pagination": {
        "next_cursor": encode_cursor(cursor, actor=actor)}}


def operation_error(error):
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(
            status_code=403, code="forbidden",
            message="Starter catalog access is unavailable.")
    if isinstance(error, NotFound):
        return ApiHttpError(
            status_code=404, code="not_found",
            message="Starter template version is unavailable.")
    if isinstance(error, Validation):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Starter catalog request is invalid.")
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Starter catalog read failed.")


def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def get_starter_template_service(session=Depends(get_construction_scoped_session)):
    return build_construction_starter_template_service(session=session)


router = ConstructionAPIRouter(
    prefix="/construction/settings/starter-measurement-templates",
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
    "", response_model=StarterTemplateListEnvelope,
    dependencies=[Depends(permission)],
    description="Latest visible published versions; metadata uses source_language.",
)
async def list_starter_templates(
    request: Request, response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=CURSOR_LENGTH),
    actor=Depends(get_current_actor),
    service=Depends(get_starter_template_service),
):
    try:
        if (
            set(request.query_params) - {"limit", "cursor"}
            or any(len(request.query_params.getlist(name)) != 1
                   for name in request.query_params)
        ):
            raise Validation("Invalid catalog query.")
        after = decode_cursor(cursor, actor=actor)
        result = await service.list_latest(actor=actor, limit=limit, after=after)
        data, meta = public_page(
            result, actor=actor, limit=limit, after=after)
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data, meta=meta, request_id=request.state.request_id)


@router.get(
    "/versions/{version_id}", response_model=StarterTemplateVersionEnvelope,
    dependencies=[Depends(permission)],
    description="Exact visible published version, including its validated definition.",
)
async def read_starter_template_version(
    version_id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_starter_template_service),
):
    try:
        if request.query_params:
            raise Validation("Version reads accept no query selectors.")
        state = await service.get_version(actor=actor, version_id=version_id)
        summary = public_summary(state)
        if summary.version_id != version_id:
            raise ValueError("Requested version differs.")
        data = StarterTemplateVersionData(
            **summary.model_dump(), definition=state.definition.payload())
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


# CON_020B_STARTER_TRANSLATION_HTTP_V1
def get_starter_translation_workflow(
    session=Depends(get_construction_scoped_session),
    platform_session=Depends(get_api_session),
):
    try:
        return build_construction_starter_translation_workflow(
            session=session, platform_session=platform_session)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Starter translation runtime is unavailable.") from None


def starter_translation_error(error):
    if isinstance(error, (NotFound, Validation)):
        return operation_error(error)
    return translation_operation_error(error)


async def starter_translated_response(
    *, version_id, field_name, label_key, request, response, actor, workflow,
):
    try:
        if request.query_params or await request.body():
            raise TemplateTranslationSourceValidationError(
                "Starter text reads accept no query selectors or body.")
        result = await workflow.read_for_actor(
            actor=actor, version_id=version_id, field_name=field_name)
        if label_key is None:
            data = public_translated_text(
                result, actor=actor, version_id=version_id, field_name=field_name)
        else:
            data = public_translated_label(
                result, actor=actor, version_id=version_id, label_key=label_key)
    except Exception as error:
        raise starter_translation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.get(
    "/versions/{version_id}/texts/{field_name}",
    response_model=TemplateTranslatedTextEnvelope,
    dependencies=[Depends(permission)],
    responses={
        409: {"description": "Language, source context or translation cache conflict"},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
    },
    description=(
        "Read title/description of an exact visible published starter version in "
        "the fresh Platform profile language. The catalog and organization copies "
        "are unchanged. Derived cache belongs to the current tenant. No caller "
        "language or source text and no Idempotency-Key are accepted/required."
    ),
)
async def read_starter_template_text(
    version_id: UUID, field_name: Literal["title", "description"],
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    workflow=Depends(get_starter_translation_workflow),
):
    return await starter_translated_response(
        version_id=version_id, field_name=field_name, label_key=None,
        request=request, response=response, actor=actor, workflow=workflow)


@router.get(
    "/versions/{version_id}/labels/{label_key}",
    response_model=TemplateTranslatedLabelEnvelope,
    dependencies=[Depends(permission)],
    responses={
        409: {"description": "Language, source context or translation cache conflict"},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
    },
    description=(
        "Read an exact section/question/option/help label of a visible published "
        "starter version. Keys are case-sensitive. Only canonical label text is "
        "translated; machine data and immutable starter content remain unchanged. "
        "Reading does not copy a template into the organization."
    ),
)
async def read_starter_template_label(
    version_id: UUID, request: Request, response: Response,
    label_key: StrictStr = Path(
        ..., min_length=1, max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"),
    actor=Depends(get_current_actor),
    workflow=Depends(get_starter_translation_workflow),
):
    try:
        field_name = template_label_field(label_key)
    except Exception as error:
        raise starter_translation_error(error) from None
    return await starter_translated_response(
        version_id=version_id, field_name=field_name, label_key=label_key,
        request=request, response=response, actor=actor, workflow=workflow)


# CON_020B_STARTER_TRANSLATION_BATCH_HTTP_V1
from api.routes.construction_organization_templates import (
    TemplateTranslationBatchLabelKey,
    TemplateTranslatedBatchEnvelope,
    template_translation_batch_response,
)
from services.construction.construction_template_translation_batch import (
    ConstructionTemplateTranslationBatchWorkflow,
    MAX_BATCH_FIELDS,
)


def get_starter_translation_batch_workflow(
    workflow=Depends(get_starter_translation_workflow),
):
    try:
        return ConstructionTemplateTranslationBatchWorkflow(workflow=workflow)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="TRANSLATION_UNAVAILABLE",
            message="Starter translation runtime is unavailable.") from None


@router.get(
    "/versions/{version_id}/translated-texts",
    response_model=TemplateTranslatedBatchEnvelope,
    dependencies=[Depends(permission)],
    responses={
        409: {"description": (
            "Language, source context or translation cache conflict")},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
    },
    description=(
        "Read up to 32 distinct texts from one exact visible published starter "
        "version. Repeat fields for title/description and label_keys for exact "
        "canonical labels. Results preserve fields order first, then label_keys "
        "order. Target language comes from the fresh Platform profile. "
        "The global source catalog is read-only; derived cache is tenant-scoped. "
        "No organization copy, onboarding completion or business mutation. "
        "No caller tenant, language, source text, body or required replay key."
    ),
)
async def read_starter_template_translated_batch(
    version_id: UUID, request: Request, response: Response,
    fields: list[Literal["title", "description"]] | None = Query(
        default=None, max_length=2),
    label_keys: list[TemplateTranslationBatchLabelKey] | None = Query(
        default=None, max_length=MAX_BATCH_FIELDS),
    actor=Depends(get_current_actor),
    workflow=Depends(get_starter_translation_batch_workflow),
):
    return await template_translation_batch_response(
        version_id=version_id, fields=fields, label_keys=label_keys,
        request=request, response=response, actor=actor, workflow=workflow,
        error_mapper=starter_translation_error)
