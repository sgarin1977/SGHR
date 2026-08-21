from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.portfolio import (
    PortfolioRepository,
)
from database.repositories.translation import (
    TranslationRepository,
)
from services.portfolio import (
    OwnerPortfolioPage,
    PortfolioService,
)
from services.translation import TranslationService
from services.user import UserService


class SpecialistPortfolioAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class SpecialistPortfolioActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class SpecialistPortfolioPage:
    actor: SpecialistPortfolioActor
    page: OwnerPortfolioPage


@dataclass(frozen=True)
class SpecialistPortfolioAction:
    actor: SpecialistPortfolioActor
    result: Any


class SpecialistPortfolioService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        translations: TranslationService | None = None,
        portfolio: PortfolioService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.translations = (
            translations
            or TranslationService(
                TranslationRepository(session)
            )
        )
        self.portfolio = (
            portfolio
            or PortfolioService(
                PortfolioRepository(session)
            )
        )

    @staticmethod
    def normalize_language(
        language: str | None,
    ) -> str:
        normalized = (
            language or "ru"
        ).strip().lower()

        if normalized == "ua":
            normalized = "uk"

        if normalized not in {
            "ru",
            "en",
            "pt",
            "uk",
            "pl",
            "de",
            "nl",
        }:
            return "ru"

        return normalized

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
    ) -> SpecialistPortfolioActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise SpecialistPortfolioAccessError(
                "Portfolio access denied."
            )

        language = await (
            self.translations
            .resolve_interface_language(
                user_id=user.id,
                fallback_language=(
                    user.language_code
                    or fallback_language
                ),
            )
        )

        return SpecialistPortfolioActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            language=self.normalize_language(
                language
            ),
        )

    async def list_owner_items(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        page: int,
        page_size: int,
    ) -> SpecialistPortfolioPage:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        portfolio_page = await (
            self.portfolio.list_owner_items_page(
                tenant_id=actor.tenant_id,
                owner_user_id=actor.user_id,
                page=max(0, page),
                page_size=max(1, page_size),
            )
        )

        return SpecialistPortfolioPage(
            actor=actor,
            page=portfolio_page,
        )

    async def delete_owner_item(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        item_id: UUID,
    ) -> SpecialistPortfolioAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        item = await (
            self.portfolio.delete_owner_item(
                tenant_id=actor.tenant_id,
                owner_user_id=actor.user_id,
                item_id=item_id,
            )
        )

        return SpecialistPortfolioAction(
            actor=actor,
            result=item,
        )

    async def upload_item(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        filename: str,
        mime_type: str | None,
        content: bytes,
        caption: str | None,
    ) -> SpecialistPortfolioAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )
        normalized_caption = (
            caption or ""
        ).strip()

        item = await self.portfolio.upload_item(
            tenant_id=actor.tenant_id,
            owner_user_id=actor.user_id,
            filename=filename,
            mime_type=mime_type,
            content=content,
            title=(
                normalized_caption
                or filename
            ),
            description=(
                normalized_caption
                or None
            ),
        )

        return SpecialistPortfolioAction(
            actor=actor,
            result=item,
        )
