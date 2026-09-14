from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Review
from database.repositories.reviews import (
    ReviewRepository,
)
from database.repositories.webhooks import (
    WebhookRepository,
)
from services.reviews import (
    ReviewService,
    ReviewServiceError,
)
from services.webhooks import (
    WebhookEventPublisher,
)


class UserReviewsValidationError(ValueError):
    pass


class UserReviewsCreateError(Exception):
    pass


@dataclass(frozen=True)
class UserReviewCreateAction:
    review: Review


class UserReviewsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        reviews: ReviewService | None = None,
    ):
        self.session = session
        self.reviews = (
            reviews
            or ReviewService(
                ReviewRepository(session),
                webhook_publisher=(
                    WebhookEventPublisher(
                        repository=WebhookRepository(
                            session
                        )
                    )
                ),
            )
        )

    async def create_review_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        context_type: Literal[
            "contact_request",
            "service_order",
        ],
        context_id: UUID,
        rating: int,
        text: str | None,
    ) -> UserReviewCreateAction:
        try:
            if context_type == "contact_request":
                review = await (
                    self.reviews
                    .create_contact_review(
                        tenant_id=tenant_id,
                        reviewer_user_id=user_id,
                        contact_request_id=context_id,
                        rating=rating,
                        text=text,
                    )
                )
            elif context_type == "service_order":
                review = await (
                    self.reviews
                    .create_service_order_review(
                        tenant_id=tenant_id,
                        reviewer_user_id=user_id,
                        service_order_id=context_id,
                        rating=rating,
                        text=text,
                    )
                )
            else:
                raise UserReviewsValidationError(
                    "Review context is not valid."
                )
        except ReviewServiceError as exc:
            raise UserReviewsCreateError(
                "Review cannot be created."
            ) from exc

        return UserReviewCreateAction(
            review=review,
        )
