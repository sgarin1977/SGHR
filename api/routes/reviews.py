from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Request,
    status,
)

from api.auth import get_current_actor
from api.dependencies import (
    get_user_reviews_service,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    ReviewCreateRequest,
    ReviewCreatedResponse,
)
from services.api_identity import ApiActorContext
from services.user_reviews import (
    UserReviewsCreateError,
    UserReviewsService,
)


router = APIRouter()


@router.post(
    "/reviews",
    response_model=ReviewCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_review(
    payload: ReviewCreateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        UserReviewsService,
        Depends(get_user_reviews_service),
    ],
):
    try:
        action = await service.create_review_for_user(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            context_type=payload.context_type,
            context_id=payload.context_id,
            rating=payload.rating,
            text=payload.text,
        )
    except UserReviewsCreateError as exc:
        raise ApiHttpError(
            status_code=409,
            code="review_conflict",
            message="Review cannot be created.",
        ) from exc
    review = action.review

    return success_envelope(
        data={
            "id": review.id,
            "context_type": review.context_type,
            "context_id": review.context_id,
            "rating": review.rating,
            "text": review.text,
            "status": review.status,
            "created_at": review.created_at,
        },
        meta={},
        request_id=request.state.request_id,
    )
