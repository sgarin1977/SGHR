from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
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
    DialogDetailResponse,
    DialogListResponse,
    DialogMessageCreateRequest,
    DialogMessageCreatedResponse,
    DialogFinishResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.contact_chat import (
    ContactChatError,
    ContactChatRateLimitError,
    ContactChatThreadNotFoundError,
)
from services.user_dialogs import (
    UserDialogsSelectionError,
    UserDialogsService,
)


router = APIRouter()


@router.get(
    "/me/dialogs",
    response_model=DialogListResponse,
)
async def list_my_dialogs(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserDialogsService,
        Depends(get_user_dialogs_service),
    ],
    role: Annotated[
        Literal["client", "specialist"] | None,
        Query(),
    ] = None,
    view: Annotated[
        Literal[
            "active",
            "new",
            "completed",
            "archive",
            "hidden",
        ],
        Query(),
    ] = "active",
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=200,
        ),
    ] = None,
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
    selected_role = (
        role
        or (
            "specialist"
            if actor.active_role
            == "specialist"
            else "client"
        )
    )

    if selected_role not in actor.roles:
        raise ApiHttpError(
            status_code=403,
            code="dialog_role_denied",
            message="Dialog role access denied.",
        )

    page_number = decode_page_cursor(
        cursor
    )

    try:
        page = await service.list_dialogs_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            language=actor.language_code,
            role=selected_role,
            view=view,
            page=page_number,
            page_size=limit,
            search_query=q,
        )
    except UserDialogsSelectionError:
        raise ApiHttpError(
            status_code=422,
            code="dialog_validation_error",
            message=(
                "Dialog filters are not valid."
            ),
        ) from None

    return success_envelope(
        data=[
            {
                "id": item.thread_id,
                "counterparty_name": (
                    item.specialist_name
                ),
                "profession_name": (
                    item.profession_name
                ),
                "last_message_text": (
                    item.last_message_text
                ),
                "last_message_at": (
                    item.last_message_at
                ),
                "unread_count": (
                    item.unread_count
                ),
                "status": item.status,
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
            "unread_messages": (
                page.unread_messages
            ),
        },
        request_id=request.state.request_id,
    )



@router.get(
    "/dialogs/{thread_id}",
    response_model=DialogDetailResponse,
)
async def get_dialog_detail(
    thread_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserDialogsService,
        Depends(get_user_dialogs_service),
    ],
    role: Annotated[
        Literal["client", "specialist"] | None,
        Query(),
    ] = None,
):
    selected_role = (
        role
        or (
            "specialist"
            if actor.active_role
            == "specialist"
            else "client"
        )
    )

    if selected_role not in actor.roles:
        raise ApiHttpError(
            status_code=403,
            code="dialog_role_denied",
            message="Dialog role access denied.",
        )

    try:
        result = await service.get_dialog_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            thread_id=thread_id,
            language=actor.language_code,
            role=selected_role,
        )
    except ContactChatError:
        raise ApiHttpError(
            status_code=404,
            code="dialog_not_found",
            message="Dialog not found.",
        ) from None
    except UserDialogsSelectionError:
        raise ApiHttpError(
            status_code=422,
            code="dialog_validation_error",
            message="Dialog data is not valid.",
        ) from None

    detail = result.detail

    return success_envelope(
        data={
            "id": detail.thread_id,
            "contact_request_id": (
                detail.contact_request_id
            ),
            "counterparty_name": (
                detail.specialist_name
                if selected_role == "client"
                else detail.client_name
            ),
            "profession_name": (
                detail.profession_name
            ),
            "request_text": detail.request_text,
            "request_status": (
                detail.request_status
            ),
            "status": detail.thread_status,
            "active_order_id": (
                detail.active_order_id
            ),
            "active_order_status": (
                detail.active_order_status
            ),
            "show_original_button": (
                detail.show_original_button
            ),
            "messages": [
                {
                    "text": item.text,
                    "original_text": (
                        item.original_text
                    ),
                    "is_sent_by_viewer": (
                        item.is_sent_by_viewer
                    ),
                    "is_system": item.is_system,
                    "created_at": item.created_at,
                    "used_translation": (
                        item.used_translation
                    ),
                    "attachment": item.attachment,
                }
                for item in detail.messages
            ],
        },
        meta={},
        request_id=request.state.request_id,
    )



@router.post(
    "/dialogs/{thread_id}/messages",
    response_model=DialogMessageCreatedResponse,
    status_code=201,
)
async def create_dialog_message(
    thread_id: UUID,
    payload: DialogMessageCreateRequest,
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
            service.send_dialog_message_for_user(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                thread_id=thread_id,
                language=actor.language_code,
                text=payload.text or "",
                attachment=payload.attachment,
            )
        )
    except ContactChatThreadNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="dialog_not_found",
            message="Dialog not found.",
        ) from None
    except ContactChatRateLimitError:
        raise ApiHttpError(
            status_code=429,
            code="dialog_message_rate_limited",
            message=(
                "Too many message requests."
            ),
        ) from None
    except ContactChatError:
        raise ApiHttpError(
            status_code=422,
            code="dialog_message_invalid",
            message=(
                "Dialog message is not valid."
            ),
        ) from None

    result = action.result

    return success_envelope(
        data={
            "id": result.message_id,
            "dialog_id": result.thread_id,
            "status": result.thread_status,
            "message_masked": (
                result.message_masked
            ),
            "thread_restricted": (
                result.thread_restricted
            ),
        },
        meta={},
        request_id=request.state.request_id,
    )



@router.post(
    "/dialogs/{thread_id}/finish",
    response_model=DialogFinishResponse,
)
async def finish_dialog(
    thread_id: UUID,
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
        action = await service.finish_dialog_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            thread_id=thread_id,
            language=actor.language_code,
        )
    except ContactChatThreadNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="dialog_not_found",
            message="Dialog not found.",
        ) from None
    except ContactChatError:
        raise ApiHttpError(
            status_code=409,
            code="dialog_finish_conflict",
            message=(
                "Dialog cannot be finished."
            ),
        ) from None

    result = action.result

    return success_envelope(
        data={
            "id": result.thread_id,
            "action": result.action,
            "contact_request_id": (
                result.contact_request_id
            ),
            "requested_for_role": (
                result.requested_for_role
            ),
        },
        meta={},
        request_id=request.state.request_id,
    )
