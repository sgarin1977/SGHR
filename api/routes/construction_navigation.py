"""Read-only menu projection over existing Construction policies."""
from services.construction.construction_api_services import (
    build_navigation_access,
    build_navigation_organization,
    read_construction_navigation,
)
# api_service_boundary_v1
from fastapi import APIRouter, Depends, Request, Response

from api.auth import (
    get_current_actor, require_authenticated_user_rate_limit,
    require_module, require_suite,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import (
    get_construction_access_context, get_construction_scoped_session,
)
from services.construction.construction_foundation import (
    ConstructionOrganizationService, ConstructionOrganizationUnavailableError,
)
from services.construction.construction_lead_read import ConstructionLeadReadService
from services.construction.construction_client_catalog_read import ConstructionClientCatalogReadService
from services.construction.construction_project_catalog_read import ConstructionProjectCatalogReadService
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access,
)


router = APIRouter(
    prefix="/construction/ui",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_authenticated_user_rate_limit()),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)



def get_navigation_access(session=Depends(get_construction_scoped_session)):
    return build_navigation_access(session)



def get_navigation_organization(session=Depends(get_construction_scoped_session)):
    return build_navigation_organization(session)


@router.get("/navigation-context")
async def navigation_context(
    request: Request,
    response: Response,
    actor=Depends(get_current_actor),
    context=Depends(get_construction_access_context),
    access=Depends(get_navigation_access),
    organizations=Depends(get_navigation_organization),
):
    if request.query_params:
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Navigation query parameters are not supported.",
        )
    try:
        data = await read_construction_navigation(
            actor=actor, context=context, access=access,
            organizations=organizations,
        )
    except (
        ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        ConstructionOrganizationUnavailableError,
    ):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Construction navigation is unavailable.",
        ) from None

    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=data,
        request_id=request.state.request_id,
    )
