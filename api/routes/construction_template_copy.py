"""CON-O003 exact-source copying through the shared encrypted workflow."""
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session,
    require_construction_permission,
)
from api.settings import ApiConfigurationError, ApiIdempotencySettings
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_starter_templates import (
    ConstructionStarterTemplateNotFoundError,
    ConstructionStarterTemplateValidationError,
)
from services.construction.construction_template_copy import (
    ConstructionTemplateCopyValidationError,
    ConstructionTemplateCopyConflictError,
    ConstructionTemplateCopyBindingUnavailable,
)
from services.construction.construction_template_copy_workflow import (
    ConstructionTemplateCopyInProgressError,
    build_construction_template_copy_workflow, encode_result, decode_result,
)


class TemplateCopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_version_id: UUID
    code: StrictStr | None = Field(
        default=None, min_length=1, max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
        description="Optional organization-local code; null or omission uses the source code.")


class TemplateCopyData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    template_id: UUID
    code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    template_row_version: Literal[1]
    template_version_id: UUID
    version: Literal[1]
    version_row_version: Literal[2]
    source_starter_version_id: UUID
    definition_schema_version: Literal[1]
    source_language: StrictStr = Field(
        pattern=r"^[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?$")
    content_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    section_count: StrictInt = Field(ge=1, le=100)
    question_count: StrictInt = Field(ge=1, le=2000)
    created_by: UUID
    created_at: AwareDatetime
    published_at: AwareDatetime


class TemplateCopyEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: TemplateCopyData
    meta: dict
    request_id: StrictStr = Field(min_length=1)


def require_template_copy_key(
    request: Request,
    value: Annotated[str, Header(
        alias="Idempotency-Key", min_length=1, max_length=255,
        description=(
            "Required. Within the shared Platform retention window, "
            "the same actor, organization, key and request replay the original result."
        ),
    )],
):
    if (
        request.query_params
        or len(request.headers.getlist("Idempotency-Key")) != 1
        or not value.strip()
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Template copy request is invalid.")
    return value


def get_template_copy_settings(_key=Depends(require_template_copy_key)):
    try:
        return ApiIdempotencySettings.from_env()
    except ApiConfigurationError:
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Template copy replay is not configured.") from None


def get_template_copy_workflow(
    settings=Depends(get_template_copy_settings),
    session=Depends(get_construction_scoped_session),
):
    try:
        return build_construction_template_copy_workflow(
            session=session, idempotency_encryption_key=settings.encryption_key)
    except (ValueError, TypeError):
        raise ApiHttpError(
            status_code=503, code="service_unavailable",
            message="Template copy runtime is unavailable.") from None


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
        404: {"description": "not_found: source version unavailable"},
        409: {"description": (
            "IDEMPOTENCY_KEY_REUSED, idempotency_in_progress, "
            "TEMPLATE_COPY_CONFLICT or TEMPLATE_BINDING_UNAVAILABLE")},
        422: {"description": "validation_error: exact version and valid key required"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        500: {"description": "internal_error: sanitized copy failure"},
        503: {"description": "service_unavailable: replay configuration unavailable"},
    },
)
permission = require_construction_permission("construction.settings.manage")


@router.post(
    "/copy-starter", status_code=201, response_model=TemplateCopyEnvelope,
    dependencies=[Depends(permission)],
    description=(
        "Copy an exact visible published starter version into the authenticated "
        "organization. Copy, initial publication, audit and encrypted receipt "
        "share one commit. Replay returns the original creation result."
    ),
)
async def copy_starter_template(
    body: TemplateCopyRequest, request: Request, response: Response,
    actor=Depends(get_current_actor),
    key=Depends(require_template_copy_key),
    workflow=Depends(get_template_copy_workflow),
):
    try:
        result = await workflow.copy_template(
            actor=actor, source_version_id=body.source_version_id,
            code=body.code, idempotency_key=key,
            trace_id=request.state.request_id)
        checked = decode_result(
            encode_result(result), actor=actor,
            request={
                "source_version_id": str(body.source_version_id),
                "code": body.code,
            })
        data = TemplateCopyData.model_validate(
            asdict(checked)).model_dump(mode="json")
    except ApiIdempotencyKeyReusedError:
        raise ApiHttpError(
            status_code=409, code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was reused with a different request.") from None
    except ConstructionTemplateCopyInProgressError:
        raise ApiHttpError(
            status_code=409, code="idempotency_in_progress",
            message="Template copy is still processing.") from None
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Organization template copying is unavailable.") from None
    except ConstructionStarterTemplateNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found",
            message="Starter template version is unavailable.") from None
    except ConstructionTemplateCopyBindingUnavailable:
        raise ApiHttpError(
            status_code=409, code="TEMPLATE_BINDING_UNAVAILABLE",
            message="Template work-type validation is unavailable.") from None
    except ConstructionTemplateCopyConflictError:
        raise ApiHttpError(
            status_code=409, code="TEMPLATE_COPY_CONFLICT",
            message="Template copy conflicts with the current organization state.") from None
    except (
        ConstructionTemplateCopyValidationError,
        ConstructionStarterTemplateValidationError,
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Template copy request is invalid.") from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Template copy failed.") from None
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return success_envelope(
        data=data, request_id=request.state.request_id)
