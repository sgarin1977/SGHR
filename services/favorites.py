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

    async def list_saved_specialist_ids(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_ids: list[UUID],
    ) -> set[UUID]:
        return await self.repository.list_saved_specialist_ids(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_ids=specialist_ids,
        )


    async def save_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        try:
            saved = (
                await self.repository
                .save_specialist(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    specialist_id=specialist_id,
                )
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return saved

    async def toggle_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        try:
            is_saved = (
                await self.repository
                .toggle_specialist(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    specialist_id=specialist_id,
                )
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return is_saved

    async def remove_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        source: str = "favorites",
    ) -> bool:
        try:
            removed = (
                await self.repository
                .remove_specialist(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    specialist_id=specialist_id,
                )
            )

            if removed:
                await self.events.create_event(
                    event_type="favorite_removed",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    entity_type="specialist",
                    entity_id=specialist_id,
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
        normalized_page = max(0, page)
        normalized_page_size = max(
            1,
            page_size,
        )

        specialists = (
            await self.repository
            .list_saved_specialists(
                tenant_id=tenant_id,
                user_id=user_id,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )

        has_next = (
            len(specialists)
            > normalized_page_size
        )
        visible_specialists = specialists[
            :normalized_page_size
        ]

        cards: list[SpecialistPublicCard] = []

        for specialist in visible_specialists:
            card = (
                await self.card_service
                .get_public_card(
                    specialist_id=specialist.id,
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
                entity_type="saved_specialist",
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
        specialist_id: UUID,
        language: str,
    ) -> SpecialistPublicCard | None:
        is_saved = await self.repository.is_saved(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )

        if not is_saved:
            return None

        card = await self.card_service.get_public_card(
            specialist_id=specialist_id,
            requester_user_id=user_id,
            tenant_id=tenant_id,
            distance_km=None,
            log_event=False,
            language=language,
        )

        if not card:
            return None

        try:
            await self.events.create_event(
                event_type="specialist_viewed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="specialist",
                entity_id=specialist_id,
                payload={
                    "source": "favorites",
                },
                platform="telegram",
            )

            await self.events.create_event(
                event_type="favorite_viewed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="specialist",
                entity_id=specialist_id,
                payload={
                    "source": "favorites",
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return card