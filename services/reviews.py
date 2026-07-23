from dataclasses import dataclass
from uuid import UUID

from database.models import ReputationScore, Review
from database.repositories.reviews import ReviewError, ReviewRepository
from database.repositories.event import EventRepository
from database.repositories.moderation import ModerationRepository

class ReviewServiceError(Exception):
    pass


@dataclass(frozen=True)
class ReviewResult:
    review: Review
    reputation: ReputationScore | None = None

@dataclass(frozen=True)
class ReviewModerationCard:
    review: Review
    author_label: str
    target_name: str | None

@dataclass(frozen=True)
class PublicReviewPage:
    reviews: list[Review]
    reputation: ReputationScore | None
    total_count: int
    page: int
    page_size: int
    has_previous: bool
    has_next: bool

class ReviewService:
    def __init__(self, repository: ReviewRepository):
        self.repository = repository
        self.events = EventRepository(repository.session)

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

    async def create_service_order_review(
        self,
        *,
        tenant_id: UUID,
        reviewer_user_id: UUID,
        service_order_id: UUID,
        rating: int,
        text: str | None = None,
    ) -> Review:
        normalized_rating = self._normalize_rating(rating)
        normalized_text = self._normalize_text(text)

        try:
            review = await self.repository.create_service_order_review(
                tenant_id=tenant_id,
                reviewer_user_id=reviewer_user_id,
                service_order_id=service_order_id,
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
            review = await self.repository.publish_review(
                review_id=review_id
            )

            if review.professional_cabinet_id:
                await (
                    self.repository
                    .recalculate_professional_cabinet_reputation(
                        tenant_id=review.tenant_id,
                        professional_cabinet_id=(
                            review.professional_cabinet_id
                        ),
                    )
                )

            reputation = await (
                self.repository
                .recalculate_reputation(
                    tenant_id=review.tenant_id,
                    target_type=review.target_type,
                    target_id=review.target_id,
                )
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
        tenant_id: UUID,
        moderator_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
    ) -> list[Review]:
        normalized_page = max(int(page), 0)
        normalized_page_size = max(
            1,
            min(int(page_size), 10),
        )

        try:
            return await self.repository.list_pending_reviews(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                limit=normalized_page_size + 1,
                offset=normalized_page * normalized_page_size,
            )
        except ReviewError as exc:
            raise ReviewServiceError(str(exc)) from exc

    async def get_pending_review_for_moderation(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        review_id: UUID,
    ) -> ReviewModerationCard:
        try:
            review = await self.repository.get_pending_review_for_moderation(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                review_id=review_id,
            )

            target_name = await self.repository.get_review_target_name(
                tenant_id=tenant_id,
                target_type=review.target_type,
                target_id=review.target_id,
            )

            author_token = str(
                review.reviewer_user_id
            ).replace("-", "")[:8]

            return ReviewModerationCard(
                review=review,
                author_label=f"user-{author_token}",
                target_name=target_name,
            )

        except ReviewError as exc:
            raise ReviewServiceError(str(exc)) from exc
        
    async def moderate_review(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        review_id: UUID,
        status: str,
        reason: str,
    ) -> ReviewResult:
        normalized_reason = self._normalize_reason(reason)

        try:
            review, before_status = await self.repository.set_review_status(
                tenant_id=tenant_id,
                moderator_user_id=moderator_user_id,
                review_id=review_id,
                status=status,
            )

            if review.professional_cabinet_id:
                await (
                    self.repository
                    .recalculate_professional_cabinet_reputation(
                        tenant_id=review.tenant_id,
                        professional_cabinet_id=(
                            review.professional_cabinet_id
                        ),
                    )
                )

            reputation = await (
                self.repository
                .recalculate_reputation(
                    tenant_id=review.tenant_id,
                    target_type=review.target_type,
                    target_id=review.target_id,
                )
            )

            decision = "shown" if status == "published" else "hidden"
            moderation_repository = ModerationRepository(
                self.repository.session
            )

            await moderation_repository.log_admin_action(
                admin_user_id=moderator_user_id,
                tenant_id=tenant_id,
                action_type="moderate_review",
                target_type="review",
                target_id=review.id,
                before_state={"status": before_status},
                after_state={"status": review.status},
                reason=normalized_reason,
            )

            await moderation_repository.log_event(
                tenant_id=tenant_id,
                user_id=moderator_user_id,
                event_type="review_moderated",
                entity_type="review",
                entity_id=review.id,
                payload={
                    "decision": decision,
                    "reason": normalized_reason,
                    "before_status": before_status,
                    "after_status": review.status,
                },
            )

            await self.repository.session.commit()

            return ReviewResult(
                review=review,
                reputation=reputation,
            )

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
        raise ReviewServiceError("Review replies are disabled for controlled Beta.")

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

    async def list_public_reviews_for_specialist(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
        page: int = 0,
        page_size: int = 5,
    ) -> PublicReviewPage:
        normalized_page = max(int(page), 0)
        normalized_page_size = max(1, min(int(page_size), 10))
        offset = normalized_page * normalized_page_size

        reviews, total_count = await self.repository.list_public_reviews_for_specialist(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                professional_cabinet_id
            ),
            limit=normalized_page_size,
            offset=offset,
        )
        if professional_cabinet_id is not None:
            reputation = await (
                self.repository
                .get_professional_cabinet_reputation(
                    tenant_id=tenant_id,
                    professional_cabinet_id=(
                        professional_cabinet_id
                    ),
                )
            )
        else:
            reputation = await (
                self.repository
                .get_specialist_reputation(
                    tenant_id=tenant_id,
                    specialist_id=specialist_id,
                )
            )

        return PublicReviewPage(
            reviews=reviews,
            reputation=reputation,
            total_count=total_count,
            page=normalized_page,
            page_size=normalized_page_size,
            has_previous=normalized_page > 0,
            has_next=offset + len(reviews) < total_count,
        )
    
    async def list_public_reviews_for_viewer(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
        viewer_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
        source: str | None = None,
    ) -> PublicReviewPage:
        review_page = (
            await self.list_public_reviews_for_specialist(
                tenant_id=tenant_id,
                specialist_id=specialist_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                page=page,
                page_size=page_size,
            )
        )

        payload = {
            "page": review_page.page,
            "count": len(review_page.reviews),
            "total_count": review_page.total_count,
        }

        if source:
            payload["source"] = source

        try:
            await self.events.create_event(
                tenant_id=tenant_id,
                user_id=viewer_user_id,
                event_type="reviews_viewed",
                entity_type=(
                    "professional_cabinet"
                    if professional_cabinet_id
                    else "specialist"
                ),
                entity_id=(
                    professional_cabinet_id
                    or specialist_id
                ),
                payload=payload,
                platform="telegram",
            )
            await self.repository.session.commit()

        except Exception as exc:
            await self.repository.session.rollback()
            raise ReviewServiceError(
                "Unable to record reviews view."
            ) from exc

        return review_page