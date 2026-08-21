from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.specialist import (
    SpecialistRepository,
)
from database.repositories.translation import (
    TranslationRepository,
)
from services.specialist import (
    SpecialistService,
    SpecialistServiceItemData,
)
from services.translation import TranslationService
from services.user import UserService


class SpecialistServicesAccessError(
    PermissionError
):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SpecialistServicesActor:
    user_id: UUID
    tenant_id: UUID
    specialist_id: UUID
    language: str


@dataclass(frozen=True)
class SpecialistServicesPage:
    actor: SpecialistServicesActor
    total: int
    items: list


@dataclass(frozen=True)
class SpecialistServicesEdit:
    actor: SpecialistServicesActor
    item: Any


@dataclass(frozen=True)
class SpecialistServicesAction:
    actor: SpecialistServicesActor
    result: Any


class SpecialistServicesService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        translations: TranslationService | None = None,
        specialist: SpecialistService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.translations = (
            translations
            or TranslationService(
                TranslationRepository(session)
            )
        )
        self.specialist = (
            specialist
            or SpecialistService(
                SpecialistRepository(session)
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

    @staticmethod
    def parse_price(
        value: str | None,
    ) -> tuple[
        float | None,
        float | None,
    ]:
        cleaned = (
            value or ""
        ).strip().replace(",", ".")

        if not cleaned:
            raise ValueError("empty")

        if "-" in cleaned:
            left, right = [
                part.strip()
                for part in cleaned.split(
                    "-",
                    1,
                )
            ]
            price_from = float(left)
            price_to = float(right)
        else:
            price_from = float(cleaned)
            price_to = None

        if (
            price_from < 0
            or (
                price_to is not None
                and price_to < 0
            )
        ):
            raise ValueError("negative")

        if (
            price_to is not None
            and price_to < price_from
        ):
            raise ValueError("range")

        return price_from, price_to

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
    ) -> SpecialistServicesActor:
        context = await (
            self.users
            .get_specialist_context_by_telegram_id(
                platform_user_id
            )
        )

        if not context:
            raise SpecialistServicesAccessError(
                "user_not_found"
            )

        language = await (
            self.translations
            .resolve_interface_language(
                user_id=context.user.id,
                fallback_language=(
                    context.user.language_code
                    or fallback_language
                ),
            )
        )

        if (
            context.tenant_id is None
            or context.specialist is None
        ):
            raise SpecialistServicesAccessError(
                "specialist_not_found"
            )

        return SpecialistServicesActor(
            user_id=context.user.id,
            tenant_id=context.tenant_id,
            specialist_id=context.specialist.id,
            language=self.normalize_language(
                language
            ),
        )

    async def list_services(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        page: int,
        page_size: int,
    ) -> SpecialistServicesPage:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        total, items = await (
            self.specialist
            .list_service_items_page_for_viewer(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=actor.specialist_id,
                page=max(0, page),
                page_size=max(1, page_size),
            )
        )

        return SpecialistServicesPage(
            actor=actor,
            total=total,
            items=items,
        )

    async def get_service_for_editing(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        service_id: UUID,
    ) -> SpecialistServicesEdit:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        item = await (
            self.specialist
            .get_service_item_for_editing(
                user_id=actor.user_id,
                specialist_id=actor.specialist_id,
                service_id=service_id,
            )
        )

        return SpecialistServicesEdit(
            actor=actor,
            item=item,
        )

    async def save_service(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        service_id: UUID | None,
        category_id: UUID | None,
        profession_id: UUID | None,
        title: str,
        description: str,
        price_from: float | None,
        price_to: float | None,
        currency: str,
    ) -> SpecialistServicesAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        result = await (
            self.specialist.save_service_item(
                SpecialistServiceItemData(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    specialist_id=(
                        actor.specialist_id
                    ),
                    service_id=service_id,
                    category_id=category_id,
                    profession_id=profession_id,
                    title=title,
                    description=description,
                    price_from=price_from,
                    price_to=price_to,
                    currency=currency,
                )
            )
        )

        return SpecialistServicesAction(
            actor=actor,
            result=result,
        )

    async def toggle_service_status(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        service_id: UUID,
    ) -> SpecialistServicesAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        result = await (
            self.specialist
            .toggle_service_item_status(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                service_id=service_id,
            )
        )

        return SpecialistServicesAction(
            actor=actor,
            result=result,
        )

    async def delete_service(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        service_id: UUID,
    ) -> SpecialistServicesAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        result = await (
            self.specialist.delete_service_item(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                service_id=service_id,
            )
        )

        return SpecialistServicesAction(
            actor=actor,
            result=result,
        )
