from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.event import EventRepository

from database.repositories.translation import (
    normalize_translation_language,
)
from services.user import (
    RoleSwitchResult,
    TelegramRegistrationResult,
    TelegramUserData,
    UserService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


@dataclass(frozen=True)
class UserStartContext:
    language: str
    role_context: RoleSwitchResult | None


@dataclass(frozen=True)
class UserStartRegistration:
    registration: TelegramRegistrationResult
    context: UserStartContext


class UserStartService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        settings: UserSettingsService | None = None,
        events: EventRepository | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.settings = (
            settings
            or UserSettingsService(
                session,
                users=self.users,
            )
        )
        self.events = (
            events
            or EventRepository(session)
        )

    @staticmethod
    def normalize_language(
        language: str | None,
    ) -> str:
        return normalize_translation_language(
            (language or "").strip().lower()
        )

    async def get_context(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None = None,
    ) -> UserStartContext:
        language = self.normalize_language(
            fallback_language
        )

        try:
            settings_context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError:
            return UserStartContext(
                language=language,
                role_context=None,
            )

        language = self.normalize_language(
            settings_context.interface_language
        )
        role_context = (
            await self.users
            .get_role_switch_context(
                platform_user_id,
                language=language,
            )
        )

        return UserStartContext(
            language=language,
            role_context=role_context,
        )

    async def register_user(
        self,
        *,
        platform_user_id: int | str,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> UserStartRegistration:
        normalized_language = (
            self.normalize_language(
                language_code
            )
        )
        registration = (
            await self.users
            .register_telegram_user(
                TelegramUserData(
                    platform_user_id=str(
                        platform_user_id
                    ),
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    language_code=(
                        normalized_language
                    ),
                )
            )
        )
        context = await self.get_context(
            platform_user_id=(
                platform_user_id
            ),
            fallback_language=(
                normalized_language
            ),
        )

        return UserStartRegistration(
            registration=registration,
            context=context,
        )

    async def switch_role(
        self,
        *,
        platform_user_id: int | str,
        role: str,
        fallback_language: str | None = None,
    ) -> UserStartContext:
        role_context = (
            await self.users
            .switch_active_role(
                platform_user_id,
                role,
            )
        )
        language = self.normalize_language(
            fallback_language
        )

        try:
            settings_context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError:
            pass
        else:
            language = self.normalize_language(
                settings_context
                .interface_language
            )

        return UserStartContext(
            language=language,
            role_context=role_context,
        )

    async def get_language(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None = None,
    ) -> str:
        language = self.normalize_language(
            fallback_language
        )

        try:
            settings_context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError:
            return language

        return self.normalize_language(
            settings_context.interface_language
        )

    async def record_placeholder_opened(
        self,
        *,
        platform_user_id: int | str,
        feature: str,
        source: str,
        fallback_language: str | None = None,
    ) -> str:
        language = self.normalize_language(
            fallback_language
        )

        try:
            settings_context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError:
            return language

        language = self.normalize_language(
            settings_context.interface_language
        )

        try:
            await self.events.create_event(
                event_type=(
                    "placeholder_opened"
                ),
                tenant_id=(
                    settings_context.tenant_id
                ),
                user_id=(
                    settings_context.user_id
                ),
                entity_type="feature",
                entity_id=None,
                payload={
                    "feature": feature,
                    "source": source,
                },
                platform="telegram",
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return language
