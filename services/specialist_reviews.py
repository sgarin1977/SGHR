from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.reviews import (
    ReviewRepository,
)
from services.reviews import (
    PublicReviewPage,
    ReviewService,
)
from services.specialist_cabinets import (
    SpecialistCabinetsActor,
    SpecialistCabinetsService,
)


@dataclass(frozen=True)
class SpecialistReviewsPage:
    actor: SpecialistCabinetsActor
    result: PublicReviewPage


class SpecialistReviewsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cabinets: (
            SpecialistCabinetsService | None
        ) = None,
        reviews: ReviewService | None = None,
    ):
        self.session = session
        self.cabinets = (
            cabinets
            or SpecialistCabinetsService(session)
        )
        self.reviews = (
            reviews
            or ReviewService(
                ReviewRepository(session)
            )
        )

    async def list_reviews(
        self,
        *,
        platform_user_id: int | str,
        page: int = 0,
        page_size: int = 5,
    ) -> SpecialistReviewsPage:
        actor = await self.cabinets.require_actor(
            platform_user_id=platform_user_id,
        )
        result = await (
            self.reviews
            .list_public_reviews_for_viewer(
                tenant_id=actor.tenant_id,
                specialist_id=(
                    actor.specialist_id
                ),
                viewer_user_id=actor.user_id,
                page=max(0, int(page)),
                page_size=max(1, int(page_size)),
                source="specialist_cabinet",
            )
        )
        return SpecialistReviewsPage(
            actor=actor,
            result=result,
        )
