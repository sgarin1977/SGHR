"""CON-011 language context and versioned organization settings."""
from dataclasses import asdict
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator
from api.auth import get_current_actor, require_authenticated_user_rate_limit, require_module, require_suite
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.construction_settings_contract import SettingsVersion
from api.responses import success_envelope
from services.construction.construction_language_context import (
    ConstructionLanguageAccessError, ConstructionLanguageConfigurationError,
    ConstructionLanguageContext, ConstructionLanguageOperationError,
    build_construction_language_context_service,
)


class LanguageOptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: StrictStr = Field(min_length=1, max_length=10)
    name: StrictStr = Field(min_length=1)
    native_name: StrictStr | None
    label: StrictStr = Field(min_length=1)
    enabled_for_organization: StrictBool


class LanguageContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    user_id: UUID
    interface_language: StrictStr
    organization_default_language: StrictStr
    organization_updated_at: AwareDatetime
    row_version: SettingsVersion
    languages: list[LanguageOptionResponse]


class LanguageContextEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: LanguageContextResponse
    meta: dict
    request_id: StrictStr


def get_language_context_service(platform_session=Depends(get_api_session)):
    return build_construction_language_context_service(platform_session=platform_session)


router = APIRouter(prefix="/construction/settings", dependencies=[
    Depends(get_current_actor), Depends(require_authenticated_user_rate_limit()),
    Depends(require_suite("construction", error_code="MODULE_DISABLED")),
    Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
])


@router.get("/languages", response_model=LanguageContextEnvelope, responses={
    401: {"description": "Platform authentication required"},
    403: {"description": "Membership or module unavailable"},
    409: {"description": "LANGUAGE_CONFIGURATION_REQUIRED"},
    422: {"description": "Query selectors are not accepted"},
    429: {"description": "RATE_LIMIT_EXCEEDED"},
    500: {"description": "Sanitized read failure"},
})
async def read_language_context(
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_language_context_service),
):
    if request.query_params:
        raise ApiHttpError(status_code=422, code="validation_error",
                           message="Language context accepts no query selectors.")
    try:
        result = await service.read_for_actor(actor=actor)
        if (not isinstance(result, ConstructionLanguageContext)
                or result.tenant_id != actor.tenant_id or result.user_id != actor.user_id):
            raise ConstructionLanguageOperationError("Language response context differs.")
    except ConstructionLanguageAccessError:
        raise ApiHttpError(status_code=403, code="forbidden",
                           message="Language context is unavailable.") from None
    except ConstructionLanguageConfigurationError:
        raise ApiHttpError(status_code=409, code="LANGUAGE_CONFIGURATION_REQUIRED",
                           message="Platform language configuration is required.") from None
    except ConstructionLanguageOperationError:
        raise ApiHttpError(status_code=500, code="internal_error",
                           message="Language context read failed.") from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(data=asdict(result), request_id=request.state.request_id)


# con_011_organization_language_http_v1
from services.construction.construction_organization_language import (
    OrganizationLanguageResult, OrganizationLanguageDenied,
    OrganizationLanguageValidationError, OrganizationLanguageConflict,
    OrganizationLanguageOperationError,
    build_construction_organization_language_service,
)


class OrganizationLanguageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_language: StrictStr = Field(min_length=1, max_length=10)
    expected_row_version: SettingsVersion

    @field_validator("default_language")
    @classmethod
    def canonical_code(cls, value):
        if value != value.strip() or any(char.isspace() for char in value):
            raise ValueError("A canonical catalog language code is required.")
        return value


class OrganizationLanguageWriteData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    organization_default_language: StrictStr = Field(min_length=1, max_length=10)
    organization_updated_at: AwareDatetime
    row_version: SettingsVersion
    changed: StrictBool


class OrganizationLanguageWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: OrganizationLanguageWriteData
    meta: dict
    request_id: StrictStr


def get_organization_language_service(platform_session=Depends(get_api_session)):
    return build_construction_organization_language_service(
        platform_session=platform_session
    )


@router.patch("/languages", response_model=OrganizationLanguageWriteEnvelope, responses={
    401: {"description": "Platform authentication required"},
    403: {"description": "Organization/settings authority or module unavailable"},
    409: {"description": "ORGANIZATION_SETTINGS_CONFLICT"},
    422: {"description": "Invalid language, expected row version or request fields"},
    429: {"description": "RATE_LIMIT_EXCEEDED"},
    500: {"description": "Sanitized update failure"},
})
async def update_organization_language(
    payload: OrganizationLanguageUpdate,
    request: Request, response: Response,
    actor=Depends(get_current_actor),
    service=Depends(get_organization_language_service),
):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Language update accepts no query selectors.",
        )
    try:
        result = await service.set_default_for_actor(
            actor=actor,
            language_code=payload.default_language,
            expected_row_version=payload.expected_row_version,
            trace_id=request.state.request_id,
        )
        if (
            not isinstance(result, OrganizationLanguageResult)
            or result.tenant_id != actor.tenant_id
        ):
            raise OrganizationLanguageOperationError(
                "Language response context differs."
            )
        data = OrganizationLanguageWriteData(
            tenant_id=result.tenant_id,
            organization_default_language=result.default_language,
            organization_updated_at=result.updated_at,
            changed=result.changed,
            row_version=result.row_version,
        )
    except OrganizationLanguageDenied:
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Organization language management is unavailable.",
        ) from None
    except OrganizationLanguageValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Organization language update is invalid.",
        ) from None
    except OrganizationLanguageConflict:
        raise ApiHttpError(
            status_code=409, code="ORGANIZATION_SETTINGS_CONFLICT",
            message="Organization settings changed. Refresh before saving.",
        ) from None
    except OrganizationLanguageOperationError:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Organization language update failed.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(
        data=data.model_dump(mode="json"),
        request_id=request.state.request_id,
    )

# con_011_row_version_v1


# CON_020D_DOCUMENT_LANGUAGE_HTTP_V1
from typing import Annotated
from pydantic import model_validator
from services.construction.construction_document_language import (
    ConstructionDocumentLanguageService,
    ConstructionDocumentLanguagePlan,
    ConstructionDocumentLanguageValidationError,
    ConstructionDocumentLanguageAccessError,
    ConstructionDocumentLanguageConfigurationError,
    ConstructionDocumentLanguageContextChanged,
)

DocumentLanguageCode = Annotated[
    StrictStr,
    Field(
        min_length=2, max_length=10,
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$"),
]


class DocumentLanguagePlanData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    user_id: UUID
    interface_language: DocumentLanguageCode
    organization_default_language: DocumentLanguageCode
    organization_row_version: SettingsVersion
    document_languages: list[DocumentLanguageCode] = Field(
        min_length=1, max_length=2)
    bilingual: StrictBool

    @model_validator(mode="after")
    def coherent_selection(self):
        if (
            len(set(self.document_languages)) != len(self.document_languages)
            or self.bilingual is not (len(self.document_languages) == 2)
        ):
            raise ValueError("Document language response selection differs.")
        return self


class DocumentLanguagePlanEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: DocumentLanguagePlanData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def get_document_language_service(
    context=Depends(get_language_context_service),
):
    try:
        return ConstructionDocumentLanguageService(
            session=getattr(context, "session", None),
            language_context=context)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Document language runtime is unavailable.") from None


def public_document_language_plan(result, *, actor, document_languages):
    if (
        not isinstance(result, ConstructionDocumentLanguagePlan)
        or result.tenant_id != actor.tenant_id
        or result.user_id != actor.user_id
        or result.document_languages != document_languages
    ):
        raise ValueError("Document language response context differs.")
    return DocumentLanguagePlanData(
        **asdict(result), bilingual=result.bilingual)


@router.get(
    "/document-languages",
    response_model=DocumentLanguagePlanEnvelope,
    responses={
        401: {"description": "Platform authentication required"},
        403: {"description": "Membership or Construction module unavailable"},
        409: {"description": (
            "LANGUAGE_CONFIGURATION_REQUIRED or DOCUMENT_LANGUAGE_CONTEXT_CHANGED")},
        422: {"description": "Explicit one/two distinct active languages required"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        503: {"description": "Document language runtime unavailable"},
        500: {"description": "Sanitized preparation failure"},
    },
    description=(
        "Prepare an explicit ordered selection of one or two document languages. "
        "Repeat document_languages for bilingual output. The interface language "
        "comes separately from the fresh Platform profile. Active languages are "
        "validated against the canonical Platform catalog. No caller tenant, "
        "user, interface language, body or Idempotency-Key is required/accepted "
        "as selection data. This read does not change settings, publish a "
        "document or authorize a document business operation. Publication must "
        "persist the language selection and translation in its document version."
    ),
)
async def read_document_language_plan(
    request: Request, response: Response,
    document_languages: list[DocumentLanguageCode] = Query(
        ..., min_length=1, max_length=2),
    actor=Depends(get_current_actor),
    service=Depends(get_document_language_service),
):
    try:
        if (
            set(request.query_params) - {"document_languages"}
            or await request.body()
        ):
            raise ConstructionDocumentLanguageValidationError(
                "Only explicit document language selectors are accepted.")
        selected = tuple(document_languages)
        result = await service.prepare_for_actor(
            actor=actor, document_languages=selected)
        data = public_document_language_plan(
            result, actor=actor, document_languages=selected)
    except ConstructionDocumentLanguageValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Document language selection is invalid.") from None
    except ConstructionDocumentLanguageAccessError:
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Document language context is unavailable.") from None
    except ConstructionDocumentLanguageConfigurationError:
        raise ApiHttpError(
            status_code=409, code="LANGUAGE_CONFIGURATION_REQUIRED",
            message="Canonical Platform language configuration is required.") from None
    except ConstructionDocumentLanguageContextChanged:
        raise ApiHttpError(
            status_code=409, code="DOCUMENT_LANGUAGE_CONTEXT_CHANGED",
            message="Language context changed. Reload the selection.") from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Document language preparation failed.") from None

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(
        data=data.model_dump(mode="json"),
        request_id=request.state.request_id)
