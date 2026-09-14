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
    get_specialist_orders_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    SpecialistOrderListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.contact_chat import (
    ContactChatError,
)
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsSelectionError,
)
from services.specialist_orders import (
    SpecialistOrdersService,
)


router = APIRouter()

SPECIALIST_ORDERS_READ_PERMISSION = (
    "specialist.orders.read"
)
require_specialist_orders_read = (
    require_permission(
        SPECIALIST_ORDERS_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/orders"
    ),
    response_model=(
        SpecialistOrderListResponse
    ),
)
async def list_specialist_orders(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_orders_read
        ),
    ],
    service: Annotated[
        SpecialistOrdersService,
        Depends(
            get_specialist_orders_service
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
            service.list_orders_for_user(
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
        ContactChatError,
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
                "id": item.order_id,
                "thread_id": item.thread_id,
                "contact_request_id": (
                    item.contact_request_id
                ),
                "client_name": (
                    item.client_name
                ),
                "profession_name": (
                    item.profession_name
                ),
                "status": item.status,
                "description": (
                    item.description
                ),
                "schedule_text": (
                    item.schedule_text
                ),
                "agreed_amount": (
                    item.agreed_amount
                ),
                "currency": item.currency,
                "created_at": (
                    item.created_at
                ),
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
