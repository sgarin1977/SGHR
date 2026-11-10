"""Read-only boundary for universal project areas, not Measurement areas."""
from typing import Any
from uuid import UUID

from fastapi import Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from api.auth import get_current_actor, require_module, require_suite
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.construction_area_localization import (
    PlainProjectMeta, LocalizedAreaMeta, get_optional_area_localizer,
    AREA_LOCALIZATION_ERRORS, validate_area_localization_request,
    public_area_translations, area_localization_error,
)
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context,
    get_construction_scoped_session, require_construction_permission,
)
from services.construction.construction_area_read import (
    ProjectAreaReadItem, build_construction_project_area_read_service,
)
from services.construction.construction_areas import (
    ConstructionProjectAreaProjectNotFoundError,
    ConstructionProjectAreaValidationError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


class ProjectAreaListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[ProjectAreaReadItem]
    meta: PlainProjectMeta | LocalizedAreaMeta
    request_id: str


def get_project_area_read_service(session=Depends(get_construction_scoped_session)):
    return build_construction_project_area_read_service(session=session)


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
    "/{id}/areas", response_model=ProjectAreaListEnvelope,
    dependencies=[Depends(read_permission)],
    description=(
        "Original area names remain in data. include_translations=true adds "
        "profile-language names in meta.translations. One batch contains at most "
        "32 names; repeat translation_area_ids to select a batch without filtering data."
    ),
    responses={
        401: {"description": "authentication_required or invalid_token"},
        403: {"description": "MODULE_DISABLED, permission_required or forbidden"},
        404: {"description": "not_found: project unavailable"},
        422: {"description": "validation_error"},
        429: {"description": "RATE_LIMIT_EXCEEDED"},
        409: {"description": "SOURCE_CONTEXT_CHANGED or LANGUAGE_CONFIGURATION_REQUIRED"},
        503: {"description": "TRANSLATION_UNAVAILABLE"},
        500: {"description": "internal_error"},
    },
)
async def list_project_areas(
    id: UUID, request: Request, response: Response,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_project_area_read_service),
    localizer=Depends(get_optional_area_localizer),
):
    try:
        await validate_area_localization_request(
            request, localized=localizer is not None)
        data = await service.list_areas(
            actor=actor, access_context=access_context, project_id=id,
            include_capabilities=True,
        )
        meta = {"capabilities": {
            "can_create_area": data["can_create_area"],
            "can_edit_area": data["can_edit_area"],
        }}
        if localizer is not None:
            translated = await localizer.workflow.read_for_actor(
                actor=actor, project_id=id, expected_source=data["items"])
            metadata = public_area_translations(
                translated, actor=actor, project_id=id, rows=data["items"],
                selected_ids=localizer.area_ids)
            meta = {
                "capabilities": translated.capabilities,
                "translations": metadata.model_dump(mode="json"),
            }
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Project area read is not available.",
        ) from None
    except ConstructionProjectAreaProjectNotFoundError:
        raise ApiHttpError(
            status_code=404, code="not_found",
            message="Project is not available.",
        ) from None
    except ConstructionProjectAreaValidationError:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Project area request is invalid.",
        ) from None
    except AREA_LOCALIZATION_ERRORS as error:
        raise area_localization_error(error) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Project area read failed.",
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=data["items"], request_id=request.state.request_id, meta=meta,
    )

# universal_area_editor_v1

# CON_020B_AREA_LOCALIZED_READ_V1
