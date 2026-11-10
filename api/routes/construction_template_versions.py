"""Draft creation/readiness/publication through the shared Construction API."""
from dataclasses import asdict
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field,
    StrictBool, StrictInt, StrictStr,
)

from api.auth import get_current_actor, require_module, require_suite
from api.construction_settings_contract import SettingsVersion
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session,
    require_construction_permission,
)
from api.routes.construction_organization_templates import (
    OrganizationTemplateSummaryData,
    OrganizationTemplateSectionReferenceData,
)
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingStateConflictError as Conflict,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateNotFoundError,
)
from services.construction.construction_organization_template_definitions import (
    OrganizationDefinition, ValidatedOrganizationTemplateDefinition,
    validate_organization_template_definition,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftNotFound,
)
from services.construction.construction_template_draft_content import (
    ConstructionTemplateDraftState,
    build_construction_template_draft_content_service,
)
from services.construction.construction_template_draft_workflow import (
    build_construction_template_draft_workflow,
    encode_result as encode_draft, decode_result as decode_draft,
)
from services.construction.construction_template_draft_header_workflow import (
    build_construction_template_draft_header_workflow,
    encode_result as encode_header, decode_result as decode_header,
)
from services.construction.construction_template_draft_item_workflow import (
    build_construction_template_draft_item_workflow,
    normalize_item_request,
    encode_result as encode_item, decode_result as decode_item,
)
from services.construction.construction_template_draft_items import UNSET
from services.construction.construction_organization_templates import (
    SECTION_FIELDS, QUESTION_FIELDS,
)
from services.construction.construction_organization_template_definitions import (
    OrganizationSection, OrganizationQuestion,
)
from services.construction.construction_template_draft_append import (
    UNSET as APPEND_UNSET,
)
from services.construction.construction_template_draft_append_workflow import (
    build_construction_template_draft_append_workflow,
    normalize_append_request,
    encode_result as encode_append, decode_result as decode_append,
)
from services.construction.construction_template_keyed_workflow import (
    ConstructionTemplateOperationInProgressError,
)
from services.construction.construction_template_publication import (
    ConstructionTemplatePublicationReadiness,
    ConstructionTemplateBindingUnavailable,
    build_construction_template_publication_service,
)
from services.construction.construction_template_publication_workflow import (
    ConstructionTemplatePublicationInProgressError,
    build_construction_template_publication_workflow,
    encode_result as encode_publication, decode_result as decode_publication,
)


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplateDraftCreateRequest(Closed):
    base_version_id: UUID
    expected_template_row_version: SettingsVersion


class TemplateDraftVersionRequest(Closed):
    expected_row_version: SettingsVersion


class TemplateDraftCreatedData(Closed):
    tenant_id: UUID
    template_id: UUID
    template_row_version: SettingsVersion
    base_version_id: UUID
    version_id: UUID
    version: StrictInt = Field(ge=2, le=2**31 - 1)
    version_row_version: Literal[1]
    status: Literal["DRAFT"]
    source_starter_version_id: UUID | None
    source_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    section_count: StrictInt = Field(ge=1, le=100)
    question_count: StrictInt = Field(ge=1, le=2000)
    created_by: UUID
    created_at: AwareDatetime


class TemplateDraftData(Closed):
    tenant_id: UUID
    template_id: UUID
    code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    active: StrictBool
    template_row_version: SettingsVersion
    version_id: UUID
    version: StrictInt = Field(ge=1, le=2**31 - 1)
    version_row_version: SettingsVersion
    status: Literal["DRAFT"]
    source_starter_version_id: UUID | None
    definition_schema_version: Literal[1]
    source_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    title: StrictStr = Field(min_length=1, max_length=120)
    description: StrictStr | None = Field(max_length=120)
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    definition: OrganizationDefinition
    section_references: list[OrganizationTemplateSectionReferenceData] = Field(
        min_length=1, max_length=100)


class TemplatePublicationReadinessData(Closed):
    tenant_id: UUID
    template_id: UUID
    version_id: UUID
    version: StrictInt = Field(ge=1, le=2**31 - 1)
    version_row_version: SettingsVersion
    template_row_version: SettingsVersion
    active: StrictBool
    definition_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    section_count: StrictInt = Field(ge=1, le=100)
    question_count: StrictInt = Field(ge=1, le=2000)
    binding_count: StrictInt = Field(ge=0, le=2000)


class TemplatePublicationData(OrganizationTemplateSummaryData):
    section_count: StrictInt = Field(ge=1, le=100)
    question_count: StrictInt = Field(ge=1, le=2000)


class TemplateDraftHeaderPatchRequest(Closed):
    expected_row_version: SettingsVersion
    source_language: StrictStr = Field(
        default_factory=str,
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    title_key: StrictStr = Field(
        default_factory=str,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$", max_length=128)
    description_key: StrictStr | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$", max_length=128)
    labels: dict[StrictStr, dict[StrictStr, StrictStr]] = Field(
        default_factory=dict, max_length=64,
        description="Full labels dictionary when supplied; omission preserves saved labels.")

    def patch_fields(self):
        return self.model_dump(
            include=self.model_fields_set - {"expected_row_version"})


class TemplateDraftWriteData(Closed):
    tenant_id: UUID
    template_id: UUID
    code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    active: StrictBool
    template_row_version: SettingsVersion
    version_id: UUID
    version: StrictInt = Field(ge=1, le=2**31 - 1)
    version_row_version: SettingsVersion
    status: Literal["DRAFT"]
    source_starter_version_id: UUID | None
    definition_schema_version: Literal[1]
    source_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$", max_length=10)
    title: StrictStr = Field(min_length=1, max_length=120)
    description: StrictStr | None = Field(max_length=120)
    definition_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    section_count: StrictInt = Field(ge=1, le=100)
    question_count: StrictInt = Field(ge=1, le=2000)
    changed: StrictBool


T = TypeVar("T")


class TemplateVersionEnvelope(Closed, Generic[T]):
    data: T
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def no_query(request):
    if request.query_params:
        raise Validation("Template version operations accept no query selectors.")


def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def operation_error(error):
    if isinstance(error, ApiIdempotencyKeyReusedError):
        return ApiHttpError(
            status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with a different request.")
    if isinstance(error, (
        ConstructionTemplateOperationInProgressError,
        ConstructionTemplatePublicationInProgressError,
    )):
        return ApiHttpError(
            status_code=409, code="idempotency_in_progress",
            message="Template operation is still processing.")
    if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        return ApiHttpError(
            status_code=403, code="forbidden",
            message="Template version access is unavailable.")
    if isinstance(error, ConstructionRowVersionConflictError):
        return ApiHttpError(
            status_code=409, code="ROW_VERSION_CONFLICT",
            message="Template version has changed.")
    if isinstance(error, (
        ConstructionTemplateDraftNotFound,
        ConstructionOrganizationTemplateNotFoundError,
    )):
        return ApiHttpError(
            status_code=404, code="not_found",
            message="Template version is unavailable.")
    if isinstance(error, ConstructionTemplateBindingUnavailable):
        return ApiHttpError(
            status_code=409, code="TEMPLATE_BINDING_UNAVAILABLE",
            message="Template work-type validation is unavailable.")
    if isinstance(error, Validation):
        return ApiHttpError(
            status_code=422, code="validation_error",
            message="Template version request is invalid.")
    if isinstance(error, Conflict):
        return ApiHttpError(
            status_code=409, code="TEMPLATE_VERSION_CONFLICT",
            message="Template operation conflicts with the current state.")
    return ApiHttpError(
        status_code=500, code="internal_error",
        message="Template version operation failed.")


def require_template_version_key(
    request: Request,
    value: Annotated[str, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description="Required for version mutations; replays the original successful receipt.",
    )],
):
    if (
        request.query_params
        or len(request.headers.getlist("Idempotency-Key")) != 1
        or not value.strip()
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Template version request is invalid.")
    return value


def get_template_version_settings(_key=Depends(require_template_version_key)):
    try:
        return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Template replay is not configured.") from None


def get_draft_workflow(
    settings=Depends(get_template_version_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_template_draft_workflow(
            session=session, idempotency_encryption_key=settings.encryption_key)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Template draft runtime is unavailable.") from None


def get_publication_workflow(
    settings=Depends(get_template_version_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_template_publication_workflow(
            session=session, idempotency_encryption_key=settings.encryption_key)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Template publication runtime is unavailable.") from None


def get_draft_header_workflow(
    settings=Depends(get_template_version_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_template_draft_header_workflow(
            session=session, idempotency_encryption_key=settings.encryption_key)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Template header runtime is unavailable.") from None


def get_draft_content_service(session=Depends(get_construction_scoped_session)):
    return build_construction_template_draft_content_service(session=session)


def get_publication_service(session=Depends(get_construction_scoped_session)):
    return build_construction_template_publication_service(session=session)


def public_draft(state, *, actor, template_id, version_id):
    if (
        not isinstance(state, ConstructionTemplateDraftState)
        or state.tenant_id != actor.tenant_id
        or state.template_id != template_id or state.version_id != version_id
        or not isinstance(state.definition, ValidatedOrganizationTemplateDefinition)
        or not isinstance(state.sections, tuple)
    ):
        raise ValueError("Draft response context differs.")
    checked = validate_organization_template_definition(
        state.definition.canonical_json, source_language=state.source_language)
    if checked != state.definition:
        raise ValueError("Draft definition differs.")
    payload = checked.payload()
    references = [
        OrganizationTemplateSectionReferenceData.model_validate(asdict(reference))
        for reference in state.sections
    ]
    if len(references) != len(payload["sections"]):
        raise ValueError("Draft section references differ.")
    section_ids, question_ids = set(), set()
    for reference, section in zip(references, payload["sections"]):
        if (
            reference.id in section_ids or reference.code != section["code"]
            or len(reference.questions) != len(section["questions"])
        ):
            raise ValueError("Draft section references differ.")
        section_ids.add(reference.id)
        for question, body in zip(reference.questions, section["questions"]):
            if question.id in question_ids or question.code != body["code"]:
                raise ValueError("Draft question references differ.")
            question_ids.add(question.id)
    values = {
        name: getattr(state, name) for name in TemplateDraftData.model_fields
        if name not in ("definition", "section_references")
    }
    return TemplateDraftData(
        **values, definition=payload, section_references=references)


def public_readiness(state, *, actor, template_id, version_id, expected):
    if (
        not isinstance(state, ConstructionTemplatePublicationReadiness)
        or state.tenant_id != actor.tenant_id
        or state.template_id != template_id or state.version_id != version_id
        or state.version_row_version != expected
        or state.binding_count > state.question_count
        or state.section_count > state.question_count
    ):
        raise ValueError("Readiness response context differs.")
    return TemplatePublicationReadinessData.model_validate(asdict(state))


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
        409: {"description": (
            "ROW_VERSION_CONFLICT, IDEMPOTENCY_KEY_REUSED, idempotency_in_progress, "
            "TEMPLATE_BINDING_UNAVAILABLE or TEMPLATE_VERSION_CONFLICT")},
        422: {"description": "validation_error"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized failure"},
        503: {"description": "service_unavailable: replay configuration unavailable"},
    },
)
permission = require_construction_permission("construction.settings.manage")


@router.post(
    "/{template_id}/drafts", status_code=201,
    response_model=TemplateVersionEnvelope[TemplateDraftCreatedData],
    dependencies=[Depends(permission)],
    description=(
        "Create a draft from an exact published organization version. "
        "Requires Idempotency-Key. Replay returns the original creation receipt, "
        "including its original DRAFT status and counters."
    ),
)
async def create_template_draft(
    template_id: UUID, body: TemplateDraftCreateRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_template_version_key),
    workflow=Depends(get_draft_workflow),
):
    try:
        no_query(request)
        result = await workflow.create_draft(
            actor=actor, template_id=template_id,
            base_version_id=body.base_version_id,
            expected_template_row_version=body.expected_template_row_version,
            idempotency_key=key, trace_id=request.state.request_id)
        checked = decode_draft(
            encode_draft(result), actor=actor, request={
                "template_id": str(template_id),
                "base_version_id": str(body.base_version_id),
                "expected_template_row_version": body.expected_template_row_version,
            })
        data = TemplateDraftCreatedData.model_validate(asdict(checked))
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.get(
    "/{template_id}/drafts/{version_id}",
    response_model=TemplateVersionEnvelope[TemplateDraftData],
    dependencies=[Depends(permission)],
    description="Read exact current draft content and local references; no replay key required.",
)
async def read_template_draft(
    template_id: UUID, version_id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_draft_content_service),
):
    try:
        no_query(request)
        state = await service.read_draft(
            actor=actor, template_id=template_id, version_id=version_id)
        data = public_draft(
            state, actor=actor, template_id=template_id, version_id=version_id)
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.post(
    "/{template_id}/drafts/{version_id}/validate",
    response_model=TemplateVersionEnvelope[TemplatePublicationReadinessData],
    dependencies=[Depends(permission)],
    description=(
        "Read-only validation of the expected draft version. No replay key required. "
        "This snapshot does not authorize later publication; publication validates again."
    ),
)
async def validate_template_draft(
    template_id: UUID, version_id: UUID, body: TemplateDraftVersionRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_publication_service),
):
    try:
        no_query(request)
        state = await service.check_draft(
            actor=actor, template_id=template_id, version_id=version_id,
            expected_row_version=body.expected_row_version)
        data = public_readiness(
            state, actor=actor, template_id=template_id,
            version_id=version_id, expected=body.expected_row_version)
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.post(
    "/{template_id}/drafts/{version_id}/publish",
    response_model=TemplateVersionEnvelope[TemplatePublicationData],
    dependencies=[Depends(permission)],
    description=(
        "Validate and publish the expected draft version. Requires Idempotency-Key. "
        "Publication, audit and encrypted receipt share one commit."
    ),
)
async def publish_template_draft(
    template_id: UUID, version_id: UUID, body: TemplateDraftVersionRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_template_version_key),
    workflow=Depends(get_publication_workflow),
):
    try:
        no_query(request)
        result = await workflow.publish_draft(
            actor=actor, template_id=template_id, version_id=version_id,
            expected_row_version=body.expected_row_version,
            idempotency_key=key, trace_id=request.state.request_id)
        checked = decode_publication(
            encode_publication(result), actor=actor, request={
                "template_id": str(template_id), "version_id": str(version_id),
                "expected_row_version": body.expected_row_version,
            })
        data = TemplatePublicationData.model_validate(asdict(checked))
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.patch(
    "/{template_id}/drafts/{version_id}",
    response_model=TemplateVersionEnvelope[TemplateDraftWriteData],
    dependencies=[Depends(permission)],
    description=(
        "Sparse versioned draft-header edit. Requires Idempotency-Key. "
        "Omitted fields remain unchanged. A current-version no-op creates no audit "
        "or version increment. Replay returns the original write metadata."
    ),
)
async def patch_template_draft_header(
    template_id: UUID, version_id: UUID, body: TemplateDraftHeaderPatchRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_template_version_key),
    workflow=Depends(get_draft_header_workflow),
):
    try:
        no_query(request)
        fields = body.patch_fields()
        result = await workflow.edit_header(
            actor=actor, template_id=template_id, version_id=version_id,
            expected_row_version=body.expected_row_version,
            fields=fields, idempotency_key=key,
            trace_id=request.state.request_id)
        checked = decode_header(
            encode_header(result), actor=actor, request={
                "template_id": str(template_id),
                "version_id": str(version_id),
                "expected_row_version": body.expected_row_version,
                "fields": fields,
            })
        data = TemplateDraftWriteData.model_validate(asdict(checked))
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)



class TemplateDraftItemPatchRequest(Closed):
    expected_row_version: SettingsVersion
    labels: dict[StrictStr, dict[StrictStr, StrictStr]] = Field(
        default_factory=dict, max_length=64,
        description=(
            "Full replacement labels dictionary when supplied. "
            "Omission preserves saved labels; null is invalid."
        ),
    )

    def normalized_values(self, kind):
        optional = (
            {"labels": self.labels}
            if "labels" in self.model_fields_set else {}
        )
        fields, labels = normalize_item_request(
            kind=kind, fields=self.fields, **optional)
        values = {"fields": fields}
        if labels is not UNSET:
            values["labels"] = labels
        return values


class TemplateDraftSectionPatchRequest(TemplateDraftItemPatchRequest):
    fields: dict[StrictStr, Any] = Field(
        default_factory=dict, max_length=len(SECTION_FIELDS),
        json_schema_extra={"propertyNames": {"enum": list(SECTION_FIELDS)}},
        description=(
            "Sparse fields from the shared section contract. "
            "IDs, parent references, questions and counters cannot be changed. "
            "The complete resulting draft is validated before saving."
        ),
    )


class TemplateDraftQuestionPatchRequest(TemplateDraftItemPatchRequest):
    fields: dict[StrictStr, Any] = Field(
        default_factory=dict, max_length=len(QUESTION_FIELDS),
        json_schema_extra={"propertyNames": {"enum": list(QUESTION_FIELDS)}},
        description=(
            "Sparse fields from the shared question contract. "
            "Omitted values are preserved; explicit null clears only nullable fields. "
            "Use exact decimal strings rather than floating-point JSON numbers. "
            "The complete resulting draft is validated before saving."
        ),
    )


class TemplateDraftItemWriteData(Closed):
    draft: TemplateDraftWriteData
    kind: Literal["SECTION", "QUESTION"]
    item_id: UUID


def get_draft_item_workflow(
    settings=Depends(get_template_version_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_template_draft_item_workflow(
            session=session, idempotency_encryption_key=settings.encryption_key)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Template item runtime is unavailable.") from None


async def patch_draft_item_response(
    *, template_id, version_id, item_id, kind, body,
    request, response, actor, key, workflow,
):
    try:
        no_query(request)
        values = body.normalized_values(kind)
        result = await workflow.edit_item(
            actor=actor, template_id=template_id, version_id=version_id,
            kind=kind, item_id=item_id,
            expected_row_version=body.expected_row_version,
            idempotency_key=key, trace_id=request.state.request_id,
            **values)
        checked = decode_item(
            encode_item(result), actor=actor, request={
                "template_id": str(template_id),
                "version_id": str(version_id),
                "kind": kind,
                "item_id": str(item_id),
                "expected_row_version": body.expected_row_version,
                **values,
            })
        data = TemplateDraftItemWriteData.model_validate(asdict(checked))
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.patch(
    "/{template_id}/drafts/{version_id}/sections/{item_id}",
    response_model=TemplateVersionEnvelope[TemplateDraftItemWriteData],
    dependencies=[Depends(permission)],
    description=(
        "Edit an existing section of an exact draft. Requires Idempotency-Key "
        "and the expected row version of the whole draft. Section identity and "
        "parent remain unchanged. Fields and optional labels are saved atomically "
        "with audit and the encrypted receipt. Replay returns original metadata."
    ),
)
async def patch_template_draft_section(
    template_id: UUID, version_id: UUID, item_id: UUID,
    body: TemplateDraftSectionPatchRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_template_version_key),
    workflow=Depends(get_draft_item_workflow),
):
    return await patch_draft_item_response(
        template_id=template_id, version_id=version_id, item_id=item_id,
        kind="SECTION", body=body, request=request, response=response,
        actor=actor, key=key, workflow=workflow)


@router.patch(
    "/{template_id}/drafts/{version_id}/questions/{item_id}",
    response_model=TemplateVersionEnvelope[TemplateDraftItemWriteData],
    dependencies=[Depends(permission)],
    description=(
        "Edit an existing question using the shared data-driven question contract. "
        "Requires Idempotency-Key and the expected row version of the whole draft. "
        "Question identity and section remain unchanged. Publication and work-type "
        "binding checks remain separate; published versions cannot be edited."
    ),
)
async def patch_template_draft_question(
    template_id: UUID, version_id: UUID, item_id: UUID,
    body: TemplateDraftQuestionPatchRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_template_version_key),
    workflow=Depends(get_draft_item_workflow),
):
    return await patch_draft_item_response(
        template_id=template_id, version_id=version_id, item_id=item_id,
        kind="QUESTION", body=body, request=request, response=response,
        actor=actor, key=key, workflow=workflow)



class TemplateDraftAppendRequest(Closed):
    expected_row_version: SettingsVersion
    labels: dict[StrictStr, dict[StrictStr, StrictStr]] = Field(
        default_factory=dict, max_length=64,
        description=(
            "Full replacement labels dictionary when supplied. "
            "Omission preserves saved labels; null is invalid."
        ),
    )

    def normalized_values(self, kind):
        definition = self.section if kind == "SECTION" else self.question
        optional = (
            {"labels": self.labels}
            if "labels" in self.model_fields_set else {}
        )
        body, labels = normalize_append_request(
            kind=kind, body=definition.model_dump(mode="json"), **optional)
        values = {"body": body}
        if labels is not APPEND_UNSET:
            values["labels"] = labels
        return values


class TemplateDraftSectionCreateRequest(TemplateDraftAppendRequest):
    section: OrganizationSection


class TemplateDraftQuestionCreateRequest(TemplateDraftAppendRequest):
    question: OrganizationQuestion


class TemplateDraftAppendData(Closed):
    draft: TemplateDraftWriteData
    kind: Literal["SECTION", "QUESTION"]
    parent_section_id: UUID | None
    section_ids: list[UUID] = Field(max_length=1)
    question_ids: list[UUID] = Field(min_length=1, max_length=2000)


def get_draft_append_workflow(
    settings=Depends(get_template_version_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_template_draft_append_workflow(
            session=session, idempotency_encryption_key=settings.encryption_key)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Template addition runtime is unavailable.") from None


async def append_draft_item_response(
    *, template_id, version_id, section_id, kind, body,
    request, response, actor, key, workflow,
):
    try:
        no_query(request)
        values = body.normalized_values(kind)
        result = await workflow.append_item(
            actor=actor, template_id=template_id, version_id=version_id,
            section_id=section_id, kind=kind,
            expected_row_version=body.expected_row_version,
            idempotency_key=key, trace_id=request.state.request_id,
            **values)
        checked = decode_append(
            encode_append(result), actor=actor, request={
                "template_id": str(template_id),
                "version_id": str(version_id),
                "section_id": None if section_id is None else str(section_id),
                "kind": kind,
                "expected_row_version": body.expected_row_version,
                **values,
            })
        data = TemplateDraftAppendData.model_validate(asdict(checked))
    except Exception as error:
        raise operation_error(error) from None
    no_store(response)
    return success_envelope(
        data=data.model_dump(mode="json"), request_id=request.state.request_id)


@router.post(
    "/{template_id}/drafts/{version_id}/sections",
    status_code=201,
    response_model=TemplateVersionEnvelope[TemplateDraftAppendData],
    dependencies=[Depends(permission)],
    description=(
        "Add a section with explicit questions to an exact draft. "
        "Uses the shared section/question contract and validates the complete "
        "resulting definition, including base phase order and labels. "
        "Requires Idempotency-Key and the expected row version of the whole draft. "
        "Server-generated IDs, draft version, audit and encrypted receipt share "
        "one commit. Replay returns original IDs and metadata."
    ),
)
async def create_template_draft_section(
    template_id: UUID, version_id: UUID,
    body: TemplateDraftSectionCreateRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_template_version_key),
    workflow=Depends(get_draft_append_workflow),
):
    return await append_draft_item_response(
        template_id=template_id, version_id=version_id, section_id=None,
        kind="SECTION", body=body, request=request, response=response,
        actor=actor, key=key, workflow=workflow)


@router.post(
    "/{template_id}/drafts/{version_id}/sections/{section_id}/questions",
    status_code=201,
    response_model=TemplateVersionEnvelope[TemplateDraftAppendData],
    dependencies=[Depends(permission)],
    description=(
        "Add a question to an exact section of a draft using the shared question "
        "contract. Requires Idempotency-Key and the expected whole-draft row version. "
        "The complete resulting definition is validated before saving. "
        "Published versions cannot receive additions. Replay returns the original "
        "server-generated question ID without another insert or audit."
    ),
)
async def create_template_draft_question(
    template_id: UUID, version_id: UUID, section_id: UUID,
    body: TemplateDraftQuestionCreateRequest,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_template_version_key),
    workflow=Depends(get_draft_append_workflow),
):
    return await append_draft_item_response(
        template_id=template_id, version_id=version_id, section_id=section_id,
        kind="QUESTION", body=body, request=request, response=response,
        actor=actor, key=key, workflow=workflow)


def install_template_version_openapi_precision(application):
    previous = application.openapi
    if getattr(previous, "_template_version_exact_counters", False):
        return
    targets = {
        "TemplateDraftCreateRequest": ("expected_template_row_version",),
        "TemplateDraftVersionRequest": ("expected_row_version",),
        "TemplateDraftCreatedData": ("template_row_version",),
        "TemplateDraftData": ("template_row_version", "version_row_version"),
        "TemplatePublicationReadinessData": ("template_row_version", "version_row_version"),
        "TemplatePublicationData": ("template_row_version", "version_row_version"),
        "TemplateDraftHeaderPatchRequest": ("expected_row_version",),
        "TemplateDraftWriteData": ("template_row_version", "version_row_version"),
        "TemplateDraftSectionPatchRequest": ("expected_row_version",),
        "TemplateDraftQuestionPatchRequest": ("expected_row_version",),
        "TemplateDraftSectionCreateRequest": ("expected_row_version",),
        "TemplateDraftQuestionCreateRequest": ("expected_row_version",),
    }

    def exact_openapi():
        document = previous()
        for schema in document.get("components", {}).get("schemas", {}).values():
            for name in targets.get(schema.get("title"), ()):
                field = schema["properties"][name]
                if field.get("type") != "integer":
                    raise ValueError("Template version counter schema differs.")
                field.update(minimum=1, maximum=2**63 - 1, format="int64")
        return document

    exact_openapi._template_version_exact_counters = True
    application.openapi = exact_openapi
