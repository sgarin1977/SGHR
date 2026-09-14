from api.routes.auth import router as auth_router
from api.routes.health import router as health_router
from api.routes.search_specialists import (
    router as search_specialists_router,
)
from api.routes.contact_requests import (
    router as contact_requests_router,
)
from api.routes.dialogs import (
    router as dialogs_router,
)
from api.routes.orders import (
    router as orders_router,
)
from api.routes.favorites import (
    router as favorites_router,
)
from api.routes.reviews import (
    router as reviews_router,
)
from api.routes.me import router as me_router
from api.routes.specialist_cabinets import (
    router as specialist_cabinets_router,
)
from api.routes.specialist_services import (
    router as specialist_services_router,
)
from api.routes.specialist_portfolio import (
    router as specialist_portfolio_router,
)
from api.routes.specialist_reviews import (
    router as specialist_reviews_router,
)
from api.routes.specialist_skills import (
    router as specialist_skills_router,
)
from api.routes.specialist_availability import (
    router as specialist_availability_router,
)
from api.routes.specialist_orders import (
    router as specialist_orders_router,
)
from api.routes.specialist_clients import (
    router as specialist_clients_router,
)
from api.routes.specialist_promotions import (
    router as specialist_promotions_router,
)
from api.routes.specialist_subscriptions import (
    router as specialist_subscriptions_router,
)
from api.routes.specialist_calendar import (
    router as specialist_calendar_router,
)
from api.routes.specialist_statistics import (
    router as specialist_statistics_router,
)
from api.routes.hr_vacancies import (
    router as hr_vacancies_router,
)
from api.routes.hr_applications import (
    router as hr_applications_router,
)

from api.routes.white_label import (
    router as white_label_router,
)

__all__ = [
    "files_router",
    "webhooks_router",
    "partner_api_router",
    "admin_reviews_router",
    "admin_complaints_router",
    "admin_audit_router",
    "white_label_router",
    "admin_users_router",
    "admin_specialists_router",
    "auth_router",
    "health_router",
    "search_specialists_router",
    "contact_requests_router",
    "dialogs_router",
    "orders_router",
    "favorites_router",
    "reviews_router",
    "me_router",
    "specialist_cabinets_router",
    "specialist_services_router",
    "specialist_portfolio_router",
    "specialist_reviews_router",
    "specialist_skills_router",
    "specialist_availability_router",
    "specialist_orders_router",
    "specialist_clients_router",
    "specialist_promotions_router",
    "specialist_subscriptions_router",
    "specialist_calendar_router",
    "specialist_statistics_router",
    "hr_vacancies_router",
    "hr_applications_router",
]

from api.routes.admin_users import (
    router as admin_users_router,
)

from api.routes.admin_specialists import (
    router as admin_specialists_router,
)

from api.routes.admin_reviews import (
    router as admin_reviews_router,
)

from api.routes.partner_api import (
    router as partner_api_router,
)


from api.routes.admin_complaints import (
    router as admin_complaints_router,
)


from api.routes.admin_audit import (
    router as admin_audit_router,
)



from api.routes.webhooks import (
    router as webhooks_router,
)


from api.routes.files import (
    router as files_router,
)
