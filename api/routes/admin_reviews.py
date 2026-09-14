from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import require_admin_any_permission
from api.dependencies import (
    get_api_admin_reviews_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import AdminReviewListResponse
from api.schemas import (
    AdminReviewModerationRequest,
    AdminReviewModerationResponse,
)
from services.api_admin_reviews import (
    ApiAdminReviewsService,
)
from services.api_admin_reviews import (
    ApiAdminReviewModerationError,
)
from services.api_identity import ApiActorContext


from api.auth import require_admin_api_rate_limit


router = APIRouter(
    dependencies=[
        Depends(
            require_admin_api_rate_limit()
        ),
    ],
)

require_admin_reviews_read = (
    require_admin_any_permission(
        "moderation.reviews.view",
    )
)


require_admin_reviews_moderate = (
    require_admin_any_permission(
        "moderation.reviews.hide",
    )
)


@router.get(
    "/admin/reviews",
    response_model=AdminReviewListResponse,
)
async def list_admin_reviews(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_admin_reviews_read),
    ],
    service: Annotated[
        ApiAdminReviewsService,
        Depends(get_api_admin_reviews_service),
    ],
    status: Annotated[
        str | None,
        Query(min_length=1, max_length=64),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    page = await service.list_reviews(
        actor=actor,
        status=status,
        page=decode_page_cursor(cursor),
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "reviewer_user_id": (
                    item.reviewer_user_id
                ),
                "professional_cabinet_id": (
                    item.professional_cabinet_id
                ),
                "specialist_id": (
                    item.specialist_id
                ),
                "service_order_id": (
                    item.service_order_id
                ),
                "context_type": (
                    item.context_type
                ),
                "context_id": item.context_id,
                "rating": item.rating,
                "text": item.text,
                "status": item.status,
                "published_at": (
                    item.published_at
                ),
                "created_at": item.created_at,
            }
            for item in page.items
        ],
        meta={
            "next_cursor": (
                encode_page_cursor(page.page + 1)
                if page.has_next
                else None
            ),
            "has_more": page.has_next,
        },
        request_id=request.state.request_id,
    )



async def _moderate_admin_review(
    *,
    actor: ApiActorContext,
    service: ApiAdminReviewsService,
    review_id: UUID,
    status: str,
    reason: str,
    request: Request,
):
    try:
        result = await service.moderate_review(
            actor=actor,
            review_id=review_id,
            status=status,
            reason=reason,
        )
    except ApiAdminReviewModerationError:
        raise ApiHttpError(
            status_code=422,
            code="review_moderation_failed",
            message=(
                "Review moderation could not "
                "be completed."
            ),
        ) from None

    return success_envelope(
        data={
            "review_id": result.review_id,
            "status": result.status,
        },
        request_id=request.state.request_id,
    )


@router.post(
    "/admin/reviews/{review_id}/publish",
    response_model=(
        AdminReviewModerationResponse
    ),
)
async def publish_admin_review(
    review_id: UUID,
    payload: AdminReviewModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_reviews_moderate
        ),
    ],
    service: Annotated[
        ApiAdminReviewsService,
        Depends(
            get_api_admin_reviews_service
        ),
    ],
):
    return await _moderate_admin_review(
        actor=actor,
        service=service,
        review_id=review_id,
        status="published",
        reason=payload.reason,
        request=request,
    )


@router.post(
    "/admin/reviews/{review_id}/hide",
    response_model=(
        AdminReviewModerationResponse
    ),
)
async def hide_admin_review(
    review_id: UUID,
    payload: AdminReviewModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_reviews_moderate
        ),
    ],
    service: Annotated[
        ApiAdminReviewsService,
        Depends(
            get_api_admin_reviews_service
        ),
    ],
):
    return await _moderate_admin_review(
        actor=actor,
        service=service,
        review_id=review_id,
        status="hidden",
        reason=payload.reason,
        request=request,
    )
