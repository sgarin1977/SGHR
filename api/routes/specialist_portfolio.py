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
    get_specialist_portfolio_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    SpecialistPortfolioListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.specialist_portfolio import (
    SpecialistPortfolioAccessError,
    SpecialistPortfolioService,
)


router = APIRouter()

SPECIALIST_PORTFOLIO_READ_PERMISSION = (
    "specialist.portfolio.read"
)
require_specialist_portfolio_read = (
    require_permission(
        SPECIALIST_PORTFOLIO_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/portfolio"
    ),
    response_model=(
        SpecialistPortfolioListResponse
    ),
)
async def list_specialist_portfolio(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_portfolio_read
        ),
    ],
    service: Annotated[
        SpecialistPortfolioService,
        Depends(
            get_specialist_portfolio_service
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 25,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    page = decode_page_cursor(cursor)

    try:
        action = await (
            service.list_portfolio_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                page=page,
                platform="api",
            )
        )
    except SpecialistPortfolioAccessError:
        raise ApiHttpError(
            status_code=404,
            code="cabinet_not_found",
            message=(
                "Professional cabinet "
                "was not found."
            ),
        ) from None

    all_items = tuple(action.result)
    start = page * limit
    items = all_items[
        start:start + limit
    ]
    has_more = len(all_items) > (
        start + limit
    )
    next_cursor = (
        encode_page_cursor(page + 1)
        if has_more
        else None
    )

    return success_envelope(
        data=[
            {
                "id": view.item.id,
                "title": view.item.title,
                "description": (
                    view.item.description
                ),
                "file_type": (
                    view.storage_object.file_type
                ),
                "mime_type": (
                    view.storage_object.mime_type
                ),
                "size_bytes": (
                    view.storage_object.size_bytes
                ),
                "url": view.signed_url,
                "status": view.item.status,
                "created_at": (
                    view.item.created_at
                ),
            }
            for view in items
        ],
        meta={
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
        request_id=request.state.request_id,
    )
