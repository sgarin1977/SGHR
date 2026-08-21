from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from database.repositories.legal import (
    LegalRepository,
)
from database.repositories.specialist import (
    SpecialistRepository,
)
from database.repositories.privacy import (
    PrivacyRepository,
)
from database.repositories.translation import (
    SUPPORTED_TRANSLATION_LANGUAGES,
    TRANSLATION_MODES,
    TranslationRepository,
)
from services.legal import (
    LegalService,
    UserConsentView,
)
from services.privacy import PrivacyService
from services.translation import (
    TranslationService,
    TranslationSettingsView,
)
from services.user import UserService


class UserSettingsNotFoundError(LookupError):
    pass


class SpecialistProfileNotFoundError(
    UserSettingsNotFoundError
):
    pass


class UserSettingsValidationError(ValueError):
    pass


@dataclass(frozen=True)
class UserSettingsContext:
    user_id: UUID
    tenant_id: UUID
    interface_language: str
    settings: TranslationSettingsView


class UserSettingsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        translation: TranslationService | None = None,
        privacy: PrivacyService | None = None,
        legal: LegalService | None = None,
        specialists: SpecialistRepository | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.translation = translation or (
            TranslationService(
                TranslationRepository(session)
            )
        )
        self.privacy = privacy or PrivacyService(
            PrivacyRepository(session)
        )
        self.legal = legal or LegalService(
            LegalRepository(session)
        )
        self.specialists = (
            specialists
            or SpecialistRepository(session)
        )

    @staticmethod
    def validate_language_code(
        language_code: str,
    ) -> str:
        normalized = (
            language_code or ""
        ).strip().lower()

        if (
            normalized
            not in SUPPORTED_TRANSLATION_LANGUAGES
        ):
            raise UserSettingsValidationError(
                "Unsupported language code."
            )

        return normalized

    @staticmethod
    def validate_translation_mode(
        translation_mode: str,
    ) -> str:
        normalized = (
            translation_mode or ""
        ).strip().lower()

        if normalized not in TRANSLATION_MODES:
            raise UserSettingsValidationError(
                "Unsupported translation mode."
            )

        return normalized

    async def require_telegram_user(
        self,
        platform_user_id: int | str,
    ) -> User:
        user = (
            await self.users
            .get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise UserSettingsNotFoundError(
                "User settings context not found."
            )

        return user

    async def get_context(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserSettingsContext:
        user = await self.require_telegram_user(
            platform_user_id
        )
        settings = (
            await self.translation
            .get_language_settings_view(
                user_id=user.id,
            )
        )

        return UserSettingsContext(
            user_id=user.id,
            tenant_id=user.tenant_id,
            interface_language=(
                self.validate_language_code(
                    settings.interface_language
                    or user.language_code
                )
            ),
            settings=settings,
        )

    async def update_interface_language(
        self,
        *,
        platform_user_id: int | str,
        language_code: str,
        source: str = "client_settings",
    ) -> TranslationSettingsView:
        normalized_language = (
            self.validate_language_code(
                language_code
            )
        )
        user = await self.require_telegram_user(
            platform_user_id
        )

        return await (
            self.translation
            .update_interface_language(
                tenant_id=user.tenant_id,
                user_id=user.id,
                language_code=normalized_language,
                source=source,
            )
        )

    async def update_message_language(
        self,
        *,
        platform_user_id: int | str,
        language_code: str,
        source: str = "client_settings",
    ) -> TranslationSettingsView:
        normalized_language = (
            self.validate_language_code(
                language_code
            )
        )
        user = await self.require_telegram_user(
            platform_user_id
        )

        return await (
            self.translation
            .update_message_language(
                tenant_id=user.tenant_id,
                user_id=user.id,
                language_code=normalized_language,
                source=source,
            )
        )

    async def update_translation_mode(
        self,
        *,
        platform_user_id: int | str,
        translation_mode: str,
        source: str = "client_settings",
    ) -> TranslationSettingsView:
        normalized_mode = (
            self.validate_translation_mode(
                translation_mode
            )
        )
        user = await self.require_telegram_user(
            platform_user_id
        )

        return await (
            self.translation
            .update_translation_mode(
                tenant_id=user.tenant_id,
                user_id=user.id,
                translation_mode=normalized_mode,
                source=source,
            )
        )

    async def toggle_show_original(
        self,
        *,
        platform_user_id: int | str,
        source: str = "client_settings",
    ) -> TranslationSettingsView:
        user = await self.require_telegram_user(
            platform_user_id
        )

        return await (
            self.translation
            .toggle_show_original(
                tenant_id=user.tenant_id,
                user_id=user.id,
                source=source,
            )
        )

    async def request_data_export(
        self,
        *,
        platform_user_id: int | str,
    ) -> None:
        user = await self.require_telegram_user(
            platform_user_id
        )

        await self.privacy.request_data_export(
            tenant_id=user.tenant_id,
            user_id=user.id,
        )

    async def delete_geo_data(
        self,
        *,
        platform_user_id: int | str,
    ) -> int:
        user = await self.require_telegram_user(
            platform_user_id
        )

        return await self.privacy.delete_geo_data(
            tenant_id=user.tenant_id,
            user_id=user.id,
        )

    async def schedule_profile_deletion(
        self,
        *,
        platform_user_id: int | str,
    ) -> None:
        user = await self.require_telegram_user(
            platform_user_id
        )

        await self.privacy.schedule_profile_deletion(
            tenant_id=user.tenant_id,
            user_id=user.id,
        )


    async def list_consents(
        self,
        *,
        platform_user_id: int | str,
    ) -> list[UserConsentView]:
        user = await self.require_telegram_user(
            platform_user_id
        )

        return await self.legal.list_user_consent_views(
            tenant_id=user.tenant_id,
            user_id=user.id,
        )

    async def schedule_specialist_profile_deletion(
        self,
        *,
        platform_user_id: int | str,
    ) -> None:
        user = await self.require_telegram_user(
            platform_user_id
        )
        specialist = (
            await self.specialists.get_by_user_id(
                user.id
            )
        )

        if specialist is None:
            raise SpecialistProfileNotFoundError(
                "Specialist profile not found."
            )

        await self.privacy.schedule_profile_deletion(
            tenant_id=user.tenant_id,
            user_id=user.id,
            specialist_id=specialist.id,
            source="specialist_cabinet",
        )
