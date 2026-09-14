from typing import Annotated
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
    get_specialist_subscriptions_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    SpecialistSubscriptionListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.billing import BillingError
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsSelectionError,
)
from services.specialist_subscriptions import (
    SpecialistSubscriptionsService,
)


router = APIRouter()

SPECIALIST_SUBSCRIPTIONS_READ_PERMISSION = (
    "specialist.subscriptions.read"
)
require_specialist_subscriptions_read = (
    require_permission(
        SPECIALIST_SUBSCRIPTIONS_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/subscriptions"
    ),
    response_model=(
        SpecialistSubscriptionListResponse
    ),
)
async def list_specialist_subscriptions(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_subscriptions_read
        ),
    ],
    service: Annotated[
        SpecialistSubscriptionsService,
        Depends(
            get_specialist_subscriptions_service
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    page = decode_page_cursor(cursor)

    try:
        action = await (
            service.list_subscriptions_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                limit=limit + 1,
                offset=page * limit,
            )
        )
    except (
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
        BillingError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="cabinet_not_found",
            message=(
                "Professional cabinet "
                "was not found."
            ),
        ) from None

    rows = list(action.result)
    has_more = len(rows) > limit
    items = rows[:limit]

    return success_envelope(
        data=[
            {
                "id": item.id,
                "plan_code": item.plan_code,
                "status": item.status,
                "billing_period": (
                    item.billing_period
                ),
                "amount": item.amount,
                "currency": item.currency,
                "starts_at": item.starts_at,
                "current_period_start": (
                    item.current_period_start
                ),
                "current_period_end": (
                    item.current_period_end
                ),
                "cancel_at_period_end": (
                    item.cancel_at_period_end
                ),
                "cancelled_at": (
                    item.cancelled_at
                ),
                "ended_at": item.ended_at,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in items
        ],
        meta={
            "next_cursor": (
                encode_page_cursor(page + 1)
                if has_more
                else None
            ),
            "has_more": has_more,
        },
        request_id=request.state.request_id,
    )
