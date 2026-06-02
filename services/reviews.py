from dataclasses import dataclass
from uuid import UUID

from database.models import ReputationScore, Review
from database.repositories.reviews import ReviewError, ReviewRepository


class ReviewServiceError(Exception):
    pass


@dataclass(frozen=True)
class ReviewResult:
    review: Review
    reputation: ReputationScore | None = None


class ReviewService:
    def __init__(self, repository: ReviewRepository):
        self.repository = repository

    async def create_contact_review(
        self,
        *,
        tenant_id: UUID,
        reviewer_user_id: UUID,
        contact_request_id: UUID,
        rating: int,
        text: str | None = None,
    ) -> Review:
        normalized_rating = self._normalize_rating(rating)
        normalized_text = self._normalize_text(text)

        try:
            review = await self.repository.create_contact_review(
                tenant_id=tenant_id,
                reviewer_user_id=reviewer_user_id,
                contact_request_id=contact_request_id,
                rating=normalized_rating,
                text=normalized_text,
            )
            await self.repository.session.commit()
            return review
        except ReviewError as exc:
            await self.repository.session.rollback()
            raise ReviewServiceError(str(exc)) from exc

    async def publish_review(self, *, review_id: UUID) -> ReviewResult:
        try:
            review = await self.repository.publish_review(review_id=review_id)
            reputation = await self.repository.recalculate_reputation(
                tenant_id=review.tenant_id,
                target_type=review.target_type,
                target_id=review.target_id,
            )
            await self.repository.session.commit()
            return ReviewResult(review=review, reputation=reputation)
        except ReviewError as exc:
            await self.repository.session.rollback()
            raise ReviewServiceError(str(exc)) from exc

    async def reject_review(self, *, review_id: UUID) -> Review:
        try:
            review = await self.repository.reject_review(review_id=review_id)
            await self.repository.session.commit()
            return review
        except ReviewError as exc:
            await self.repository.session.rollback()
            raise ReviewServiceError(str(exc)) from exc
    async def list_pending_reviews(
        self,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Review]:
        return await self.repository.list_pending_reviews(
            limit=limit,
            offset=offset,
        )

    async def moderate_review(
        self,
        *,
        review_id: UUID,
        status: str,
        reason: str,
    ) -> ReviewResult:
        normalized_reason = self._normalize_reason(reason)

        try:
            review = await self.repository.set_review_status(
                review_id=review_id,
                status=status,
            )
            reputation = None
            if review.status in {"published", "hidden"}:
                reputation = await self.repository.recalculate_reputation(
                    tenant_id=review.tenant_id,
                    target_type=review.target_type,
                    target_id=review.target_id,
                )

            await self.repository.session.commit()
            return ReviewResult(review=review, reputation=reputation)
        except ReviewError as exc:
            await self.repository.session.rollback()
            raise ReviewServiceError(str(exc)) from exc

    async def add_specialist_reply(
        self,
        *,
        specialist_user_id: UUID,
        review_id: UUID,
        reply: str,
    ) -> Review:
        normalized_reply = self._normalize_reply(reply)

        try:
            review = await self.repository.add_specialist_reply(
                specialist_user_id=specialist_user_id,
                review_id=review_id,
                reply=normalized_reply,
            )
            await self.repository.session.commit()
            return review
        except ReviewError as exc:
            await self.repository.session.rollback()
            raise ReviewServiceError(str(exc)) from exc

    def _normalize_rating(self, rating: int) -> int:
        try:
            normalized = int(rating)
        except (TypeError, ValueError) as exc:
            raise ReviewServiceError("Rating must be between 1 and 5.") from exc

        if normalized < 1 or normalized > 5:
            raise ReviewServiceError("Rating must be between 1 and 5.")

        return normalized

    def _normalize_text(self, text: str | None) -> str | None:
        normalized = (text or "").strip()
        if not normalized:
            return None

        if len(normalized) > 1000:
            raise ReviewServiceError("Review text is too long.")

        return normalized

    def _normalize_reason(self, reason: str | None) -> str:
        normalized = (reason or "").strip()
        if len(normalized) < 3:
            raise ReviewServiceError("Reason is required.")
        if len(normalized) > 500:
            raise ReviewServiceError("Reason is too long.")
        return normalized

    def _normalize_reply(self, reply: str | None) -> str:
        normalized = (reply or "").strip()
        if len(normalized) < 2:
            raise ReviewServiceError("Reply is too short.")
        if len(normalized) > 1000:
            raise ReviewServiceError("Reply is too long.")
        return normalized
