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