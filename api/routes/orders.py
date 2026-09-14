from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    Request,
    status,
)

from api.auth import get_current_actor
from api.dependencies import (
    get_user_orders_service,
)
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    UserOrderActionResponse,
    UserOrderCreateRequest,
    UserOrderCreatedResponse,
    UserOrderListResponse,
    UserOrderUpdateRequest,
)
from services.api_identity import (
    ApiActorContext,
)
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError,
)
from services.contact_chat import (
    ContactChatError,
    ContactChatOrderNotFoundError,
)
from services.user_orders import (
    UserOrdersService,
)


router = APIRouter()


@router.post(
    "/orders",
    response_model=UserOrderCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    payload: UserOrderCreateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserOrdersService,
        Depends(get_user_orders_service),
    ],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
        ),
    ],
):
    try:
        result = await service.create_order_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            thread_id=payload.dialog_id,
            description=payload.description,
            start_at=payload.start_at,
            end_at=payload.end_at,
            agreed_amount=payload.agreed_amount,
            currency=payload.currency,
            schedule_text=None,
            idempotency_key=idempotency_key,
        )
    except ApiIdempotencyKeyReusedError as exc:
        raise ApiHttpError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message=(
                "Idempotency key was reused with "
                "a different request."
            ),
        ) from exc
    except ContactChatOrderNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="order_not_found",
            message="Order was not found.",
        ) from exc
    except ContactChatError as exc:
        raise ApiHttpError(
            status_code=422,
            code="order_validation_error",
            message="Order data is not valid.",
        ) from exc

    return success_envelope(
        data={
            "id": result.order_id,
            "dialog_id": result.thread_id,
            "contact_request_id": (
                result.contact_request_id
            ),
            "status": result.status,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.post(
    "/orders/{order_id}/confirm",
    response_model=UserOrderActionResponse,
)
async def confirm_order(
    order_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserOrdersService,
        Depends(get_user_orders_service),
    ],
):
    try:
        result = await service.confirm_order_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            order_id=order_id,
        )
    except ContactChatOrderNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="order_not_found",
            message="Order was not found.",
        ) from exc
    except ContactChatError as exc:
        raise ApiHttpError(
            status_code=409,
            code="order_conflict",
            message=(
                "Order state does not allow "
                "this action."
            ),
        ) from exc

    return success_envelope(
        data={
            "id": result.order_id,
            "dialog_id": result.thread_id,
            "contact_request_id": (
                result.contact_request_id
            ),
            "status": result.status,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.post(
    "/orders/{order_id}/cancel",
    response_model=UserOrderActionResponse,
)
async def cancel_order(
    order_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserOrdersService,
        Depends(get_user_orders_service),
    ],
):
    try:
        result = await service.cancel_order_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            order_id=order_id,
        )
    except ContactChatOrderNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="order_not_found",
            message="Order was not found.",
        ) from exc
    except ContactChatError as exc:
        raise ApiHttpError(
            status_code=409,
            code="order_conflict",
            message=(
                "Order state does not allow "
                "this action."
            ),
        ) from exc

    return success_envelope(
        data={
            "id": result.order_id,
            "dialog_id": result.thread_id,
            "contact_request_id": (
                result.contact_request_id
            ),
            "status": result.status,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.post(
    "/orders/{order_id}/complete",
    response_model=UserOrderActionResponse,
)
async def complete_order(
    order_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserOrdersService,
        Depends(get_user_orders_service),
    ],
):
    try:
        result = await service.complete_order_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            order_id=order_id,
        )
    except ContactChatOrderNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="order_not_found",
            message="Order was not found.",
        ) from exc
    except ContactChatError as exc:
        raise ApiHttpError(
            status_code=409,
            code="order_conflict",
            message=(
                "Order state does not allow "
                "this action."
            ),
        ) from exc

    return success_envelope(
        data={
            "id": result.order_id,
            "dialog_id": result.thread_id,
            "contact_request_id": (
                result.contact_request_id
            ),
            "status": result.status,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.patch(
    "/orders/{order_id}",
    response_model=UserOrderCreatedResponse,
)
async def update_order(
    order_id: UUID,
    payload: UserOrderUpdateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserOrdersService,
        Depends(get_user_orders_service),
    ],
):
    try:
        result = await service.update_order_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            order_id=order_id,
            description=payload.description,
            start_at=payload.start_at,
            end_at=payload.end_at,
            agreed_amount=payload.agreed_amount,
            currency=payload.currency,
        )
    except ContactChatOrderNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="order_not_found",
            message="Order was not found.",
        ) from exc
    except ContactChatError as exc:
        raise ApiHttpError(
            status_code=422,
            code="order_validation_error",
            message="Order data is not valid.",
        ) from exc

    return success_envelope(
        data={
            "id": result.order_id,
            "dialog_id": result.thread_id,
            "contact_request_id": (
                result.contact_request_id
            ),
            "status": result.status,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.get(
    "/me/orders",
    response_model=UserOrderListResponse,
)
async def list_my_orders(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserOrdersService,
        Depends(get_user_orders_service),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=128,
        ),
    ] = None,
):
    page_number = decode_page_cursor(
        cursor
    )

    page = await service.list_orders_for_user(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        language=actor.language_code,
        page=page_number,
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.order_id,
                "dialog_id": item.thread_id,
                "contact_request_id": (
                    item.contact_request_id
                ),
                "counterparty_name": (
                    item.specialist_name
                    if item.is_client
                    else item.client_name
                ),
                "profession_name": (
                    item.profession_name
                ),
                "status": item.status,
                "description": item.description,
                "schedule_text": (
                    item.schedule_text
                ),
                "agreed_amount": (
                    item.agreed_amount
                ),
                "currency": item.currency,
                "created_at": item.created_at,
                "actor_role": (
                    "client"
                    if item.is_client
                    else "specialist"
                ),
            }
            for item in page.items
        ],
        meta={
            "next_cursor": (
                encode_page_cursor(
                    page.page + 1
                )
                if page.has_next
                else None
            ),
            "has_more": page.has_next,
        },
        request_id=request.state.request_id,
    )
