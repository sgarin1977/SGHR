from fastapi import APIRouter, Depends, Request

from api.auth import get_current_actor, require_authenticated_user_rate_limit, require_module, require_suite
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.routes.construction import get_construction_scoped_session
from api.routes.construction import get_construction_access_context
from api.routes.construction import require_construction_permission
import services.construction.construction_tenant_settings as settings_domain
from services.construction.construction_tenant_settings import (
    ConstructionTenantSettingsScopeError,
    build_construction_tenant_settings_service,
)

router = APIRouter(
    prefix="/construction/settings",
    dependencies=[
        Depends(require_authenticated_user_rate_limit()),
        Depends(get_current_actor),
        Depends(get_construction_scoped_session),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
    responses={429: {"description": "RATE_LIMIT_EXCEEDED"}},
)

FIELDS = (
    "tenant_id", "display_name_override", "phone_override",
    "email_override", "website_override", "logo_file_id_override",
    "document_header_text", "document_footer_text", "accent_color",
    "created_at", "updated_at", "row_version",
)


def get_settings_service(
    construction_session=Depends(get_construction_scoped_session),
    platform_session=Depends(get_api_session),
):
    return build_construction_tenant_settings_service(
        construction_session=construction_session,
        platform_session=platform_session,
    )


@router.get("/company")
async def get_company_settings(
    request: Request,
    actor=Depends(get_current_actor),
    service=Depends(get_settings_service),
):
    try:
        settings = await service.get_for_actor(actor=actor)
    except ConstructionTenantSettingsScopeError as exc:
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Construction settings are not available.",
        ) from exc
    return success_envelope(
        data=(
            {field: getattr(settings, field) for field in FIELDS}
            if settings is not None else None
        ),
        request_id=request.state.request_id,
    )


def _settings_write_error(exc):
    if isinstance(exc, settings_domain.ConstructionTenantSettingsNotFoundError):
        return ApiHttpError(status_code=404, code="construction_settings_not_found",
                            message="Construction settings are not available.")
    if isinstance(exc, settings_domain.ConstructionTenantSettingsPermissionDeniedError):
        return ApiHttpError(status_code=403, code="forbidden",
                            message="Construction settings management is not available.")
    if isinstance(exc, settings_domain.ConstructionTenantSettingsRowVersionConflictError):
        return ApiHttpError(status_code=409, code="ROW_VERSION_CONFLICT",
                            message="Construction settings have changed.")
    if isinstance(exc, settings_domain.ConstructionTenantSettingsConflictError):
        return ApiHttpError(status_code=409, code="conflict",
                            message="Construction settings have changed.")
    if isinstance(exc, ValueError):
        return ApiHttpError(status_code=422, code="validation_error",
                            message="Construction settings are invalid.")
    return ApiHttpError(status_code=500, code="internal_error",
                        message="Construction settings operation failed.")


async def _write_settings(*, request, actor, access_context, service, values, create):
    from uuid import UUID

    values = dict(values)
    try:
        logo_id = values.get("logo_file_id_override")
        if logo_id is not None and not isinstance(logo_id, UUID):
            if not isinstance(logo_id, str):
                raise ValueError("Logo reference must be a UUID.")
            values["logo_file_id_override"] = UUID(logo_id)
        if create:
            settings = await service.create_for_actor(
                actor=actor, access_context=access_context,
                values=values, trace_id=request.state.request_id,
            )
        else:
            version = values.pop("expected_row_version", None)
            settings = await service.update_for_actor(
                actor=actor, access_context=access_context,
                expected_row_version=version, changes=values,
                trace_id=request.state.request_id,
            )
    except (ValueError,
            settings_domain.ConstructionTenantSettingsPermissionDeniedError,
            settings_domain.ConstructionTenantSettingsConflictError,
            settings_domain.ConstructionTenantSettingsRowVersionConflictError,
            settings_domain.ConstructionTenantSettingsNotFoundError,
            settings_domain.ConstructionTenantSettingsOperationError) as exc:
        raise _settings_write_error(exc) from exc
    return success_envelope(
        data={field: getattr(settings, field) for field in FIELDS},
        request_id=request.state.request_id,
    )


@router.post("/company", status_code=201, dependencies=[
    Depends(require_construction_permission("construction.settings.manage")),
])
async def create_company_settings(
    request: Request, values: dict,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_settings_service),
):
    return await _write_settings(
        request=request, actor=actor, access_context=access_context,
        service=service, values=values, create=True,
    )


@router.patch("/company", dependencies=[
    Depends(require_construction_permission("construction.settings.manage")),
])
async def update_company_settings(
    request: Request, values: dict,
    actor=Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_settings_service),
):
    return await _write_settings(
        request=request, actor=actor, access_context=access_context,
        service=service, values=values, create=False,
    )
