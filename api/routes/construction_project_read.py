from datetime import timezone
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.construction_project_localization import (
    PlainProjectMeta, LocalizedProjectMeta, LOCALIZATION_ERRORS,
    get_optional_project_localizer, public_project_translations,
    project_localization_error, validate_project_localization_request,
)
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_access
from services.construction.construction_project_read import build_construction_project_read_service
from services.construction.construction_projects import ConstructionProjectNotFoundError, ConstructionProjectValidationError


class ProjectReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    client_id: UUID | None
    name: StrictStr
    status: Literal["draft", "planning", "active", "completed", "cancelled"]
    responsible_user_id: UUID | None
    comment: StrictStr | None
    address_raw: StrictStr
    address_formatted: StrictStr | None
    country_code: StrictStr | None
    region: StrictStr | None
    city: StrictStr | None
    postal_code: StrictStr | None
    latitude: Decimal | None = Field(allow_inf_nan=False)
    longitude: Decimal | None = Field(allow_inf_nan=False)
    address_provider: StrictStr | None
    provider_place_id: StrictStr | None
    address_verification_status: Literal["pending", "verified", "failed"]
    created_by: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: StrictInt = Field(ge=1)

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)


class ProjectReadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: ProjectReadResponse
    meta: dict[str, Any]
    request_id: str


class ProjectDetailsResponse(ProjectReadResponse):
    """Current project state, separate from historical creation receipts."""
    currency: Annotated[
        StrictStr, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
    ] | None = Field(
        description="Stored project currency code; null means unknown. No organization default or currency conversion is applied.",
    )


class ProjectDetailsEnvelope(ProjectReadEnvelope):
    data: ProjectDetailsResponse
    meta: PlainProjectMeta | LocalizedProjectMeta


def get_project_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_project_read_service(session)


def project_action_capabilities(*, actor, access_context, project_id):
    """Project UI projection; mutation endpoints keep their own authorization."""
    from services.construction.construction_activity_create import ACTIVITY_CREATE_PERMISSION

    if getattr(actor, "status", None) != "active":
        raise ConstructionPermissionDeniedError()

    require_construction_access(
        actor=actor, access_context=access_context,
        permission_code="construction.projects.read", project_id=project_id,
    )
    result = {}
    for name, permission in (
        ("can_edit_project", "construction.projects.edit"),
        ("can_plan_activity", ACTIVITY_CREATE_PERMISSION),
        ("can_open_team", "construction.team.manage"),
    ):
        try:
            require_construction_access(
                actor=actor, access_context=access_context,
                permission_code=permission, project_id=project_id,
            )
        except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            result[name] = False
        else:
            result[name] = True
    return result


def public_project(project, *, tenant_id, project_id):
    if (
        getattr(project, "tenant_id", None) != tenant_id
        or getattr(project, "id", None) != project_id
        or getattr(project, "deleted_at", object()) is not None
    ):
        raise ValueError("Project response context is invalid.")
    fields = ProjectReadResponse.model_fields
    return ProjectReadResponse.model_validate({
        field: getattr(project, field) for field in fields
    }).model_dump(mode="json")


def public_project_details(project, *, tenant_id, project_id):
    # ProjectReadResponse/public_project remain the stable creation projection.
    # Do not look up the current organization default for a historical project.
    return ProjectDetailsResponse.model_validate({
        **public_project(project, tenant_id=tenant_id, project_id=project_id),
        "currency": project.currency,
    }).model_dump(mode="json")


read_permission = require_construction_permission("construction.projects.read")
router = ConstructionAPIRouter(
    prefix="/construction/projects",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.get(
    "/{id}", response_model=ProjectDetailsEnvelope,
    dependencies=[Depends(read_permission)],
    description=(
        "Canonical project fields remain original. Set include_translations=true "
        "to receive name/comment display texts in meta.translations using the "
        "fresh Platform profile language and one atomic version-bound cache batch."
    ),
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: project is absent from the authorized tenant"},
        422: {"description": "validation_error"},
        409: {"description": "SOURCE_CONTEXT_CHANGED or LANGUAGE_CONFIGURATION_REQUIRED"},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
        500: {"description": "internal_error"},
    },
)
async def read_project(
    id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_project_read_service),
    localizer=Depends(get_optional_project_localizer),
):
    try:
        await validate_project_localization_request(
            request, localized=localizer is not None)
        project = await service.get_project(
            actor=actor, access_context=access_context, project_id=id,
        )
        data = public_project_details(project, tenant_id=actor.tenant_id, project_id=id)
        capabilities = project_action_capabilities(
            actor=actor, access_context=access_context, project_id=id,
        )
        meta = {"capabilities": capabilities}
        if localizer is not None:
            translated = await localizer.read_for_actor(
                actor=actor, project_id=id,
                expected_source=(data["row_version"], data["name"], data["comment"]))
            metadata = public_project_translations(
                translated, actor=actor, project_id=id, project_data=data)
            meta = {
                "capabilities": project_action_capabilities(
                    actor=actor, access_context=translated.access_context, project_id=id),
                "translations": metadata.model_dump(mode="json"),
            }
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Construction project access is not available.",
        ) from None
    except ConstructionProjectNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found",
            message="Construction project is not available.",
        ) from None
    except ConstructionProjectValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Project request is invalid.",
        ) from None
    except LOCALIZATION_ERRORS as error:
        raise project_localization_error(error) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Construction project read failed.",
        ) from None
    return success_envelope(
        data=data, request_id=request.state.request_id, meta=meta,
    )

# CON_020B_PROJECT_LOCALIZED_READ_V1
