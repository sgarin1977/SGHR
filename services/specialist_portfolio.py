from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.portfolio import (
    PortfolioRepository,
)
from database.repositories.specialist import (
    SpecialistRepository,
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
    specialist_id: UUID | None = None


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
        specialists: SpecialistRepository | None = None,
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
        self.specialists = (
            specialists
            or SpecialistRepository(session)
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

    async def require_user_actor(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str | None,
    ) -> SpecialistPortfolioActor:
        specialist = (
            await self.specialists.get_by_user_id(
                user_id
            )
        )

        if (
            specialist is None
            or specialist.user_id != user_id
            or specialist.tenant_id != tenant_id
        ):
            raise SpecialistPortfolioAccessError(
                "Portfolio access denied."
            )

        return SpecialistPortfolioActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=self.normalize_language(
                language
            ),
            specialist_id=specialist.id,
        )

    async def list_portfolio_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str | None,
        professional_cabinet_id: UUID,
        page: int,
        platform: str,
    ) -> SpecialistPortfolioAction:
        actor = await self.require_user_actor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=language,
        )

        if actor.specialist_id is None:
            raise SpecialistPortfolioAccessError(
                "Portfolio access denied."
            )

        cabinet_row = await (
            self.specialists
            .get_professional_cabinet(
                tenant_id=actor.tenant_id,
                specialist_id=(
                    actor.specialist_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        if not cabinet_row:
            raise SpecialistPortfolioAccessError(
                "Portfolio access denied."
            )

        cabinet = cabinet_row[0]

        if (
            cabinet.id
            != professional_cabinet_id
            or cabinet.specialist_id
            != actor.specialist_id
            or cabinet.tenant_id
            != actor.tenant_id
        ):
            raise SpecialistPortfolioAccessError(
                "Portfolio access denied."
            )

        items = await (
            self.portfolio
            .list_active_items_for_viewer(
                tenant_id=actor.tenant_id,
                specialist_id=(
                    actor.specialist_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                viewer_user_id=actor.user_id,
                page=max(0, page),
                platform=platform,
            )
        )

        return SpecialistPortfolioAction(
            actor=actor,
            result=items,
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
