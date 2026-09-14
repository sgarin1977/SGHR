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

from api.auth import (
    require_webhook_access,
    require_webhook_management_rate_limit,
)
from api.dependencies import (
    get_webhook_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    WebhookDeliveryDetailResponse,
    WebhookDeliveryListResponse,
    WebhookEndpointUpdateRequest,
    WebhookEndpointDetailResponse,
    WebhookEndpointListResponse,
    WebhookEndpointCreateRequest,
    WebhookEndpointCreateResponse,
)
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError,
)
from services.webhooks import (
    WebhookDeliveryNotFoundError,
    WebhookEndpointStatusError,
    WebhookAccessContext,
    WebhookCallbackUrlError,
    WebhookEndpointNotFoundError,
    WebhookEventTypeError,
    WebhookOperationError,
    WebhookService,
)


router = APIRouter()

webhooks_read_access = require_webhook_access(
    "webhooks.read"
)
require_webhooks_read = (
    require_webhook_management_rate_limit(
        context_dependency=(
            webhooks_read_access
        ),
    )
)


webhooks_write_access = require_webhook_access(
    "webhooks.write"
)
require_webhooks_write = (
    require_webhook_management_rate_limit(
        context_dependency=(
            webhooks_write_access
        ),
    )
)


@router.get(
    "/webhooks/endpoints",
    response_model=WebhookEndpointListResponse,
)
async def list_webhook_endpoints(
    request: Request,
    actor: Annotated[
        WebhookAccessContext,
        Depends(require_webhooks_read),
    ],
    service: Annotated[
        WebhookService,
        Depends(get_webhook_service),
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

    page = await service.list_endpoints(
        tenant_id=actor.tenant_id,
        api_client_id=actor.api_client_id,
        page=page_number,
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "api_client_id": (
                    item.api_client_id
                ),
                "callback_url": (
                    item.callback_url
                ),
                "status": item.status,
                "events": item.event_types,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
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


@router.post(
    "/webhooks/endpoints",
    response_model=(
        WebhookEndpointCreateResponse
    ),
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook_endpoint(
    payload: WebhookEndpointCreateRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
        ),
    ],
    actor: Annotated[
        WebhookAccessContext,
        Depends(require_webhooks_write),
    ],
    service: Annotated[
        WebhookService,
        Depends(get_webhook_service),
    ],
):
    if actor.principal_type == "api_key":
        if actor.api_client_id is None:
            raise ApiHttpError(
                status_code=403,
                code="api_key_scope_required",
                message=(
                    "API Key client scope "
                    "is required."
                ),
            )

        if (
            payload.api_client_id is not None
            and payload.api_client_id
            != actor.api_client_id
        ):
            raise ApiHttpError(
                status_code=404,
                code="api_client_not_found",
                message=(
                    "API client was not found."
                ),
            )

        scoped_api_client_id = (
            actor.api_client_id
        )
    else:
        if payload.api_client_id is None:
            raise ApiHttpError(
                status_code=422,
                code="webhook_endpoint_invalid",
                message=(
                    "API client is required."
                ),
            )

        scoped_api_client_id = (
            payload.api_client_id
        )

    try:
        result = await service.create_endpoint(
            tenant_id=actor.tenant_id,
            principal_type=(
                actor.principal_type
            ),
            principal_id=actor.principal_id,
            api_client_id=(
                scoped_api_client_id
            ),
            callback_url=payload.callback_url,
            event_types=tuple(
                payload.events
            ),
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
    except WebhookEndpointNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="api_client_not_found",
            message="API client was not found.",
        ) from None
    except (
        WebhookCallbackUrlError,
        WebhookEventTypeError,
    ):
        raise ApiHttpError(
            status_code=422,
            code="webhook_endpoint_invalid",
            message=(
                "Webhook endpoint data "
                "is not valid."
            ),
        ) from None
    except WebhookOperationError:
        raise ApiHttpError(
            status_code=422,
            code="webhook_endpoint_create_failed",
            message=(
                "Webhook endpoint could not "
                "be created."
            ),
        ) from None

    return success_envelope(
        data={
            "id": result.id,
            "api_client_id": (
                result.api_client_id
            ),
            "callback_url": (
                result.callback_url
            ),
            "status": result.status,
            "events": result.event_types,
            "created_at": result.created_at,
            "secret": result.secret,
        },
        request_id=request.state.request_id,
    )


@router.get(
    "/webhooks/endpoints/{endpoint_id}",
    response_model=(
        WebhookEndpointDetailResponse
    ),
)
async def get_webhook_endpoint(
    endpoint_id: UUID,
    request: Request,
    actor: Annotated[
        WebhookAccessContext,
        Depends(require_webhooks_read),
    ],
    service: Annotated[
        WebhookService,
        Depends(get_webhook_service),
    ],
):
    try:
        result = await service.get_endpoint(
            tenant_id=actor.tenant_id,
            endpoint_id=endpoint_id,
            api_client_id=(
                actor.api_client_id
            ),
        )
    except WebhookEndpointNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="webhook_endpoint_not_found",
            message=(
                "Webhook endpoint was not found."
            ),
        ) from None

    return success_envelope(
        data={
            "id": result.id,
            "api_client_id": (
                result.api_client_id
            ),
            "callback_url": (
                result.callback_url
            ),
            "status": result.status,
            "events": result.event_types,
            "created_at": result.created_at,
            "updated_at": result.updated_at,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.patch(
    "/webhooks/endpoints/{endpoint_id}",
    response_model=(
        WebhookEndpointDetailResponse
    ),
)
async def update_webhook_endpoint(
    endpoint_id: UUID,
    payload: WebhookEndpointUpdateRequest,
    request: Request,
    actor: Annotated[
        WebhookAccessContext,
        Depends(require_webhooks_write),
    ],
    service: Annotated[
        WebhookService,
        Depends(get_webhook_service),
    ],
):
    if (
        payload.callback_url is None
        and payload.events is None
        and payload.status is None
    ):
        raise ApiHttpError(
            status_code=422,
            code="webhook_endpoint_invalid",
            message=(
                "At least one Webhook endpoint "
                "field is required."
            ),
        )

    try:
        result = await service.update_endpoint(
            tenant_id=actor.tenant_id,
            endpoint_id=endpoint_id,
            api_client_id=(
                actor.api_client_id
            ),
            callback_url=(
                payload.callback_url
            ),
            event_types=(
                tuple(payload.events)
                if payload.events is not None
                else None
            ),
            status=payload.status,
        )
    except WebhookEndpointNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="webhook_endpoint_not_found",
            message=(
                "Webhook endpoint was not found."
            ),
        ) from None
    except (
        WebhookCallbackUrlError,
        WebhookEndpointStatusError,
        WebhookEventTypeError,
    ):
        raise ApiHttpError(
            status_code=422,
            code="webhook_endpoint_invalid",
            message=(
                "Webhook endpoint data "
                "is not valid."
            ),
        ) from None
    except WebhookOperationError:
        raise ApiHttpError(
            status_code=422,
            code="webhook_endpoint_update_failed",
            message=(
                "Webhook endpoint could not "
                "be updated."
            ),
        ) from None

    return success_envelope(
        data={
            "id": result.id,
            "api_client_id": (
                result.api_client_id
            ),
            "callback_url": (
                result.callback_url
            ),
            "status": result.status,
            "events": result.event_types,
            "created_at": result.created_at,
            "updated_at": result.updated_at,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.delete(
    "/webhooks/endpoints/{endpoint_id}",
    response_model=(
        WebhookEndpointDetailResponse
    ),
)
async def disable_webhook_endpoint(
    endpoint_id: UUID,
    request: Request,
    actor: Annotated[
        WebhookAccessContext,
        Depends(require_webhooks_write),
    ],
    service: Annotated[
        WebhookService,
        Depends(get_webhook_service),
    ],
):
    try:
        result = await service.update_endpoint(
            tenant_id=actor.tenant_id,
            endpoint_id=endpoint_id,
            api_client_id=(
                actor.api_client_id
            ),
            callback_url=None,
            event_types=None,
            status="disabled",
        )
    except WebhookEndpointNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="webhook_endpoint_not_found",
            message=(
                "Webhook endpoint was not found."
            ),
        ) from None
    except WebhookOperationError:
        raise ApiHttpError(
            status_code=422,
            code="webhook_endpoint_disable_failed",
            message=(
                "Webhook endpoint could not "
                "be disabled."
            ),
        ) from None

    return success_envelope(
        data={
            "id": result.id,
            "api_client_id": (
                result.api_client_id
            ),
            "callback_url": (
                result.callback_url
            ),
            "status": result.status,
            "events": result.event_types,
            "created_at": result.created_at,
            "updated_at": result.updated_at,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.get(
    "/webhooks/deliveries",
    response_model=WebhookDeliveryListResponse,
)
async def list_webhook_deliveries(
    request: Request,
    actor: Annotated[
        WebhookAccessContext,
        Depends(require_webhooks_read),
    ],
    service: Annotated[
        WebhookService,
        Depends(get_webhook_service),
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

    page = await service.list_deliveries(
        tenant_id=actor.tenant_id,
        api_client_id=actor.api_client_id,
        page=page_number,
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "webhook_event_id": (
                    item.webhook_event_id
                ),
                "webhook_endpoint_id": (
                    item.webhook_endpoint_id
                ),
                "status": item.status,
                "attempt_count": (
                    item.attempt_count
                ),
                "next_attempt_at": (
                    item.next_attempt_at
                ),
                "response_status": (
                    item.response_status
                ),
                "error_category": (
                    item.error_category
                ),
                "delivered_at": (
                    item.delivered_at
                ),
                "created_at": item.created_at,
                "updated_at": item.updated_at,
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


@router.post(
    "/webhooks/deliveries/{delivery_id}/redeliver",
    response_model=WebhookDeliveryDetailResponse,
)
async def redeliver_webhook_delivery(
    delivery_id: UUID,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
        ),
    ],
    actor: Annotated[
        WebhookAccessContext,
        Depends(require_webhooks_write),
    ],
    service: Annotated[
        WebhookService,
        Depends(get_webhook_service),
    ],
):
    try:
        result = await service.redeliver_delivery(
            tenant_id=actor.tenant_id,
            principal_type=(
                actor.principal_type
            ),
            principal_id=actor.principal_id,
            api_client_id=(
                actor.api_client_id
            ),
            delivery_id=delivery_id,
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
    except WebhookDeliveryNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="webhook_delivery_not_found",
            message=(
                "Webhook delivery was not found."
            ),
        ) from None
    except WebhookOperationError:
        raise ApiHttpError(
            status_code=422,
            code="webhook_redelivery_failed",
            message=(
                "Webhook delivery could not "
                "be queued again."
            ),
        ) from None

    return success_envelope(
        data={
            "id": result.id,
            "webhook_event_id": (
                result.webhook_event_id
            ),
            "webhook_endpoint_id": (
                result.webhook_endpoint_id
            ),
            "status": result.status,
            "attempt_count": (
                result.attempt_count
            ),
            "next_attempt_at": (
                result.next_attempt_at
            ),
            "response_status": (
                result.response_status
            ),
            "error_category": (
                result.error_category
            ),
            "delivered_at": (
                result.delivered_at
            ),
            "created_at": result.created_at,
            "updated_at": result.updated_at,
        },
        meta={},
        request_id=request.state.request_id,
    )

