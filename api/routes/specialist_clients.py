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
    get_specialist_clients_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    SpecialistClientListResponse,
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
from services.specialist_clients import (
    SpecialistClientsService,
)


router = APIRouter()

SPECIALIST_CLIENTS_READ_PERMISSION = (
    "specialist.clients.read"
)
require_specialist_clients_read = (
    require_permission(
        SPECIALIST_CLIENTS_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/clients"
    ),
    response_model=(
        SpecialistClientListResponse
    ),
)
async def list_specialist_clients(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_clients_read
        ),
    ],
    service: Annotated[
        SpecialistClientsService,
        Depends(
            get_specialist_clients_service
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
            service.list_clients_for_user(
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
                "id": item.client_id,
                "display_name": (
                    item.display_name
                ),
                "requests_count": (
                    item.requests_count
                ),
                "first_request_at": (
                    item.first_request_at
                ),
                "last_request_at": (
                    item.last_request_at
                ),
                "last_request_status": (
                    item.last_request_status
                ),
                "latest_thread_id": (
                    item.latest_thread_id
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
