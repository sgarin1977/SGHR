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
    get_user_dialogs_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    ContactRequestCancelResponse,
    ContactRequestCreateRequest,
    ContactRequestCreatedResponse,
    ContactRequestDetailResponse,
    ContactRequestListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError,
)
from services.contact_chat import (
    ContactChatError,
    ContactChatRateLimitError,
)
from services.user_dialogs import (
    UserDialogsService,
)


router = APIRouter()


@router.get(
    "/contact-requests",
    response_model=ContactRequestListResponse,
)
async def list_contact_requests(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserDialogsService,
        Depends(get_user_dialogs_service),
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

    action = await (
        service.list_contact_requests_for_user(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            language=actor.language_code,
            page=page_number,
            page_size=limit,
        )
    )

    return success_envelope(
        data=[
            {
                "id": item.contact_request_id,
                "thread_id": item.thread_id,
                "specialist_name": (
                    item.specialist_name
                ),
                "profession_name": (
                    item.profession_name
                ),
                "message": item.message,
                "status": item.status,
                "created_at": item.created_at,
            }
            for item in action.items
        ],
        meta={
            "next_cursor": (
                encode_page_cursor(
                    action.page + 1
                )
                if action.has_next
                else None
            ),
            "has_more": action.has_next,
        },
        request_id=request.state.request_id,
    )


@router.get(
    "/contact-requests/{contact_request_id}",
    response_model=(
        ContactRequestDetailResponse
    ),
)
async def get_contact_request(
    contact_request_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserDialogsService,
        Depends(get_user_dialogs_service),
    ],
):
    try:
        action = await (
            service
            .get_contact_request_for_user(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                language=actor.language_code,
                contact_request_id=(
                    contact_request_id
                ),
            )
        )
    except ContactChatError:
        raise ApiHttpError(
            status_code=404,
            code="contact_request_not_found",
            message=(
                "Contact request was not found."
            ),
        ) from None

    detail = action.detail

    return success_envelope(
        data={
            "id": detail.contact_request_id,
            "thread_id": detail.thread_id,
            "specialist_name": (
                detail.specialist_name
            ),
            "profession_name": (
                detail.profession_name
            ),
            "message": detail.message,
            "status": detail.status,
            "created_at": detail.created_at,
        },
        request_id=request.state.request_id,
    )


@router.post(
    (
        "/contact-requests/"
        "{contact_request_id}/cancel"
    ),
    response_model=(
        ContactRequestCancelResponse
    ),
)
async def cancel_contact_request(
    contact_request_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserDialogsService,
        Depends(get_user_dialogs_service),
    ],
):
    try:
        action = await (
            service
            .cancel_contact_request_for_user(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                language=actor.language_code,
                contact_request_id=(
                    contact_request_id
                ),
            )
        )
    except ContactChatError:
        raise ApiHttpError(
            status_code=404,
            code="contact_request_not_found",
            message=(
                "Contact request was not found."
            ),
        ) from None

    result = action.result

    return success_envelope(
        data={
            "id": result.contact_request_id,
            "thread_id": result.thread_id,
            "status": result.status,
            "thread_status": (
                result.thread_status
            ),
        },
        request_id=request.state.request_id,
    )


@router.post(
    "/contact-requests",
    response_model=(
        ContactRequestCreatedResponse
    ),
    status_code=status.HTTP_201_CREATED,
)
async def create_contact_request(
    payload: ContactRequestCreateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserDialogsService,
        Depends(get_user_dialogs_service),
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
        action = await (
            service
            .create_contact_request_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                specialist_id=(
                    payload.specialist_id
                ),
                profession_id=(
                    payload.profession_id
                ),
                message=payload.message,
                idempotency_key=(
                    idempotency_key
                ),
            )
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
    except ContactChatRateLimitError:
        raise ApiHttpError(
            status_code=429,
            code=(
                "contact_request_rate_limit"
            ),
            message=(
                "Too many contact requests."
            ),
        ) from None
    except ContactChatError:
        raise ApiHttpError(
            status_code=422,
            code=(
                "contact_request_"
                "validation_error"
            ),
            message=(
                "Contact request is not valid."
            ),
        ) from None

    result = action.chat

    return success_envelope(
        data={
            "contact_request_id": (
                result.contact_request_id
            ),
            "thread_id": result.thread_id,
            "was_existing": bool(
                result.was_existing
            ),
            "message_masked": bool(
                result.message_masked
            ),
            "thread_restricted": bool(
                result.thread_restricted
            ),
        },
        request_id=request.state.request_id,
    )
