import os
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from database.repositories.user import UserRepository
from database.repositories.event import EventRepository
from database.repositories.rate_limit import RateLimitRepository
from services.rate_limit import RateLimitService

@dataclass(frozen=True)
class TelegramUserData:
    platform_user_id: str
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    language_code: str


@dataclass(frozen=True)
class TelegramRegistrationResult:
    user_id: uuid.UUID
    role: str
    is_new: bool

@dataclass(frozen=True)
class RoleSwitchResult:
    user_id: uuid.UUID
    active_role: Optional[str]
    available_roles: list[str]
    role_details: dict[str, str]
    unread_counts: dict[str, int]

@dataclass(frozen=True)
class ClientProfileResult:
    user_number: str
    name: str | None
    username: str | None
    language_code: str
    city_name: str | None
    active_role: str | None
    available_roles: list[str]


@dataclass(frozen=True)
class PublicPlatformStats:
    countries: int
    cities: int
    users: int
    specialists: int


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = UserRepository(session)
        self.events = EventRepository(session)

    def resolve_telegram_role(self, platform_user_id: str) -> str:
        admin_ids = {
            item.strip()
            for item in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
            if item.strip()
        }

        if str(platform_user_id) in admin_ids:
            return "super_admin"

        return "client"

    async def get_user_by_telegram_id(self, telegram_id: int | str) -> Optional[User]:
        account = await self.repository.get_by_platform_account(
            platform="telegram",
            platform_user_id=str(telegram_id),
        )

        if not account:
            return None

        return await self.session.get(User, account.user_id)

    async def get_public_platform_stats(self) -> PublicPlatformStats:
        stats = await self.repository.get_public_platform_stats()

        return PublicPlatformStats(
            countries=stats["countries"],
            cities=stats["cities"],
            users=stats["users"],
            specialists=stats["specialists"],
        )

    async def get_client_profile(
        self,
        *,
        telegram_id: int | str,
        language: str = "ru",
    ) -> ClientProfileResult | None:
        user = await self.get_user_by_telegram_id(
            telegram_id
        )

        if not user:
            return None

        return await self.get_client_profile_by_user_id(
            user_id=user.id,
            language=language,
        )

    async def get_client_profile_by_user_id(
        self,
        *,
        user_id: uuid.UUID,
        language: str = "ru",
    ) -> ClientProfileResult | None:
        row = await self.repository.get_client_profile_row(
            user_id,
            language=language,
        )

        if not row:
            return None

        user_row, account, city_name = row
        roles = await self.repository.list_active_roles(
            user_id
        )

        name = None
        username = None

        if account:
            name = (
                account.display_name
                or " ".join(
                    part
                    for part in [
                        account.first_name,
                        account.last_name,
                    ]
                    if part
                )
                or None
            )
            username = account.username

        return ClientProfileResult(
            user_number=f"user-{str(user_row.id)[:8]}",
            name=name,
            username=username,
            language_code=user_row.language_code,
            city_name=city_name,
            active_role=user_row.active_role,
            available_roles=roles,
        )
    async def update_interface_language(
        self,
        *,
        user_id: uuid.UUID,
        language_code: str,
    ) -> User:
        normalized_language = (language_code or "ru").strip().lower()

        if normalized_language not in {"ru", "en", "pt"}:
            raise ValueError("Unsupported language.")

        return await self.repository.update_language_code(
            user_id=user_id,
            language_code=normalized_language,
        )

    async def get_role_switch_context(
        self,
        telegram_id: int | str,
        language: str = "ru",
    ) -> Optional[RoleSwitchResult]:
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user:
            return None

        roles = await self.repository.list_active_roles(user.id)
        role_details = {}
        unread_counts = await self.repository.get_role_unread_counts(user.id)
        if "specialist" in roles:
            profession_name = await self.repository.get_primary_specialist_profession_name(
                user.id,
                language,
            )
            if profession_name:
                role_details["specialist"] = profession_name
        return RoleSwitchResult(
            user_id=user.id,
            active_role=user.active_role,
            available_roles=roles,
            role_details=role_details,
            unread_counts=unread_counts,
        )
    async def switch_active_role(
        self,
        telegram_id: int | str,
        role: str,
    ) -> RoleSwitchResult:
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user:
            raise ValueError("User not found.")

        roles = await self.repository.list_active_roles(user.id)
        role_details = {}
        unread_counts = await self.repository.get_role_unread_counts(user.id)
        if "specialist" in roles:
            profession_name = await self.repository.get_primary_specialist_profession_name(
                user.id,
            )
            if profession_name:
                role_details["specialist"] = profession_name
        if role not in roles:
            raise ValueError("Role is not active for this user.")

        updated_user = await self.repository.set_active_role(user.id, role)

        await self.events.create_event(
            event_type="role_switched",
            tenant_id=updated_user.tenant_id,
            user_id=updated_user.id,
            entity_type="user",
            entity_id=updated_user.id,
            payload={
                "active_role": role,
                "available_roles": roles,
            },
            platform="telegram",
        )
        await self.session.commit()

        return RoleSwitchResult(
            user_id=updated_user.id,
            active_role=updated_user.active_role,
            available_roles=roles,
            role_details=role_details,
            unread_counts=unread_counts,
        )
    async def register_telegram_user(
        self,
        data: TelegramUserData,
    ) -> TelegramRegistrationResult:
        platform_user_id = str(data.platform_user_id)

        existing_account = await self.repository.get_by_platform_account(
            platform="telegram",
            platform_user_id=platform_user_id,
        )

        role = self.resolve_telegram_role(platform_user_id)

        if existing_account:
            user = await self.session.get(User, existing_account.user_id)

            if user:
                await RateLimitService(
                    RateLimitRepository(self.session)
                ).ensure_start_allowed(
                    tenant_id=user.tenant_id,
                    user_id=existing_account.user_id,
                )
                await self.repository.ensure_active_role(
                    user_id=user.id,
                    tenant_id=user.tenant_id,
                    role="client",
                )
            await self.events.create_event(
                event_type="user_started",
                tenant_id=user.tenant_id if user else None,
                user_id=existing_account.user_id,
                entity_type="user",
                entity_id=existing_account.user_id,
                payload={
                    "is_new": False,
                    "role": role,
                    "platform_user_id": platform_user_id,
                },
                platform="telegram",
            )
            await self.events.create_event(
                event_type="start_opened",
                tenant_id=user.tenant_id if user else None,
                user_id=existing_account.user_id,
                entity_type="user",
                entity_id=existing_account.user_id,
                payload={
                    "is_new": False,
                    "role": role,
                    "active_role": user.active_role if user else None,
                    "platform_user_id": platform_user_id,
                },
                platform="telegram",
            )
            await self.session.commit()

            return TelegramRegistrationResult(
                user_id=existing_account.user_id,
                role=role,
                is_new=False,
            )

        user_id = await self.repository.create_telegram_user_core(
            platform_user_id=platform_user_id,
            username=data.username,
            first_name=data.first_name,
            last_name=data.last_name,
            language_code=data.language_code,
            role=role,
        )

        user = await self.session.get(User, user_id)

        if user:
            await RateLimitService(
                RateLimitRepository(self.session)
            ).ensure_start_allowed(
                tenant_id=user.tenant_id,
                user_id=user_id,
            )

        await self.events.create_event(
            event_type="user_started",
            tenant_id=user.tenant_id if user else None,
            user_id=user_id,
            entity_type="user",
            entity_id=user_id,
            payload={
                "is_new": True,
                "role": role,
                "platform_user_id": platform_user_id,
            },
            platform="telegram",
        )
        await self.events.create_event(
            event_type="start_opened",
            tenant_id=user.tenant_id if user else None,
            user_id=user_id,
            entity_type="user",
            entity_id=user_id,
            payload={
                "is_new": True,
                "role": role,
                "active_role": user.active_role if user else None,
                "platform_user_id": platform_user_id,
            },
            platform="telegram",
        )
        await self.session.commit()

        return TelegramRegistrationResult(
            user_id=user_id,
            role=role,
            is_new=True,
        )
async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int | str) -> Optional[User]:
    service = UserService(session)
    return await service.get_user_by_telegram_id(telegram_id)


async def create_or_update_user(
    session: AsyncSession,
    telegram_id: int | str,
    data: dict,
) -> User:
    service = UserService(session)

    result = await service.register_telegram_user(
        TelegramUserData(
            platform_user_id=str(telegram_id),
            username=data.get("username"),
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            language_code=data.get("language_code") or data.get("language") or "ru",
        )
    )

    user = await session.get(User, result.user_id)
    if not user:
        raise RuntimeError("Telegram user was created but could not be loaded.")

    return user