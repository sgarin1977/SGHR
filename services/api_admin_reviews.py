from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.api_admin import (
    AdminApiRepository,
)
from database.repositories.reviews import (
    ReviewRepository,
)
from database.repositories.webhooks import (
    WebhookRepository,
)
from services.api_admin_access import (
    build_api_admin_scope_context,
)
from services.api_identity import (
    ApiActorContext,
)
from services.reviews import ReviewService
from services.reviews import ReviewServiceError
from services.webhooks import (
    WebhookEventPublisher,
)


class ApiAdminReviewNotFoundError(
    LookupError
):
    pass


@dataclass(frozen=True)
class ApiAdminReviewView:
    id: UUID
    reviewer_user_id: UUID
    professional_cabinet_id: UUID
    specialist_id: UUID
    service_order_id: UUID | None
    context_type: str | None
    context_id: UUID | None
    rating: int
    text: str | None
    status: str
    published_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class ApiAdminReviewPage:
    items: tuple[
        ApiAdminReviewView,
        ...,
    ]
    page: int
    has_next: bool


class ApiAdminReviewModerationError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class ApiAdminReviewModerationResult:
    review_id: UUID
    status: str


class ApiAdminReviewsService:
    def __init__(
        self,
        *,
        repository: AdminApiRepository,
        reviews: ReviewService | None = None,
    ) -> None:
        self.repository = repository
        self.reviews = reviews

    @staticmethod
    def _to_review_view(
        row,
    ) -> ApiAdminReviewView:
        review = row[0]
        specialist = row[2]

        return ApiAdminReviewView(
            id=review.id,
            reviewer_user_id=(
                review.reviewer_user_id
            ),
            professional_cabinet_id=(
                review
                .professional_cabinet_id
            ),
            specialist_id=specialist.id,
            service_order_id=(
                review.service_order_id
            ),
            context_type=review.context_type,
            context_id=review.context_id,
            rating=int(review.rating),
            text=review.text,
            status=review.status,
            published_at=(
                review.published_at
            ),
            created_at=review.created_at,
        )

    async def list_reviews(
        self,
        *,
        actor: ApiActorContext,
        status: str | None,
        page: int = 0,
        page_size: int = 20,
    ) -> ApiAdminReviewPage:
        normalized_page = max(
            0,
            int(page),
        )
        normalized_size = max(
            1,
            min(int(page_size), 100),
        )

        scope_context = (
            build_api_admin_scope_context(
                actor
            )
        )

        rows = await self.repository.list_reviews(
            scope_context=scope_context,
            status=status,
            limit=normalized_size + 1,
            offset=(
                normalized_page
                * normalized_size
            ),
        )

        return ApiAdminReviewPage(
            items=tuple(
                self._to_review_view(row)
                for row in rows[
                    :normalized_size
                ]
            ),
            page=normalized_page,
            has_next=(
                len(rows) > normalized_size
            ),
        )



    async def moderate_review(
        self,
        *,
        actor: ApiActorContext,
        review_id: UUID,
        status: str,
        reason: str,
    ) -> ApiAdminReviewModerationResult:
        normalized_status = (
            status.strip().lower()
        )
        if normalized_status not in {
            "published",
            "hidden",
        }:
            raise ApiAdminReviewModerationError(
                "Unsupported review status."
            )

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ApiAdminReviewModerationError(
                "Moderation reason is required."
            )

        if self.reviews is None:
            raise ApiAdminReviewModerationError(
                "Review moderation is unavailable."
            )

        try:
            result = (
                await self.reviews.moderate_review(
                    tenant_id=actor.tenant_id,
                    moderator_user_id=(
                        actor.user_id
                    ),
                    review_id=review_id,
                    status=normalized_status,
                    reason=normalized_reason,
                )
            )
        except ReviewServiceError as exc:
            raise ApiAdminReviewModerationError(
                "Review moderation failed."
            ) from exc

        return ApiAdminReviewModerationResult(
            review_id=result.review.id,
            status=result.review.status,
        )


def build_api_admin_reviews_service(
    session: AsyncSession,
) -> ApiAdminReviewsService:
    return ApiAdminReviewsService(
        repository=AdminApiRepository(
            session
        ),
        reviews=ReviewService(
            ReviewRepository(session),
            webhook_publisher=(
                WebhookEventPublisher(
                    repository=WebhookRepository(
                        session
                    )
                )
            ),
        ),
    )
