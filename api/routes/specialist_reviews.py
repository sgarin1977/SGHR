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
    get_specialist_reviews_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    SpecialistReviewListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsSelectionError,
)
from services.specialist_reviews import (
    SpecialistReviewsService,
)


router = APIRouter()

SPECIALIST_REVIEWS_READ_PERMISSION = (
    "specialist.reviews.read"
)
require_specialist_reviews_read = (
    require_permission(
        SPECIALIST_REVIEWS_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/reviews"
    ),
    response_model=(
        SpecialistReviewListResponse
    ),
)
async def list_specialist_reviews(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_reviews_read
        ),
    ],
    service: Annotated[
        SpecialistReviewsService,
        Depends(
            get_specialist_reviews_service
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=10),
    ] = 10,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    page = decode_page_cursor(cursor)

    try:
        action = await (
            service.list_reviews_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                page=page,
                page_size=limit,
                platform="api",
            )
        )
    except (
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
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
    reputation = result.reputation

    return success_envelope(
        data={
            "items": [
                {
                    "id": review.id,
                    "rating": review.rating,
                    "text": review.text,
                    "specialist_reply": (
                        review.specialist_reply
                    ),
                    "created_at": (
                        review.created_at
                    ),
                }
                for review in result.reviews
            ],
            "reputation": (
                {
                    "score": float(
                        reputation.score
                    ),
                    "review_count": (
                        reputation.review_count
                    ),
                }
                if reputation is not None
                else None
            ),
        },
        meta={
            "next_cursor": (
                encode_page_cursor(
                    result.page + 1
                )
                if result.has_next
                else None
            ),
            "has_more": result.has_next,
        },
        request_id=request.state.request_id,
    )
