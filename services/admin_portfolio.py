from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from database.repositories.portfolio import (
    PortfolioRepository,
)
from services.moderation import ModerationService
from services.portfolio import PortfolioService
from services.user import UserService


class AdminPortfolioAccessError(PermissionError):
    pass


class AdminPortfolioDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class AdminPortfolioActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminPortfolioAction:
    actor: AdminPortfolioActor
    result: object


class AdminPortfolioService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: ModerationService | None = None,
        portfolio: PortfolioService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )
        self.portfolio = (
            portfolio
            or PortfolioService(
                PortfolioRepository(session)
            )
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
        required_roles: set[str],
    ) -> AdminPortfolioActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminPortfolioAccessError(
                "Portfolio moderation access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(required_roles):
            raise AdminPortfolioAccessError(
                "Portfolio moderation access denied."
            )

        return AdminPortfolioActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_moderator_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminPortfolioActor:
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
    ) -> AdminPortfolioActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"super_admin"},
        )

    async def require_impersonated_moderator(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
    ) -> AdminPortfolioActor:
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
            raise AdminPortfolioAccessError(
                "Impersonated portfolio "
                "moderation access denied."
            )

        return actor

    async def list_pending_items(
        self,
        *,
        platform_user_id: int | str,
        page: int = 0,
        page_size: int = 5,
        language: str = "ru",
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await self.portfolio.list_pending_items(
            tenant_id=actor.tenant_id,
            moderator_user_id=actor.user_id,
            page=page,
            page_size=page_size,
            language=language,
        )

    async def get_pending_item(
        self,
        *,
        platform_user_id: int | str,
        item_id: UUID,
        page: int = 0,
        page_size: int = 5,
        language: str = "ru",
    ):
        items = await self.list_pending_items(
            platform_user_id=platform_user_id,
            page=page,
            page_size=page_size,
            language=language,
        )

        return next(
            (
                view
                for view in items[:page_size]
                if view.item.id == item_id
            ),
            None,
        )

    async def list_rejected_items(
        self,
        *,
        platform_user_id: int | str,
        limit: int = 50,
        language: str = "ru",
    ):
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        return await self.portfolio.list_rejected_items(
            tenant_id=actor.tenant_id,
            moderator_user_id=actor.user_id,
            limit=limit,
            language=language,
        )

    async def get_rejected_item(
        self,
        *,
        platform_user_id: int | str,
        item_id: UUID,
        limit: int = 50,
        language: str = "ru",
    ):
        items = await self.list_rejected_items(
            platform_user_id=platform_user_id,
            limit=limit,
            language=language,
        )

        return next(
            (
                view
                for view in items
                if view.item.id == item_id
            ),
            None,
        )

    async def restore_rejected_item(
        self,
        *,
        platform_user_id: int | str,
        item_id: UUID,
    ) -> AdminPortfolioAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )
        result = (
            await self.portfolio.restore_rejected_item(
                tenant_id=actor.tenant_id,
                moderator_user_id=actor.user_id,
                item_id=item_id,
            )
        )

        return AdminPortfolioAction(
            actor=actor,
            result=result,
        )

    async def moderate_item(
        self,
        *,
        platform_user_id: int | str,
        item_id: UUID,
        decision: str,
        reason: str,
    ) -> AdminPortfolioAction:
        actor = await self.require_moderator_actor(
            platform_user_id=platform_user_id
        )

        if decision == "approved":
            result = await self.portfolio.approve_item(
                tenant_id=actor.tenant_id,
                moderator_user_id=actor.user_id,
                item_id=item_id,
                reason=reason,
            )
        elif decision == "rejected":
            result = await self.portfolio.reject_item(
                tenant_id=actor.tenant_id,
                moderator_user_id=actor.user_id,
                item_id=item_id,
                reason=reason,
            )
        elif decision == "forbidden":
            result = (
                await self.portfolio
                .reject_forbidden_item(
                    tenant_id=actor.tenant_id,
                    moderator_user_id=actor.user_id,
                    item_id=item_id,
                    reason=reason,
                )
            )
        else:
            raise AdminPortfolioDecisionError(
                "Unsupported portfolio decision."
            )

        return AdminPortfolioAction(
            actor=actor,
            result=result,
        )

    async def list_impersonated_pending_items(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        page: int = 0,
        page_size: int = 5,
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

        return await self.portfolio.list_pending_items(
            tenant_id=actor.tenant_id,
            moderator_user_id=(
                effective_moderator_user_id
            ),
            page=page,
            page_size=page_size,
            language=language,
        )

    async def get_impersonated_pending_item(
        self,
        *,
        platform_user_id: int | str,
        effective_moderator_user_id: UUID,
        item_id: UUID,
        page: int = 0,
        page_size: int = 5,
        language: str = "ru",
    ):
        items = (
            await self.list_impersonated_pending_items(
                platform_user_id=platform_user_id,
                effective_moderator_user_id=(
                    effective_moderator_user_id
                ),
                page=page,
                page_size=page_size,
                language=language,
            )
        )

        return next(
            (
                view
                for view in items[:page_size]
                if view.item.id == item_id
            ),
            None,
        )
