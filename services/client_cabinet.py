from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from services.user import UserService
from services.user_settings import (
    UserSettingsContext,
    UserSettingsNotFoundError,
    UserSettingsService,
)


class ClientCabinetNotFoundError(
    LookupError
):
    pass


@dataclass(frozen=True)
class ClientCabinetResult:
    language: str
    result: object


class ClientCabinetService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: (
            UserSettingsService | None
        ) = None,
        users: UserService | None = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.users = (
            users
            or UserService(session)
        )

    async def require_context(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserSettingsContext:
        try:
            return await self.settings.get_context(
                platform_user_id=platform_user_id,
            )
        except UserSettingsNotFoundError as exc:
            raise ClientCabinetNotFoundError(
                "Client context not found."
            ) from exc

    async def open_cabinet(
        self,
        *,
        platform_user_id: int | str,
    ) -> ClientCabinetResult:
        context = await self.require_context(
            platform_user_id=platform_user_id,
        )
        result = await self.users.open_client_cabinet(
            telegram_id=platform_user_id,
            language=context.interface_language,
        )

        if result is None:
            raise ClientCabinetNotFoundError(
                "Client cabinet not found."
            )

        return ClientCabinetResult(
            language=context.interface_language,
            result=result,
        )

    async def get_profile(
        self,
        *,
        platform_user_id: int | str,
    ) -> ClientCabinetResult:
        context = await self.require_context(
            platform_user_id=platform_user_id,
        )
        result = await self.users.get_client_profile(
            telegram_id=platform_user_id,
            language=context.interface_language,
        )

        if result is None:
            raise ClientCabinetNotFoundError(
                "Client profile not found."
            )

        return ClientCabinetResult(
            language=context.interface_language,
            result=result,
        )
