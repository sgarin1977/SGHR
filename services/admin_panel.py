from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.moderation import (
    ModerationRepository,
)
from services.admin_support import (
    AdminSupportService,
)
from services.moderation import ModerationService
from services.user import UserService


class AdminPanelAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminPanelResult:
    user_id: UUID
    tenant_id: UUID
    language_code: str | None
    roles: frozenset[str]
    active_role: str | None
    panel_roles: frozenset[str]
    show_role_switch: bool
    panel_type: str
    payload: Any = None


@dataclass(frozen=True)
class AdminModerationMenuResult:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]
    summary: Any


class AdminPanelService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: ModerationService | None = None,
        support: AdminSupportService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )
        self.support = (
            support
            or AdminSupportService(session)
        )

    @staticmethod
    def effective_panel_roles(
        roles: set[str],
        active_role: str | None,
    ) -> frozenset[str]:
        if active_role in roles:
            return frozenset({active_role})

        return frozenset(roles)

    async def open_panel(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminPanelResult:
        user = await self.users.get_user_by_telegram_id(
            platform_user_id
        )

        if not user or user.tenant_id is None:
            raise AdminPanelAccessError(
                "Admin panel access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles:
            raise AdminPanelAccessError(
                "Admin panel access denied."
            )

        role_context = await (
            self.users.get_role_switch_context(
                platform_user_id
            )
        )

        show_role_switch = bool(
            role_context
            and len(role_context.available_roles) > 1
        )
        active_role = (
            UserService.resolve_staff_panel_role(
                (
                    role_context.active_role
                    if role_context
                    else None
                ),
                roles,
            )
        )
        panel_roles = self.effective_panel_roles(
            roles,
            active_role,
        )
        panel_type = "generic"
        payload = None

        if active_role == "super_admin":
            panel_type = "super_admin"
            payload = await (
                self.moderation.open_super_admin_menu(
                    admin_user_id=user.id,
                    tenant_id=user.tenant_id,
                )
            )

        elif active_role == "admin":
            panel_type = "admin"
            payload = await (
                self.moderation.open_admin_menu(
                    admin_user_id=user.id,
                    tenant_id=user.tenant_id,
                )
            )

        elif active_role == "moderator":
            panel_type = "moderator"
            payload = await (
                self.moderation.open_moderator_menu(
                    moderator_user_id=user.id,
                    tenant_id=user.tenant_id,
                )
            )

        elif active_role == "support":
            panel_type = "support"
            payload = await self.support.open_staff_menu(
                platform_user_id=platform_user_id
            )
            show_role_switch = (
                payload.show_role_switch
            )

        return AdminPanelResult(
            user_id=user.id,
            tenant_id=user.tenant_id,
            language_code=user.language_code,
            roles=frozenset(roles),
            active_role=active_role,
            panel_roles=panel_roles,
            show_role_switch=show_role_switch,
            panel_type=panel_type,
            payload=payload,
        )

    async def open_moderation_menu(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminModerationMenuResult:
        user = await self.users.get_user_by_telegram_id(
            platform_user_id
        )

        if not user or user.tenant_id is None:
            raise AdminPanelAccessError(
                "Admin moderation access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(
            {"admin", "super_admin"}
        ):
            raise AdminPanelAccessError(
                "Admin moderation access denied."
            )

        summary = await (
            self.moderation.open_moderator_menu(
                moderator_user_id=user.id,
                tenant_id=user.tenant_id,
            )
        )

        return AdminModerationMenuResult(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
            summary=summary,
        )
