from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import (
    HTTPException as StarletteHTTPException,
)

from api.errors import ApiHttpError
from api.routes.platform_organization_membership import router as platform_organization_membership_router
from api.middleware import (
    PartnerApiRequestLoggingMiddleware,
)
from api.responses import error_envelope
from services.resend_email_provider import (
    close_resend_http_client,
)
from services.partner_api_logging import (
    PartnerApiRequestLogger,
)
from api.routes import (
    construction_router,
    company_profiles_router,
    auth_router,
    health_router,
    white_label_router,
    search_specialists_router,
    contact_requests_router,
    dialogs_router,
    orders_router,
    favorites_router,
    reviews_router,
    me_router,
    specialist_cabinets_router,
    specialist_services_router,
    specialist_portfolio_router,
    specialist_reviews_router,
    specialist_skills_router,
    specialist_availability_router,
    specialist_orders_router,
    specialist_clients_router,
    specialist_promotions_router,
    specialist_subscriptions_router,
    specialist_calendar_router,
    specialist_statistics_router,
    hr_vacancies_router,
    hr_applications_router,
    webhooks_router,
)


from api.routes.admin_users import router as admin_users_router

from api.routes.admin_specialists import router as admin_specialists_router

from api.routes.admin_reviews import router as admin_reviews_router

from api.routes.admin_complaints import router as admin_complaints_router

from api.routes.admin_audit import router as admin_audit_router

from api.routes.partner_api import router as partner_api_router

from api.routes.files import router as files_router
from api.routes.construction_settings import router as construction_settings_router

from api.routes.construction_invitations import router as construction_invitations_router

from api.routes.construction_invitation_recipient import router as construction_invitation_recipient_router

from api.routes.construction_invitation_management import router as construction_invitation_management_router

from api.routes.construction_invitation_list import router as construction_invitation_list_router

from api.routes.construction_access_read import router as construction_access_read_router

from api.routes.construction_role_options import router as construction_role_options_router

from api.routes.construction_access_mutations import router as construction_access_mutations_router

from api.routes.construction_access_issuance import router as construction_access_issuance_router

from api.routes.construction_team_block import router as construction_team_block_router

from api.routes.construction_project_read import router as construction_project_read_router
from api.routes.construction_address_suggestions import router as construction_address_suggestions_router
from api.routes.construction_project_create import router as construction_project_create_router
from api.routes.construction_lead_create import router as construction_lead_create_router
from api.routes.construction_lead_conversion import router as construction_lead_conversion_router
from api.routes.construction_lead_status import router as construction_lead_status_router
from api.routes.construction_lead_read import router as construction_lead_read_router
from api.routes.construction_activities import router as construction_activities_router
from api.routes.construction_area_archive import router as construction_area_archive_router
from api.routes.construction_area_patch import router as construction_area_patch_router
from api.routes.construction_client_read import router as construction_client_read_router
from api.routes.construction_client_confirmation import router as construction_client_confirmation_router
from api.routes.construction_client_self_link import router as construction_client_self_link_router
from api.routes.construction_client_link_requests import router as construction_client_link_requests_router
from api.routes.construction_client_unlink import router as construction_client_unlink_router

from api.routes.platform_telegram_binding import router as platform_telegram_binding_router
from api.routes.construction_company_page import router as construction_company_page_router, api_router as construction_company_ui_router
from api.routes.construction_invitation_page import router as construction_invitation_page_router
from api.routes.platform_telegram_account_state import router as platform_telegram_account_state_router
from api.routes.construction_client_own_link_state import router as construction_client_own_link_state_router

API_PREFIX = "/api/v1"
REQUEST_ID_HEADER = "X-Request-ID"


def request_id_for(
    request: Request,
) -> str:
    return getattr(
        request.state,
        "request_id",
        str(uuid4()),
    )


def api_error_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_for(request)
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = (
        request_id
    )

    return JSONResponse(
        status_code=status_code,
        content=error_envelope(
            code=code,
            message=message,
            request_id=request_id,
        ),
        headers=response_headers,
    )


@asynccontextmanager
async def api_lifespan(
    _application: FastAPI,
):
    try:
        yield
    finally:
        await close_resend_http_client()


def create_app() -> FastAPI:
    application = FastAPI(
        title="SGHR API Platform",
        version="1.0.0",
        lifespan=api_lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
    )

    application.add_middleware(
        PartnerApiRequestLoggingMiddleware,
        logger=PartnerApiRequestLogger(),
    )

    @application.middleware("http")
    async def add_request_id(
        request: Request,
        call_next,
    ):
        request_id = (
            request.headers.get(REQUEST_ID_HEADER)
            or str(uuid4())
        )
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = (
            request_id
        )
        return response

    @application.exception_handler(
        ApiHttpError
    )
    async def handle_api_http_error(
        request: Request,
        exception: ApiHttpError,
    ):
        return api_error_response(
            request=request,
            status_code=exception.status_code,
            code=exception.code,
            message=exception.message,
            headers=exception.headers,
        )

    @application.exception_handler(
        StarletteHTTPException
    )
    async def handle_http_exception(
        request: Request,
        exception: StarletteHTTPException,
    ):
        if exception.status_code == 404:
            code = "not_found"
            message = "Resource not found."
        else:
            code = "http_error"
            message = (
                exception.detail
                if isinstance(
                    exception.detail,
                    str,
                )
                else "HTTP request failed."
            )

        return api_error_response(
            request=request,
            status_code=exception.status_code,
            code=code,
            message=message,
        )

    @application.exception_handler(
        RequestValidationError
    )
    async def handle_validation_exception(
        request: Request,
        _exception: RequestValidationError,
    ):
        return api_error_response(
            request=request,
            status_code=422,
            code="validation_error",
            message="Request validation failed.",
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request,
        _exception: Exception,
    ):
        return api_error_response(
            request=request,
            status_code=500,
            code="internal_error",
            message="Internal server error.",
        )

    application.include_router(
        construction_router,
        prefix=API_PREFIX,
        tags=["Construction"],
    )
    application.include_router(construction_project_read_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_address_suggestions_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_project_create_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_lead_create_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_lead_conversion_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_lead_status_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_lead_read_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_activities_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_area_archive_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_area_patch_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_client_read_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(construction_client_confirmation_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_client_self_link_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_client_link_requests_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_client_unlink_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_access_read_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_access_mutations_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_access_issuance_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_team_block_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_role_options_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(
        construction_settings_router,
        prefix=API_PREFIX,
        tags=["Construction"],
    )
    application.include_router(construction_invitations_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_invitation_management_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_invitation_list_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_invitation_recipient_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(
        health_router,
        prefix=API_PREFIX,
        tags=["System"],
    )
    application.include_router(
        white_label_router,
        prefix=API_PREFIX,
        tags=["White Label"],
    )
    application.include_router(
        search_specialists_router,
        prefix=API_PREFIX,
        tags=["Search"],
    )
    application.include_router(
        contact_requests_router,
        prefix=API_PREFIX,
        tags=["Contact Requests"],
    )
    application.include_router(
        dialogs_router,
        prefix=API_PREFIX,
        tags=["Dialogs"],
    )
    application.include_router(
        auth_router,
        prefix=API_PREFIX,
        tags=["Authentication"],
    )
    application.include_router(
        me_router,
        prefix=API_PREFIX,
        tags=["Identity"],
    )
    application.include_router(
        specialist_cabinets_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_services_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_portfolio_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_reviews_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_skills_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_availability_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_orders_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_clients_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_promotions_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_subscriptions_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_calendar_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        specialist_statistics_router,
        prefix=API_PREFIX,
        tags=["Specialist"],
    )
    application.include_router(
        hr_vacancies_router,
        prefix=API_PREFIX,
        tags=["HR"],
    )
    application.include_router(
        hr_applications_router,
        prefix=API_PREFIX,
        tags=["HR"],
    )

    application.include_router(
        orders_router,
        prefix=API_PREFIX,
        tags=["Orders"],
    )

    application.include_router(
        favorites_router,
        prefix=API_PREFIX,
        tags=["Favorites"],
    )

    application.include_router(
        reviews_router,
        prefix=API_PREFIX,
        tags=["Reviews"],
    )

    application.include_router(
        admin_users_router,
        prefix=API_PREFIX,
        tags=["Admin Users"],
    )

    application.include_router(
        admin_specialists_router,
        prefix=API_PREFIX,
        tags=["Admin Specialists"],
    )

    application.include_router(
        admin_reviews_router,
        prefix=API_PREFIX,
        tags=["Admin Reviews"],
    )

    application.include_router(
        admin_complaints_router,
        prefix=API_PREFIX,
        tags=["Admin Complaints"],
    )

    application.include_router(
        admin_audit_router,
        prefix=API_PREFIX,
        tags=["Admin Audit"],
    )

    application.include_router(
        partner_api_router,
        prefix=API_PREFIX,
        tags=["Partner API"],
    )

    application.include_router(
        webhooks_router,
        prefix=API_PREFIX,
        tags=["Webhooks"],
    )

    application.include_router(
        files_router,
        prefix=API_PREFIX,
        tags=["Files"],
    )

    application.include_router(
        company_profiles_router,
        prefix=API_PREFIX,
        tags=["Organization"],
    )

    application.include_router(platform_telegram_binding_router, prefix=API_PREFIX, tags=['Platform Telegram'])
    application.include_router(construction_company_page_router)
    application.include_router(construction_company_ui_router, prefix=API_PREFIX, tags=["Construction"])
    application.include_router(construction_invitation_page_router)
    application.include_router(platform_telegram_account_state_router, prefix=API_PREFIX, tags=['Platform Telegram'])
    application.include_router(construction_client_own_link_state_router, prefix=API_PREFIX, tags=['Construction'])
    application.include_router(platform_organization_membership_router, prefix=API_PREFIX, tags=["Platform organizations"])
    return application


app = create_app()
