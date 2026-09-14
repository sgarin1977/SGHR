from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.session import get_session
from services.hr_vacancies import (
    HrVacancyService,
    build_hr_vacancy_service,
)
from services.hr_applications import (
    HrApplicationService,
    build_hr_application_service,
)
from services.specialist_cabinets import (
    SpecialistCabinetsService,
)
from services.specialist_services import (
    SpecialistServicesService,
)
from services.specialist_portfolio import (
    SpecialistPortfolioService,
)
from services.specialist_reviews import (
    SpecialistReviewsService,
)
from services.specialist_orders import (
    SpecialistOrdersService,
)
from services.specialist_skills import (
    SpecialistSkillsService,
)
from services.specialist_clients import (
    SpecialistClientsService,
)
from services.specialist_promotions import (
    SpecialistPromotionsService,
)
from services.specialist_subscriptions import (
    SpecialistSubscriptionsService,
)
from services.specialist_calendar import (
    SpecialistCalendarService,
)
from services.specialist_statistics import (
    SpecialistStatisticsService,
)
from services.user_search import (
    UserSearchService,
)
from services.user_dialogs import (
    UserDialogsService,
)
from services.user_orders import (
    UserOrdersService,
    build_user_orders_service,
)
from services.user_favorites import (
    UserFavoritesService,
)
from services.user_reviews import (
    UserReviewsService,
)


async def get_api_session(
) -> AsyncIterator[AsyncSession]:
    async with get_session() as session:
        yield session


def get_specialist_cabinets_service(
    session=Depends(get_api_session),
) -> SpecialistCabinetsService:
    return SpecialistCabinetsService(session)

def get_specialist_services_service(
    session=Depends(get_api_session),
) -> SpecialistServicesService:
    return SpecialistServicesService(session)

def get_specialist_portfolio_service(
    session=Depends(get_api_session),
) -> SpecialistPortfolioService:
    return SpecialistPortfolioService(session)

def get_specialist_reviews_service(
    session=Depends(get_api_session),
) -> SpecialistReviewsService:
    return SpecialistReviewsService(session)

def get_specialist_skills_service(
    session=Depends(get_api_session),
) -> SpecialistSkillsService:
    return SpecialistSkillsService(session)

def get_specialist_orders_service(
    session=Depends(get_api_session),
) -> SpecialistOrdersService:
    return SpecialistOrdersService(session)

def get_specialist_clients_service(
    session=Depends(get_api_session),
) -> SpecialistClientsService:
    return SpecialistClientsService(session)

def get_specialist_promotions_service(
    session=Depends(get_api_session),
) -> SpecialistPromotionsService:
    return SpecialistPromotionsService(session)

def get_specialist_subscriptions_service(
    session=Depends(get_api_session),
) -> SpecialistSubscriptionsService:
    return SpecialistSubscriptionsService(
        session
    )


def get_specialist_calendar_service(
    session=Depends(get_api_session),
) -> SpecialistCalendarService:
    return SpecialistCalendarService(
        session
    )


def get_specialist_statistics_service(
    session=Depends(get_api_session),
) -> SpecialistStatisticsService:
    return SpecialistStatisticsService(
        session
    )



def get_user_search_service(
    session=Depends(get_api_session),
) -> UserSearchService:
    return UserSearchService(
        session
    )

def get_user_dialogs_service(
    session=Depends(get_api_session),
) -> UserDialogsService:
    return UserDialogsService(
        session,
        idempotency=(
            get_api_idempotency_service(
                session=session
            )
        ),
    )


def get_user_orders_service(
    session=Depends(get_api_session),
) -> UserOrdersService:
    return build_user_orders_service(
        session
    )


def get_hr_vacancy_service(
    session=Depends(get_api_session),
) -> HrVacancyService:
    return build_hr_vacancy_service(
        session
    )


def get_hr_application_service(
    session=Depends(get_api_session),
) -> HrApplicationService:
    return build_hr_application_service(
        session
    )


def get_user_favorites_service(
    session=Depends(get_api_session),
) -> UserFavoritesService:
    return UserFavoritesService(session)


def get_user_reviews_service(
    session=Depends(get_api_session),
) -> UserReviewsService:
    return UserReviewsService(session)

from services.white_label import (
    WhiteLabelService,
    build_white_label_service,
)

def get_white_label_service(
    session=Depends(get_api_session),
) -> WhiteLabelService:
    return build_white_label_service(
        session
    )

from services.module_access import (
    TenantModuleAccessService,
    TenantSuiteAccessService,
    build_tenant_module_access_service,
    build_tenant_suite_access_service,
)

def get_tenant_module_access_service(
    session=Depends(get_api_session),
) -> TenantModuleAccessService:
    return build_tenant_module_access_service(
        session
    )


def get_tenant_suite_access_service(
    session=Depends(get_api_session),
) -> TenantSuiteAccessService:
    return build_tenant_suite_access_service(
        session
    )




from services.api_admin_users import (
    ApiAdminUsersService,
    build_api_admin_users_service,
)


def get_api_admin_users_service(
    session=Depends(get_api_session),
) -> ApiAdminUsersService:
    return build_api_admin_users_service(
        session
    )



from services.api_admin_specialists import (
    ApiAdminSpecialistsService,
    build_api_admin_specialists_service,
)


def get_api_admin_specialists_service(
    session=Depends(get_api_session),
) -> ApiAdminSpecialistsService:
    return (
        build_api_admin_specialists_service(
            session
        )
    )



from services.api_admin_reviews import (
    ApiAdminReviewsService,
    build_api_admin_reviews_service,
)


def get_api_admin_reviews_service(
    session=Depends(get_api_session),
) -> ApiAdminReviewsService:
    return build_api_admin_reviews_service(
        session
    )



from services.partner_api_auth import (
    PartnerApiAuthenticationService,
    build_partner_api_authentication_service,
)


def get_partner_api_authentication_service(
    session=Depends(get_api_session),
) -> PartnerApiAuthenticationService:
    return (
        build_partner_api_authentication_service(
            session
        )
    )



from services.partner_api import (
    PartnerApiService,
    build_partner_api_service,
)


def get_partner_api_service(
    session=Depends(get_api_session),
) -> PartnerApiService:
    return build_partner_api_service(
        session,
        idempotency=(
            get_api_idempotency_service(
                session=session
            )
        ),
    )


from services.api_admin_complaints import (
    ApiAdminComplaintsService,
    build_api_admin_complaints_service,
)


def get_api_admin_complaints_service(
    session=Depends(get_api_session),
) -> ApiAdminComplaintsService:
    return build_api_admin_complaints_service(
        session
    )


from services.api_admin_audit import (
    ApiAdminAuditService,
    build_api_admin_audit_service,
)


def get_api_admin_audit_service(
    session=Depends(get_api_session),
) -> ApiAdminAuditService:
    return build_api_admin_audit_service(
        session
    )



from api.settings import ApiIdempotencySettings
from services.api_idempotency import (
    ApiIdempotencyService,
    build_api_idempotency_service,
)


def get_api_idempotency_service(
    session=Depends(get_api_session),
) -> ApiIdempotencyService:
    settings = ApiIdempotencySettings.from_env()
    return build_api_idempotency_service(
        session,
        encryption_key=settings.encryption_key,
    )



from api.settings import ApiWebhookSettings
from database.repositories.webhooks import (
    WebhookRepository,
)
from services.webhooks import (
    WebhookCallbackUrlValidator,
    WebhookSecretCodec,
    WebhookService,
)


def get_webhook_service(
    session=Depends(get_api_session),
) -> WebhookService:
    settings = ApiWebhookSettings.from_env()

    return WebhookService(
        session=session,
        repository=WebhookRepository(
            session
        ),
        secret_codec=WebhookSecretCodec(
            encryption_key=(
                settings.secret_encryption_key
            ),
        ),
        callback_validator=(
            WebhookCallbackUrlValidator()
        ),
        environment=settings.environment,
        idempotency=(
            get_api_idempotency_service(
                session=session
            )
        ),
    )



from api.settings import ApiRateLimitSettings
from services.api_rate_limits import (
    ApiRequestRateLimitService,
    build_api_request_rate_limit_service,
)


@lru_cache(maxsize=1)
def get_api_request_rate_limit_service(
) -> ApiRequestRateLimitService:
    settings = ApiRateLimitSettings.from_env()
    redis_client = None

    if settings.redis_url is not None:
        from redis.asyncio import Redis

        redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=False,
        )

    return build_api_request_rate_limit_service(
        environment=settings.environment,
        redis_client=redis_client,
    )


from services.files import (
    FileService,
    build_file_service,
)


def get_file_service(
    session=Depends(get_api_session),
) -> FileService:
    return build_file_service(session)

