from dataclasses import dataclass
from uuid import UUID

from database.repositories.event import (
    EventRepository,
)
from database.repositories.favorites import (
    FavoriteRepository,
)
from database.repositories.search import (
    SpecialistSearchRepository,
)
from services.geo_search import (
    GeoSearchService,
    SpecialistPublicCard,
)


@dataclass(frozen=True)
class FavoriteCardsPage:
    cards: list[SpecialistPublicCard]
    has_next: bool
    page: int


class FavoriteService:
    def __init__(
        self,
        repository: FavoriteRepository,
    ):
        self.repository = repository
        self.card_service = GeoSearchService(
            SpecialistSearchRepository(
                repository.session
            )
        )
        self.events = EventRepository(
            repository.session
        )

    async def list_saved_professional_cabinet_ids(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_ids: list[UUID],
    ) -> set[UUID]:
        return await (
            self.repository
            .list_saved_professional_cabinet_ids(
                tenant_id=tenant_id,
                user_id=user_id,
                professional_cabinet_ids=(
                    professional_cabinet_ids
                ),
            )
        )

    async def save_professional_cabinet(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
    ) -> bool:
        try:
            saved = await (
                self.repository
                .save_professional_cabinet(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    professional_cabinet_id=(
                        professional_cabinet_id
                    ),
                )
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return saved

    async def toggle_professional_cabinet(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
    ) -> bool:
        try:
            is_saved = await (
                self.repository
                .toggle_professional_cabinet(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    professional_cabinet_id=(
                        professional_cabinet_id
                    ),
                )
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return is_saved

    async def remove_professional_cabinet(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
        source: str = "favorites",
    ) -> bool:
        try:
            removed = await (
                self.repository
                .remove_professional_cabinet(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    professional_cabinet_id=(
                        professional_cabinet_id
                    ),
                )
            )

            if removed:
                await self.events.create_event(
                    event_type="favorite_removed",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    entity_type=(
                        "professional_cabinet"
                    ),
                    entity_id=(
                        professional_cabinet_id
                    ),
                    payload={
                        "source": source,
                    },
                    platform="telegram",
                )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return removed

    async def list_public_cards_page(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        page: int,
        page_size: int,
        language: str,
    ) -> FavoriteCardsPage:
        normalized_page = max(
            0,
            page,
        )
        normalized_page_size = max(
            1,
            page_size,
        )

        saved_cabinets = await (
            self.repository
            .list_saved_professional_cabinets(
                tenant_id=tenant_id,
                user_id=user_id,
                limit=(
                    normalized_page_size + 1
                ),
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )

        has_next = (
            len(saved_cabinets)
            > normalized_page_size
        )
        visible_cabinets = saved_cabinets[
            :normalized_page_size
        ]

        cards: list[
            SpecialistPublicCard
        ] = []

        for saved in visible_cabinets:
            card = await (
                self.card_service
                .get_public_card(
                    specialist_id=(
                        saved.specialist.id
                    ),
                    professional_cabinet_id=(
                        saved
                        .professional_cabinet
                        .id
                    ),
                    requester_user_id=user_id,
                    tenant_id=tenant_id,
                    distance_km=None,
                    log_event=False,
                    language=language,
                )
            )

            if card:
                cards.append(card)

        try:
            await self.events.create_event(
                event_type="favorites_opened",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type=(
                    "saved_professional_cabinet"
                ),
                payload={
                    "page": normalized_page,
                    "items_count": len(cards),
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return FavoriteCardsPage(
            cards=cards,
            has_next=has_next,
            page=normalized_page,
        )

    async def get_saved_public_card(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        professional_cabinet_id: UUID,
        language: str,
    ) -> SpecialistPublicCard | None:
        is_saved = await (
            self.repository.is_saved(
                tenant_id=tenant_id,
                user_id=user_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        if not is_saved:
            return None

        context = await (
            self.repository
            .get_public_professional_cabinet(
                tenant_id=tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        if not context:
            return None

        specialist, cabinet = context

        card = await (
            self.card_service
            .get_public_card(
                specialist_id=specialist.id,
                professional_cabinet_id=(
                    cabinet.id
                ),
                requester_user_id=user_id,
                tenant_id=tenant_id,
                distance_km=None,
                log_event=False,
                language=language,
            )
        )

        if not card:
            return None

        try:
            await self.events.create_event(
                event_type=(
                    "professional_cabinet_viewed"
                ),
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type=(
                    "professional_cabinet"
                ),
                entity_id=cabinet.id,
                payload={
                    "source": "favorites",
                    "specialist_id": str(
                        specialist.id
                    ),
                },
                platform="telegram",
            )

            await self.events.create_event(
                event_type="favorite_viewed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type=(
                    "professional_cabinet"
                ),
                entity_id=cabinet.id,
                payload={
                    "source": "favorites",
                    "specialist_id": str(
                        specialist.id
                    ),
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return card