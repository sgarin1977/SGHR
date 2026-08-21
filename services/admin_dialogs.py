from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.contact import (
    ContactChatRepository,
)
from database.repositories.moderation import (
    ModerationRepository,
)
from services.contact_chat import ContactChatService
from services.moderation import ModerationService
from services.user import UserService


ADMIN_DIALOG_ROLES = frozenset(
    {
        "super_admin",
        "admin",
        "moderator",
    }
)


class AdminDialogsAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminDialogsActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminDialogsPage:
    items: tuple[object, ...]
    page: int
    has_next: bool


class AdminDialogsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: ModerationService | None = None,
        contacts: ContactChatService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )
        self.contacts = (
            contacts
            or ContactChatService(
                ContactChatRepository(session)
            )
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
        required_roles: set[str] | frozenset[str],
    ) -> AdminDialogsActor:
        user = await self.users.get_user_by_telegram_id(
            platform_user_id
        )

        if not user or user.tenant_id is None:
            raise AdminDialogsAccessError(
                "Administrative dialog access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if not roles.intersection(required_roles):
            raise AdminDialogsAccessError(
                "Administrative dialog access denied."
            )

        return AdminDialogsActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def require_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminDialogsActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles=ADMIN_DIALOG_ROLES,
        )

    async def require_super_admin_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminDialogsActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            required_roles={"super_admin"},
        )

    async def require_impersonated_target(
        self,
        *,
        actor: AdminDialogsActor,
        target_user_id: UUID,
    ):
        target = await self.users.get_user_by_id(
            target_user_id
        )

        if (
            not target
            or target.tenant_id is None
            or target.tenant_id != actor.tenant_id
        ):
            raise AdminDialogsAccessError(
                "Impersonated dialog access denied."
            )

        return target

    async def list_admin_contexts(
        self,
        *,
        platform_user_id: int | str,
    ):
        actor = await self.require_admin_actor(
            platform_user_id=platform_user_id
        )

        return await self.moderation.open_admin_thread_contexts(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    async def get_admin_thread_messages(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID,
    ):
        actor = await self.require_admin_actor(
            platform_user_id=platform_user_id
        )

        return await self.moderation.open_admin_thread_messages(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            thread_id=thread_id,
        )

    async def list_impersonated_client_threads(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        page: int,
        page_size: int = 5,
        language: str = "ru",
    ) -> AdminDialogsPage:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_impersonated_target(
            actor=actor,
            target_user_id=target_user_id,
        )

        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 25),
        )

        items = await self.contacts.list_client_threads(
            user_id=target_user_id,
            view="active",
            limit=normalized_page_size + 1,
            offset=(
                normalized_page
                * normalized_page_size
            ),
            language=language,
        )

        return AdminDialogsPage(
            items=tuple(
                items[:normalized_page_size]
            ),
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def get_impersonated_client_thread(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        thread_id: UUID,
        language: str = "ru",
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_impersonated_target(
            actor=actor,
            target_user_id=target_user_id,
        )

        return await self.contacts.get_thread_detail(
            thread_id=thread_id,
            user_id=target_user_id,
            language=language,
        )

    async def list_impersonated_specialist_threads(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        professional_cabinet_id: UUID | None,
        page: int,
        page_size: int = 5,
        language: str = "ru",
    ) -> AdminDialogsPage:
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_impersonated_target(
            actor=actor,
            target_user_id=target_user_id,
        )

        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 25),
        )

        items = (
            await self.contacts.list_specialist_threads(
                user_id=target_user_id,
                view="active",
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        return AdminDialogsPage(
            items=tuple(
                items[:normalized_page_size]
            ),
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def get_impersonated_specialist_thread(
        self,
        *,
        platform_user_id: int | str,
        target_user_id: UUID,
        thread_id: UUID,
        language: str = "ru",
    ):
        actor = await self.require_super_admin_actor(
            platform_user_id=platform_user_id
        )
        await self.require_impersonated_target(
            actor=actor,
            target_user_id=target_user_id,
        )

        return await self.contacts.get_thread_detail(
            thread_id=thread_id,
            user_id=target_user_id,
            language=language,
        )
