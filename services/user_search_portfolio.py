from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.repositories.portfolio import (
    PortfolioRepository,
)
from services.portfolio import PortfolioService
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


class UserSearchPortfolioAccessError(
    PermissionError
):
    pass


class UserSearchPortfolioSelectionError(
    ValueError
):
    pass


@dataclass(frozen=True)
class UserSearchPortfolioActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class UserSearchPortfolioPage:
    actor: UserSearchPortfolioActor
    items: tuple[Any, ...]
    page: int
    selected: Any | None


class UserSearchPortfolioService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: (
            UserSettingsService | None
        ) = None,
        portfolio: (
            PortfolioService | None
        ) = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.portfolio = (
            portfolio
            or PortfolioService(
                PortfolioRepository(session)
            )
        )

    @staticmethod
    def parse_id(
        value: UUID | str,
        *,
        field: str,
    ) -> UUID:
        try:
            return (
                value
                if isinstance(value, UUID)
                else UUID(str(value))
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise (
                UserSearchPortfolioSelectionError(
                    f"Invalid {field}."
                )
            ) from exc

    @classmethod
    def parse_optional_id(
        cls,
        value: UUID | str | None,
        *,
        field: str,
    ) -> UUID | None:
        if value is None:
            return None

        return cls.parse_id(
            value,
            field=field,
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserSearchPortfolioActor:
        try:
            context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError as exc:
            raise (
                UserSearchPortfolioAccessError(
                    "Portfolio viewer not found."
                )
            ) from exc

        return UserSearchPortfolioActor(
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            language=(
                context.interface_language
            ),
        )

    async def open_portfolio(
        self,
        *,
        platform_user_id: int | str,
        specialist_id: UUID | str,
        professional_cabinet_id: (
            UUID | str | None
        ) = None,
        page: int | str = 0,
    ) -> UserSearchPortfolioPage:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_specialist_id = self.parse_id(
            specialist_id,
            field="specialist",
        )
        parsed_cabinet_id = (
            self.parse_optional_id(
                professional_cabinet_id,
                field="professional cabinet",
            )
        )

        try:
            requested_page = max(
                0,
                int(page),
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise (
                UserSearchPortfolioSelectionError(
                    "Invalid portfolio page."
                )
            ) from exc

        items = tuple(
            await self.portfolio
            .list_active_items_for_viewer(
                tenant_id=actor.tenant_id,
                specialist_id=(
                    parsed_specialist_id
                ),
                professional_cabinet_id=(
                    parsed_cabinet_id
                ),
                viewer_user_id=(
                    actor.user_id
                ),
                page=requested_page,
            )
        )

        normalized_page = (
            min(
                requested_page,
                len(items) - 1,
            )
            if items
            else 0
        )

        return UserSearchPortfolioPage(
            actor=actor,
            items=items,
            page=normalized_page,
            selected=(
                items[normalized_page]
                if items
                else None
            ),
        )
