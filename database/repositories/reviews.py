from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ContactRequest,
    ReputationScore,
    Review,
    ServiceOrder,
    Specialist,
    UserRoleMapping,
)


class ReviewError(Exception):
    pass

REVIEW_MODERATION_ROLES = {
    "moderator",
    "admin",
    "super_admin",
}

class ReviewRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def require_moderator(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
    ) -> set[str]:
        result = await self.session.execute(
            select(UserRoleMapping.role).where(
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.user_id == moderator_user_id,
                UserRoleMapping.status == "active",
                UserRoleMapping.role.in_(
                    REVIEW_MODERATION_ROLES
                ),
            )
        )
        roles = set(result.scalars().all())

        if not roles:
            raise ReviewError(
                "Review moderation access denied."
            )

        return roles

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

    async def get_professional_cabinet_reputation(
        self,
        *,
        tenant_id: UUID,
        professional_cabinet_id: UUID,
    ) -> ReputationScore | None:
        result = await self.session.execute(
            select(ReputationScore).where(
                ReputationScore.tenant_id == tenant_id,
                ReputationScore.target_type
                == "professional_cabinet",
                ReputationScore.target_id
                == professional_cabinet_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_professional_cabinet_reputations(
        self,
        *,
        professional_cabinet_ids: list[UUID],
    ) -> dict[UUID, ReputationScore]:
        cabinet_ids = list(
            set(professional_cabinet_ids)
        )

        if not cabinet_ids:
            return {}

        result = await self.session.execute(
            select(ReputationScore).where(
                ReputationScore.target_type
                == "professional_cabinet",
                ReputationScore.target_id.in_(
                    cabinet_ids
                ),
            )
        )

        reputations = result.scalars().all()

        return {
            reputation.target_id: reputation
            for reputation in reputations
        }

    async def list_public_reviews_for_specialist(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[list[Review], int]:
        normalized_limit = max(1, min(int(limit), 10))
        normalized_offset = max(int(offset), 0)

        filters = [
            Review.tenant_id == tenant_id,
            Review.target_type == "specialist",
            Review.target_id == specialist_id,
            Review.status == "published",
        ]

        if professional_cabinet_id is not None:
            filters.append(
                Review.professional_cabinet_id
                == professional_cabinet_id
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

    async def get_completed_service_order_for_review(
        self,
        *,
        tenant_id: UUID,
        reviewer_user_id: UUID,
        service_order_id: UUID,
    ) -> ServiceOrder | None:
        result = await self.session.execute(
            select(ServiceOrder).where(
                ServiceOrder.id == service_order_id,
                ServiceOrder.tenant_id == tenant_id,
                ServiceOrder.client_user_id == reviewer_user_id,
                ServiceOrder.status == "completed",
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

    async def get_existing_service_order_review(
        self,
        *,
        reviewer_user_id: UUID,
        service_order_id: UUID,
    ) -> Review | None:
        result = await self.session.execute(
            select(Review).where(
                Review.reviewer_user_id == reviewer_user_id,
                Review.context_type == "service_order",
                Review.context_id == service_order_id,
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
            professional_cabinet_id=(
                contact_request.professional_cabinet_id
            ),
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

    async def create_service_order_review(
        self,
        *,
        tenant_id: UUID,
        reviewer_user_id: UUID,
        service_order_id: UUID,
        rating: int,
        text: str | None = None,
    ) -> Review:
        order = await self.get_completed_service_order_for_review(
            tenant_id=tenant_id,
            reviewer_user_id=reviewer_user_id,
            service_order_id=service_order_id,
        )
        if not order:
            raise ReviewError("Only completed service orders can be reviewed.")

        existing = await self.get_existing_service_order_review(
            reviewer_user_id=reviewer_user_id,
            service_order_id=service_order_id,
        )
        if existing:
            raise ReviewError("This service order already has a review.")

        review = Review(
            tenant_id=tenant_id,
            reviewer_user_id=reviewer_user_id,
            professional_cabinet_id=(
                order.professional_cabinet_id
            ),
            service_order_id=order.id,
            target_type="specialist",
            target_id=order.specialist_id,
            context_type="service_order",
            context_id=order.id,
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

        await self.session.flush()
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
        tenant_id: UUID,
        moderator_user_id: UUID,
        limit: int = 6,
        offset: int = 0,
    ) -> list[Review]:
        await self.require_moderator(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
        )

        normalized_limit = max(1, min(int(limit), 20))
        normalized_offset = max(0, int(offset))

        result = await self.session.execute(
            select(Review)
            .where(
                Review.tenant_id == tenant_id,
                Review.status == "pending_moderation",
            )
            .order_by(
                Review.created_at.asc(),
                Review.id.asc(),
            )
            .offset(normalized_offset)
            .limit(normalized_limit)
        )

        return list(result.scalars().all())
    
    async def get_pending_review_for_moderation(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        review_id: UUID,
    ) -> Review:
        await self.require_moderator(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
        )

        result = await self.session.execute(
            select(Review).where(
                Review.id == review_id,
                Review.tenant_id == tenant_id,
                Review.status == "pending_moderation",
            )
        )
        review = result.scalar_one_or_none()

        if not review:
            raise ReviewError(
                "Review is no longer pending moderation."
            )

        return review

    async def get_review_target_name(
        self,
        *,
        tenant_id: UUID,
        target_type: str,
        target_id: UUID,
    ) -> str | None:
        if target_type != "specialist":
            return None

        result = await self.session.execute(
            select(Specialist.display_name).where(
                Specialist.id == target_id,
                Specialist.tenant_id == tenant_id,
            )
        )

        return result.scalar_one_or_none()

    async def set_review_status(
        self,
        *,
        tenant_id: UUID,
        moderator_user_id: UUID,
        review_id: UUID,
        status: str,
    ) -> tuple[Review, str]:
        await self.require_moderator(
            tenant_id=tenant_id,
            moderator_user_id=moderator_user_id,
        )

        if status not in {"published", "hidden"}:
            raise ReviewError(
                "Unsupported review moderation status."
            )

        result = await self.session.execute(
            select(Review)
            .where(
                Review.id == review_id,
                Review.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        review = result.scalar_one_or_none()

        if not review:
            raise ReviewError("Review not found.")

        if review.status != "pending_moderation":
            raise ReviewError(
                "Review is no longer pending moderation."
            )

        before_status = review.status
        review.status = status
        review.updated_at = datetime.utcnow()

        await self.session.flush()

        return review, before_status
    
    async def add_specialist_reply(
        self,
        *,
        specialist_user_id: UUID,
        review_id: UUID,
        reply: str,
    ) -> Review:
        raise ReviewError("Review replies are disabled for controlled Beta.")

    async def recalculate_professional_cabinet_reputation(
        self,
        *,
        tenant_id: UUID,
        professional_cabinet_id: UUID,
    ) -> ReputationScore:
        aggregate = await self.session.execute(
            select(
                func.coalesce(
                    func.avg(Review.rating),
                    0,
                ),
                func.count(Review.id),
            ).where(
                Review.tenant_id == tenant_id,
                Review.professional_cabinet_id
                == professional_cabinet_id,
                Review.status == "published",
            )
        )
        score, review_count = aggregate.one()
        score = float(score or 0)
        review_count = int(review_count or 0)

        reputation = (
            await self.session.execute(
                select(ReputationScore).where(
                    ReputationScore.tenant_id
                    == tenant_id,
                    ReputationScore.target_type
                    == "professional_cabinet",
                    ReputationScore.target_id
                    == professional_cabinet_id,
                )
            )
        ).scalar_one_or_none()

        if not reputation:
            reputation = ReputationScore(
                tenant_id=tenant_id,
                target_type=(
                    "professional_cabinet"
                ),
                target_id=professional_cabinet_id,
            )
            self.session.add(reputation)

        reputation.score = score
        reputation.review_count = review_count
        reputation.calculated_at = datetime.utcnow()

        await self.session.flush()
        return reputation

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
