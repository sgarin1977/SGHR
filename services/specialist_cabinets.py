from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.specialist import (
    SpecialistRepository,
)
from database.repositories.webhooks import (
    WebhookRepository,
)
from services.specialist import (
    ProfessionalCabinetAlreadyExistsError,
    SpecialistRegistrationError,
    SpecialistService,
)
from services.webhooks import (
    WebhookEventPublisher,
)
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


class SpecialistCabinetsAccessError(
    PermissionError
):
    pass


class SpecialistCabinetsUserNotFoundError(
    SpecialistCabinetsAccessError
):
    pass


class SpecialistCabinetsProfileNotFoundError(
    SpecialistCabinetsAccessError
):
    pass


class SpecialistCabinetsSelectionError(ValueError):
    pass


class SpecialistCabinetsValidationError(ValueError):
    pass


class SpecialistCabinetsConflictError(Exception):
    pass


@dataclass(frozen=True)
class SpecialistCabinetsActor:
    user_id: UUID
    tenant_id: UUID
    specialist_id: UUID
    language: str


@dataclass(frozen=True)
class SpecialistCabinetsAction:
    actor: SpecialistCabinetsActor
    result: Any


@dataclass(frozen=True)
class SpecialistCabinetOpen:
    language: str
    context: Any


class SpecialistCabinetsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: UserSettingsService | None = None,
        repository: SpecialistRepository | None = None,
        specialists: SpecialistService | None = None,
    ):
        self.session = session
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.repository = (
            repository
            or SpecialistRepository(session)
        )
        self.specialists = (
            specialists
            or SpecialistService(
                self.repository,
                webhook_publisher=(
                    WebhookEventPublisher(
                        repository=WebhookRepository(
                            session
                        )
                    )
                ),
            )
        )

    @staticmethod
    def parse_id(
        value: UUID | str,
        *,
        field: str,
    ) -> UUID:
        try:
            return UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise SpecialistCabinetsSelectionError(
                f"Invalid {field}."
            ) from exc

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistCabinetsActor:
        try:
            context = await self.settings.get_context(
                platform_user_id=platform_user_id,
            )
        except UserSettingsNotFoundError as exc:
            raise (
                SpecialistCabinetsUserNotFoundError(
                    "User context not found."
                )
            ) from exc

        specialist = (
            await self.repository.get_by_user_id(
                context.user_id
            )
        )

        if specialist is None:
            raise (
                SpecialistCabinetsProfileNotFoundError(
                    "Specialist profile not found."
                )
            )

        return SpecialistCabinetsActor(
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            specialist_id=specialist.id,
            language=context.interface_language,
        )

    async def require_user_actor(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
    ) -> SpecialistCabinetsActor:
        specialist = (
            await self.repository.get_by_user_id(
                user_id
            )
        )

        if (
            specialist is None
            or specialist.tenant_id
            != tenant_id
        ):
            raise (
                SpecialistCabinetsProfileNotFoundError(
                    "Specialist profile not found."
                )
            )

        return SpecialistCabinetsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            specialist_id=specialist.id,
            language=language,
        )

    async def require_owned_cabinet_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_user_actor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=language,
        )

        cabinet_row = await (
            self.repository
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
            raise SpecialistCabinetsSelectionError(
                "Professional cabinet "
                "was not found."
            )

        cabinet = cabinet_row[0]

        if (
            cabinet.id
            != professional_cabinet_id
            or cabinet.tenant_id
            != actor.tenant_id
            or cabinet.specialist_id
            != actor.specialist_id
        ):
            raise SpecialistCabinetsSelectionError(
                "Professional cabinet "
                "was not found."
            )

        return SpecialistCabinetsAction(
            actor=actor,
            result=cabinet,
        )

    async def list_cabinets_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_user_actor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=language,
        )

        result = await (
            self.specialists
            .list_professional_cabinet_options(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                language=actor.language,
            )
        )

        return SpecialistCabinetsAction(
            actor=actor,
            result=result,
        )

    async def create_cabinet_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        category_id: UUID | str,
        profession_id: UUID | str,
        platform: str,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_user_actor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=language,
        )
        parsed_category_id = self.parse_id(
            category_id,
            field="category id",
        )
        parsed_profession_id = self.parse_id(
            profession_id,
            field="profession id",
        )

        try:
            result = await (
                self.specialists
                .create_professional_cabinet(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    specialist_id=(
                        actor.specialist_id
                    ),
                    category_id=(
                        parsed_category_id
                    ),
                    profession_id=(
                        parsed_profession_id
                    ),
                    language=actor.language,
                    platform=platform,
                )
            )
        except ProfessionalCabinetAlreadyExistsError:
            raise SpecialistCabinetsConflictError(
                "Professional cabinet "
                "already exists."
            ) from None
        except SpecialistRegistrationError:
            raise SpecialistCabinetsValidationError(
                "Professional cabinet data "
                "is not valid."
            ) from None

        return SpecialistCabinetsAction(
            actor=actor,
            result=result,
        )

    async def switch_cabinet_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID | str,
        platform: str,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_user_actor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=language,
        )
        cabinet_id = self.parse_id(
            professional_cabinet_id,
            field="professional cabinet id",
        )

        try:
            result = await (
                self.specialists
                .switch_active_professional_cabinet(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    specialist_id=(
                        actor.specialist_id
                    ),
                    professional_cabinet_id=(
                        cabinet_id
                    ),
                    platform=platform,
                )
            )
        except SpecialistRegistrationError:
            raise SpecialistCabinetsValidationError(
                "Professional cabinet "
                "is not available."
            ) from None

        return SpecialistCabinetsAction(
            actor=actor,
            result=result,
        )

    async def open_cabinet(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistCabinetOpen:
        try:
            settings_context = (
                await self.settings.get_context(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except UserSettingsNotFoundError as exc:
            raise (
                SpecialistCabinetsUserNotFoundError(
                    "User context not found."
                )
            ) from exc

        context = await (
            self.specialists.open_specialist_cabinet(
                telegram_id=platform_user_id,
                language=(
                    settings_context
                    .interface_language
                ),
            )
        )

        return SpecialistCabinetOpen(
            language=(
                settings_context.interface_language
            ),
            context=context,
        )

    async def get_availability_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
    ) -> SpecialistCabinetsAction:
        cabinet_action = await (
            self.require_owned_cabinet_for_user(
                user_id=user_id,
                tenant_id=tenant_id,
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )
        actor = cabinet_action.actor

        result = await (
            self.specialists
            .get_cabinet_availability(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        return SpecialistCabinetsAction(
            actor=actor,
            result=result,
        )

    async def get_availability(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        result = await (
            self.specialists
            .get_active_cabinet_availability(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=actor.specialist_id,
            )
        )
        return SpecialistCabinetsAction(
            actor=actor,
            result=result,
        )

    async def set_availability_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        availability_status: str,
        platform: str = "telegram",
    ) -> SpecialistCabinetsAction:
        cabinet_action = await (
            self.require_owned_cabinet_for_user(
                user_id=user_id,
                tenant_id=tenant_id,
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )
        actor = cabinet_action.actor

        result = await (
            self.specialists
            .update_cabinet_availability(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                availability_status=(
                    availability_status
                ),
                platform=platform,
            )
        )

        return SpecialistCabinetsAction(
            actor=actor,
            result=result,
        )

    async def set_availability(
        self,
        *,
        platform_user_id: int | str,
        availability_status: str,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        await self.specialists.update_availability(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            specialist_id=actor.specialist_id,
            availability_status=(
                availability_status
            ),
        )
        return SpecialistCabinetsAction(
            actor=actor,
            result=availability_status,
        )

    async def list_cabinets(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        result = await (
            self.specialists
            .list_professional_cabinet_options(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=actor.specialist_id,
                language=actor.language,
            )
        )
        return SpecialistCabinetsAction(actor, result)

    async def switch_cabinet(
        self,
        *,
        platform_user_id: int | str,
        professional_cabinet_id: UUID | str,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        cabinet_id = self.parse_id(
            professional_cabinet_id,
            field="professional cabinet id",
        )
        result = await (
            self.specialists
            .switch_active_professional_cabinet(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=actor.specialist_id,
                professional_cabinet_id=cabinet_id,
            )
        )
        return SpecialistCabinetsAction(actor, result)

    async def list_categories(
        self,
        *,
        platform_user_id: int | str,
        limit: int = 50,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        result = await (
            self.specialists
            .list_professional_cabinet_categories(
                language=actor.language,
                limit=max(1, int(limit)),
            )
        )
        return SpecialistCabinetsAction(actor, result)

    async def list_professions(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        limit: int = 50,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        parsed_category_id = self.parse_id(
            category_id,
            field="category id",
        )
        result = await (
            self.specialists
            .list_professional_cabinet_professions(
                category_id=parsed_category_id,
                language=actor.language,
                limit=max(1, int(limit)),
            )
        )
        return SpecialistCabinetsAction(actor, result)

    async def create_cabinet(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        profession_id: UUID | str,
    ) -> SpecialistCabinetsAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        parsed_category_id = self.parse_id(
            category_id,
            field="category id",
        )
        parsed_profession_id = self.parse_id(
            profession_id,
            field="profession id",
        )
        result = await (
            self.specialists
            .create_professional_cabinet(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=actor.specialist_id,
                category_id=parsed_category_id,
                profession_id=parsed_profession_id,
                language=actor.language,
            )
        )
        return SpecialistCabinetsAction(actor, result)

    async def has_profile(
        self,
        *,
        platform_user_id: int | str,
    ) -> bool:
        try:
            await self.require_actor(
                platform_user_id=(
                    platform_user_id
                ),
            )
        except SpecialistCabinetsAccessError:
            return False

        return True
