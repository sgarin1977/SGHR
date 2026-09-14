from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import (
    require_permission,
    require_specialist_actor,
)
from api.dependencies import (
    get_specialist_statistics_service,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    SpecialistStatisticsResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.specialist import (
    SpecialistRegistrationError,
)
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsSelectionError,
)
from services.specialist_statistics import (
    SpecialistStatisticsNotFoundError,
    SpecialistStatisticsService,
    SpecialistStatisticsValidationError,
)


router = APIRouter()

SPECIALIST_STATISTICS_READ_PERMISSION = (
    "specialist.statistics.read"
)
require_specialist_statistics_read = (
    require_permission(
        SPECIALIST_STATISTICS_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/statistics"
    ),
    response_model=(
        SpecialistStatisticsResponse
    ),
)
async def get_specialist_statistics(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_statistics_read
        ),
    ],
    service: Annotated[
        SpecialistStatisticsService,
        Depends(
            get_specialist_statistics_service
        ),
    ],
    period: Annotated[
        Literal[
            "7d",
            "30d",
            "90d",
        ] | None,
        Query(),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(),
    ] = None,
):
    try:
        action = await (
            service.get_statistics_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                period=period,
                date_from=date_from,
                date_to=date_to,
            )
        )
    except SpecialistStatisticsValidationError:
        raise ApiHttpError(
            status_code=422,
            code=(
                "statistics_validation_error"
            ),
            message=(
                "Statistics period is not valid."
            ),
        ) from None
    except (
        SpecialistStatisticsNotFoundError,
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="cabinet_not_found",
            message=(
                "Professional cabinet "
                "was not found."
            ),
        ) from None

    result = action.result

    return success_envelope(
        data={
            "professional_cabinet_id": (
                result.professional_cabinet_id
            ),
            "period": result.period,
            "date_from": result.date_from,
            "date_to": result.date_to,
            "timezone": result.timezone,
            "requests": result.requests,
            "unique_clients": (
                result.unique_clients
            ),
            "started_dialogs": (
                result.started_dialogs
            ),
            "completed_dialogs": (
                result.completed_dialogs
            ),
            "orders": result.orders,
            "completed_orders": (
                result.completed_orders
            ),
            "published_reviews": (
                result.published_reviews
            ),
            "average_published_rating": (
                result.average_published_rating
            ),
            "request_to_dialog": (
                result.request_to_dialog
            ),
            "dialog_to_order": (
                result.dialog_to_order
            ),
            "order_to_completed": (
                result.order_to_completed
            ),
        },
        meta={},
        request_id=request.state.request_id,
    )
