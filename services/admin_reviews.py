from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from database.repositories.reviews import (
    ReviewRepository,
)
from services.moderation import ModerationService
from services.reviews import ReviewService
from services.user import UserService


class AdminReviewsAccessError(PermissionError):
    pass


class AdminReviewsDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class AdminReviewsActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminReviewsAction:
    actor: AdminReviewsActor
    result: object


class AdminReviewsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: ModerationService | None = None,
        reviews: ReviewService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )
        self.reviews = (
            reviews
            or ReviewService(
                ReviewRepository(session)
            )
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
        required_roles: set[str],
    ) -> AdminReviewsActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminReviewsAccessError(
                "Review moderation access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(required_roles):
            raise AdminReviewsAccessError(
                "Review moderation access denied."
            )

        return AdminReviewsActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_moderator_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminReviewsActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={
                "admin",
                "moderator",
                "super_admin",
            },
        )

    async def require_super_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminReviewsActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"super_admin"},
        )

    async def require_impersonated_moderator(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
    ) -> AdminReviewsActor:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )

        effective_roles = (
            await self.moderation.get_admin_roles(
                effective_moderator_user_id,
                tenant_id=actor.tenant_id,
            )
        )

        if not effective_roles.intersection(
            {"admin", "moderator"}
        ):
            raise AdminReviewsAccessError(
                "Impersonated review "
                "moderation access denied."
            )

        return actor

    async def list_pending_reviews(
        self,
        *,
        platform_user_id: int | str,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await self.reviews.list_pending_reviews(
            tenant_id=actor.tenant_id,
            moderator_user_id=actor.user_id,
            page=page,
            page_size=page_size,
        )

    async def get_pending_review(
        self,
        *,
        platform_user_id: int | str,
        review_id: UUID,
        language: str = "ru",
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.reviews
            .get_pending_review_for_moderation(
                tenant_id=actor.tenant_id,
                moderator_user_id=actor.user_id,
                review_id=review_id,
                language=language,
            )
        )

    async def moderate_review(
        self,
        *,
        platform_user_id: int | str,
        review_id: UUID,
        status: str,
        reason: str,
    ) -> AdminReviewsAction:
        if status not in {
            "published",
            "hidden",
        }:
            raise AdminReviewsDecisionError(
                "Unsupported review decision."
            )

        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = await self.reviews.moderate_review(
            tenant_id=actor.tenant_id,
            moderator_user_id=actor.user_id,
            review_id=review_id,
            status=status,
            reason=reason,
        )

        return AdminReviewsAction(
            actor=actor,
            result=result,
        )

    async def list_impersonated_pending_reviews(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
    ):
        actor = await (
            self.require_impersonated_moderator(
                platform_user_id=platform_user_id,
                effective_moderator_user_id=(
                    effective_moderator_user_id
                ),
            )
        )

        return await self.reviews.list_pending_reviews(
            tenant_id=actor.tenant_id,
            moderator_user_id=(
                effective_moderator_user_id
            ),
            page=page,
            page_size=page_size,
        )

    async def get_impersonated_pending_review(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        review_id: UUID,
        language: str = "ru",
    ):
        actor = await (
            self.require_impersonated_moderator(
                platform_user_id=platform_user_id,
                effective_moderator_user_id=(
                    effective_moderator_user_id
                ),
            )
        )

        return await (
            self.reviews
            .get_pending_review_for_moderation(
                tenant_id=actor.tenant_id,
                moderator_user_id=(
                    effective_moderator_user_id
                ),
                review_id=review_id,
                language=language,
            )
        )
