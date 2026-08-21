from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.favorites import (
    FavoriteRepository,
)
from services.favorites import FavoriteService
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


class UserFavoritesAccessError(PermissionError):
    pass


class UserFavoritesSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class UserFavoritesActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class UserFavoritesPage:
    actor: UserFavoritesActor
    cards: list[Any]
    page: int
    has_next: bool


@dataclass(frozen=True)
class UserFavoritesAction:
    actor: UserFavoritesActor
    result: Any


class UserFavoritesService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: UserSettingsService | None = None,
        favorites: FavoriteService | None = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.favorites = (
            favorites
            or FavoriteService(
                FavoriteRepository(session)
            )
        )

    @staticmethod
    def parse_cabinet_id(
        value: UUID | str,
    ) -> UUID | None:
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserFavoritesActor:
        try:
            context = await self.settings.get_context(
                platform_user_id=platform_user_id,
            )
        except UserSettingsNotFoundError as exc:
            raise UserFavoritesAccessError(
                "Favorites user context not found."
            ) from exc

        return UserFavoritesActor(
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            language=context.interface_language,
        )

    async def list_favorites(
        self,
        *,
        platform_user_id: int | str,
        page: int,
        page_size: int,
    ) -> UserFavoritesPage:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        result = (
            await self.favorites
            .list_public_cards_page(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                page=max(0, int(page)),
                page_size=max(1, int(page_size)),
                language=actor.language,
            )
        )

        return UserFavoritesPage(
            actor=actor,
            cards=list(result.cards),
            page=result.page,
            has_next=result.has_next,
        )

    async def get_favorite_card(
        self,
        *,
        platform_user_id: int | str,
        professional_cabinet_id: UUID | str,
    ) -> UserFavoritesAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        cabinet_id = self.parse_cabinet_id(
            professional_cabinet_id
        )

        if cabinet_id is None:
            return UserFavoritesAction(
                actor=actor,
                result=None,
            )

        card = (
            await self.favorites
            .get_saved_public_card(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                professional_cabinet_id=(
                    cabinet_id
                ),
                language=actor.language,
            )
        )

        return UserFavoritesAction(
            actor=actor,
            result=card,
        )

    async def remove_favorite(
        self,
        *,
        platform_user_id: int | str,
        professional_cabinet_id: UUID | str,
    ) -> UserFavoritesAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        cabinet_id = self.parse_cabinet_id(
            professional_cabinet_id
        )

        if cabinet_id is None:
            return UserFavoritesAction(
                actor=actor,
                result=False,
            )

        removed = (
            await self.favorites
            .remove_professional_cabinet(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                professional_cabinet_id=(
                    cabinet_id
                ),
                source="favorites",
            )
        )

        return UserFavoritesAction(
            actor=actor,
            result=bool(removed),
        )

    async def toggle_favorite(
        self,
        *,
        platform_user_id: int | str,
        professional_cabinet_id: (
            UUID | str
        ),
    ) -> UserFavoritesAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        cabinet_id = self.parse_cabinet_id(
            professional_cabinet_id
        )

        if cabinet_id is None:
            raise UserFavoritesSelectionError(
                "Invalid professional cabinet."
            )

        is_saved = await (
            self.favorites
            .toggle_professional_cabinet(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                professional_cabinet_id=(
                    cabinet_id
                ),
            )
        )

        return UserFavoritesAction(
            actor=actor,
            result=bool(is_saved),
        )
