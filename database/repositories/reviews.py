from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ContactRequest, ReputationScore, Review, Specialist


class ReviewError(Exception):
    pass


class ReviewRepository:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def get_specialist_reputation(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
    ) -> ReputationScore | None:
        result = await self.session.execute(
            select(ReputationScore).where(
                ReputationScore.tenant_id == tenant_id,
                ReputationScore.target_type == "specialist",
                ReputationScore.target_id == specialist_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_public_reviews_for_specialist(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[list[Review], int]:
        normalized_limit = max(1, min(int(limit), 10))
        normalized_offset = max(int(offset), 0)

        filters = (
            Review.tenant_id == tenant_id,
            Review.target_type == "specialist",
            Review.target_id == specialist_id,
            Review.status == "published",
        )

        reviews_result = await self.session.execute(
            select(Review)
            .where(*filters)
            .order_by(Review.created_at.desc())
            .offset(normalized_offset)
            .limit(normalized_limit)
        )
        count_result = await self.session.execute(
            select(func.count(Review.id)).where(*filters)
        )

        return list(reviews_result.scalars().all()), int(count_result.scalar_one() or 0)

    async def get_completed_contact_request_for_review(
        self,
        *,
        tenant_id: UUID,
        reviewer_user_id: UUID,
        contact_request_id: UUID,
    ) -> ContactRequest | None:
        result = await self.session.execute(
            select(ContactRequest).where(
                ContactRequest.id == contact_request_id,
                ContactRequest.tenant_id == tenant_id,
                ContactRequest.from_user_id == reviewer_user_id,
                ContactRequest.status == "completed",
            )
        )
        return result.scalar_one_or_none()

    async def get_existing_contact_review(
        self,
        *,
        reviewer_user_id: UUID,
        contact_request_id: UUID,
    ) -> Review | None:
        result = await self.session.execute(
            select(Review).where(
                Review.reviewer_user_id == reviewer_user_id,
                Review.context_type == "contact_request",
                Review.context_id == contact_request_id,
                Review.status != "deleted",
            )
        )
        return result.scalar_one_or_none()

    async def create_contact_review(
        self,
        *,
        tenant_id: UUID,
        reviewer_user_id: UUID,
        contact_request_id: UUID,
        rating: int,
        text: str | None = None,
    ) -> Review:
        contact_request = await self.get_completed_contact_request_for_review(
            tenant_id=tenant_id,
            reviewer_user_id=reviewer_user_id,
            contact_request_id=contact_request_id,
        )
        if not contact_request:
            raise ReviewError("Only completed contact requests can be reviewed.")

        existing = await self.get_existing_contact_review(
            reviewer_user_id=reviewer_user_id,
            contact_request_id=contact_request_id,
        )
        if existing:
            raise ReviewError("This contact request already has a review.")

        review = Review(
            tenant_id=tenant_id,
            reviewer_user_id=reviewer_user_id,
            target_type="specialist",
            target_id=contact_request.specialist_id,
            context_type="contact_request",
            context_id=contact_request.id,
            rating=rating,
            text=(text or "").strip() or None,
            status="pending_moderation",
        )
        self.session.add(review)
        await self.session.flush()
        return review

    async def publish_review(
        self,
        *,
        review_id: UUID,
    ) -> Review:
        review = await self.session.get(Review, review_id)
        if not review:
            raise ReviewError("Review not found.")

        review.status = "published"
        review.updated_at = datetime.utcnow()

        if review.context_type == "contact_request" and review.context_id:
            contact_request = await self.session.get(ContactRequest, review.context_id)
            if contact_request:
                contact_request.status = "reviewed"
                contact_request.updated_at = datetime.utcnow()

        await self.session.flush()
        await self.recalculate_reputation(
            tenant_id=review.tenant_id,
            target_type=review.target_type,
            target_id=review.target_id,
        )
        return review

    async def reject_review(
        self,
        *,
        review_id: UUID,
    ) -> Review:
        review = await self.session.get(Review, review_id)
        if not review:
            raise ReviewError("Review not found.")

        review.status = "rejected"
        review.updated_at = datetime.utcnow()

        await self.session.flush()
        return review

    async def list_pending_reviews(
        self,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Review]:
        result = await self.session.execute(
            select(Review)
            .where(Review.status == "pending_moderation")
            .order_by(Review.created_at.asc())
            .offset(max(int(offset), 0))
            .limit(max(1, min(int(limit), 20)))
        )
        return list(result.scalars().all())

    async def hide_review(
        self,
        *,
        review_id: UUID,
    ) -> Review:
        review = await self.session.get(Review, review_id)
        if not review:
            raise ReviewError("Review not found.")

        review.status = "hidden"
        review.updated_at = datetime.utcnow()

        await self.session.flush()
        await self.recalculate_reputation(
            tenant_id=review.tenant_id,
            target_type=review.target_type,
            target_id=review.target_id,
        )
        return review

    async def set_review_status(
        self,
        *,
        review_id: UUID,
        status: str,
    ) -> Review:
        if status not in {"published", "rejected", "hidden"}:
            raise ReviewError("Unsupported review moderation status.")

        if status == "published":
            return await self.publish_review(review_id=review_id)
        if status == "rejected":
            return await self.reject_review(review_id=review_id)

        return await self.hide_review(review_id=review_id)

    async def add_specialist_reply(
        self,
        *,
        specialist_user_id: UUID,
        review_id: UUID,
        reply: str,
    ) -> Review:
        review = await self.session.get(Review, review_id)
        if not review:
            raise ReviewError("Review not found.")

        specialist = await self.session.get(Specialist, review.target_id)
        if not specialist or specialist.user_id != specialist_user_id:
            raise ReviewError("Only the reviewed specialist can reply.")

        review.specialist_reply = reply.strip()
        review.updated_at = datetime.utcnow()

        await self.session.flush()
        return review

    async def recalculate_reputation(
        self,
        *,
        tenant_id: UUID,
        target_type: str,
        target_id: UUID,
    ) -> ReputationScore:
        aggregate = await self.session.execute(
            select(
                func.coalesce(func.avg(Review.rating), 0),
                func.count(Review.id),
            ).where(
                Review.tenant_id == tenant_id,
                Review.target_type == target_type,
                Review.target_id == target_id,
                Review.status == "published",
            )
        )
        score, review_count = aggregate.one()
        score = float(score or 0)
        review_count = int(review_count or 0)

        reputation = (
            await self.session.execute(
                select(ReputationScore).where(
                    ReputationScore.target_type == target_type,
                    ReputationScore.target_id == target_id,
                )
            )
        ).scalar_one_or_none()

        if not reputation:
            reputation = ReputationScore(
                tenant_id=tenant_id,
                target_type=target_type,
                target_id=target_id,
            )
            self.session.add(reputation)

        reputation.score = score
        reputation.review_count = review_count
        reputation.calculated_at = datetime.utcnow()

        if target_type == "specialist":
            specialist = await self.session.get(Specialist, target_id)
            if specialist:
                specialist.rating = score
                specialist.reviews_count = review_count
                specialist.updated_at = datetime.utcnow()

        await self.session.flush()
        return reputation
