from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.specialist import (
    SpecialistRepository,
)
from services.specialist import (
    SpecialistService,
    MAX_PROFESSIONS_PER_CATEGORY,
    MAX_SPECIALIST_CATEGORIES,
    SpecialistProfileUpdateData,
)
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsActor,
    SpecialistCabinetsProfileNotFoundError,
    SpecialistCabinetsService,
    SpecialistCabinetsUserNotFoundError,
)


class SpecialistProfileAccessError(
    PermissionError
):
    pass


class SpecialistProfileUserNotFoundError(
    SpecialistProfileAccessError
):
    pass


class SpecialistProfileNotFoundError(
    SpecialistProfileAccessError
):
    pass


class SpecialistProfileSelectionError(
    ValueError
):
    pass



class SpecialistProfileProfessionNotFoundError(
    SpecialistProfileSelectionError
):
    pass


class SpecialistProfileProfessionLimitError(
    SpecialistProfileSelectionError
):
    def __init__(
        self,
        reason: str,
    ):
        self.reason = reason
        super().__init__(
            f"Profession limit exceeded: {reason}"
        )



@dataclass(frozen=True)
class SpecialistProfileVisibility:
    visibility: str | None
    moderation_status: str



@dataclass(frozen=True)
class SpecialistProfileProfessionEditor:
    categories: Any
    selections: list[dict]



@dataclass(frozen=True)
class SpecialistProfileProfessionCategory:
    category: Any
    professions: Any


@dataclass(frozen=True)
class SpecialistProfileProfessionToggle:
    category: Any
    profession: Any
    professions: Any
    operation: str


@dataclass(frozen=True)
class SpecialistProfileAction:
    actor: SpecialistCabinetsActor
    result: Any


class SpecialistProfileService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cabinets: (
            SpecialistCabinetsService | None
        ) = None,
        repository: (
            SpecialistRepository | None
        ) = None,
        specialists: (
            SpecialistService | None
        ) = None,
    ):
        self.session = session
        self.repository = (
            repository
            or SpecialistRepository(session)
        )
        self.specialists = (
            specialists
            or SpecialistService(
                self.repository
            )
        )
        self.cabinets = (
            cabinets
            or SpecialistCabinetsService(
                session,
                repository=self.repository,
                specialists=self.specialists,
            )
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistCabinetsActor:
        try:
            return await (
                self.cabinets.require_actor(
                    platform_user_id=(
                        platform_user_id
                    ),
                )
            )
        except (
            SpecialistCabinetsUserNotFoundError
        ) as exc:
            raise (
                SpecialistProfileUserNotFoundError(
                    "User context not found."
                )
            ) from exc
        except (
            SpecialistCabinetsProfileNotFoundError
        ) as exc:
            raise SpecialistProfileNotFoundError(
                "Specialist profile not found."
            ) from exc
        except (
            SpecialistCabinetsAccessError
        ) as exc:
            raise SpecialistProfileAccessError(
                "Specialist profile actor "
                "not found."
            ) from exc

    async def get_moderation(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        cabinet = await (
            self.repository
            .get_active_professional_cabinet(
                tenant_id=actor.tenant_id,
                specialist_id=(
                    actor.specialist_id
                ),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=cabinet,
        )

    async def get_active_profile(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        profile = await (
            self.specialists
            .get_active_cabinet_profile(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                language=actor.language,
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=profile,
        )

    async def get_visibility(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        visibility = await (
            self.specialists
            .get_profile_visibility(
                user_id=actor.user_id,
            )
        )
        moderation_status = await (
            self.specialists
            .get_active_cabinet_moderation_status(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=(
                SpecialistProfileVisibility(
                    visibility=visibility,
                    moderation_status=(
                        moderation_status
                    ),
                )
            ),
        )

    async def submit_moderation(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        changed = await (
            self.specialists
            .submit_active_professional_cabinet_for_moderation(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=changed,
        )

    async def set_visibility(
        self,
        *,
        platform_user_id: int | str,
        visibility: str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        await (
            self.specialists
            .update_profile_visibility(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                visibility=visibility,
            )
        )
        moderation_status = await (
            self.specialists
            .get_active_cabinet_moderation_status(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=(
                SpecialistProfileVisibility(
                    visibility=visibility,
                    moderation_status=(
                        moderation_status
                    ),
                )
            ),
        )

    async def get_languages(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        languages = await (
            self.specialists
            .get_languages_for_editing(
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=languages,
        )

    async def get_skills(
        self,
        *,
        platform_user_id: int | str,
        limit: int = 30,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        skills = await (
            self.specialists
            .get_skills_for_editing(
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                language=actor.language,
                limit=max(1, int(limit)),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=skills,
        )


    async def save_languages(
        self,
        *,
        platform_user_id: int | str,
        language_codes: list[str],
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        result = await (
            self.specialists
            .update_languages(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                language_codes=list(
                    language_codes
                ),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=result,
        )

    async def save_skills(
        self,
        *,
        platform_user_id: int | str,
        skill_ids: list[UUID | str],
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        try:
            parsed_skill_ids = [
                (
                    item
                    if isinstance(item, UUID)
                    else UUID(str(item))
                )
                for item in skill_ids
            ]
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise (
                SpecialistProfileSelectionError(
                    "Invalid skill selection."
                )
            ) from exc

        result = await (
            self.specialists
            .update_skills(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                skill_ids=parsed_skill_ids,
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=result,
        )

    async def toggle_language(
        self,
        *,
        platform_user_id: int | str,
        selected_codes: list[str],
        language_code: str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        selected = (
            self.specialists
            .toggle_language_selection(
                selected_codes=list(
                    selected_codes
                ),
                language_code=(
                    language_code
                ),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=selected,
        )

    async def open_work_format(
        self,
        *,
        platform_user_id: int | str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        return SpecialistProfileAction(
            actor=actor,
            result=None,
        )

    async def save_work_format(
        self,
        *,
        platform_user_id: int | str,
        work_format: str,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        result = await (
            self.specialists
            .update_work_format(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                work_format=work_format,
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=result,
        )

    async def record_blocked_change(
        self,
        *,
        platform_user_id: int | str,
        field: str,
        source: str | None = None,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        await (
            self.specialists
            .record_blocked_profile_change(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                field=field,
                source=source,
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=None,
        )

    async def save_location_candidate(
        self,
        *,
        platform_user_id: int | str,
        candidate: dict,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        result = await (
            self.specialists
            .update_location_from_candidate(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                candidate=candidate,
                language=actor.language,
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=result,
        )

    async def save_country_candidate(
        self,
        *,
        platform_user_id: int | str,
        candidate: dict,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        result = await (
            self.specialists
            .update_country_from_candidate(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                specialist_id=(
                    actor.specialist_id
                ),
                candidate=candidate,
                language=actor.language,
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=result,
        )

    async def open_profession_editor(
        self,
        *,
        platform_user_id: int | str,
        limit: int = 50,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        normalized_limit = max(
            1,
            int(limit),
        )
        categories = await (
            self.specialists
            .list_active_categories_for_profile_editor(
                limit=normalized_limit,
            )
        )
        selections = await (
            self.specialists
            .get_profile_profession_selections(
                specialist_id=(
                    actor.specialist_id
                ),
                language=actor.language,
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=(
                SpecialistProfileProfessionEditor(
                    categories=categories,
                    selections=selections,
                )
            ),
        )

    async def list_profession_categories(
        self,
        *,
        platform_user_id: int | str,
        limit: int = 50,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        categories = await (
            self.specialists
            .list_active_categories_for_profile_editor(
                limit=max(1, int(limit)),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=categories,
        )

    async def list_professions_for_category(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        limit: int = 50,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        try:
            parsed_category_id = (
                category_id
                if isinstance(
                    category_id,
                    UUID,
                )
                else UUID(
                    str(category_id)
                )
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise (
                SpecialistProfileSelectionError(
                    "Invalid category selection."
                )
            ) from exc

        professions = await (
            self.repository
            .list_active_professions_by_category(
                parsed_category_id,
                limit=max(1, int(limit)),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=professions,
        )

    async def save_professions(
        self,
        *,
        platform_user_id: int | str,
        profession_selections: list[dict],
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        result = await (
            self.specialists
            .replace_profile_professions(
                specialist_id=(
                    actor.specialist_id
                ),
                user_id=actor.user_id,
                profession_selections=list(
                    profession_selections
                ),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=result,
        )

    async def open_profession_category(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        limit: int = 50,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        try:
            parsed_category_id = (
                category_id
                if isinstance(
                    category_id,
                    UUID,
                )
                else UUID(
                    str(category_id)
                )
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise (
                SpecialistProfileProfessionNotFoundError(
                    "Invalid category."
                )
            ) from exc

        category = await (
            self.repository
            .get_active_category(
                parsed_category_id
            )
        )
        if category is None:
            raise (
                SpecialistProfileProfessionNotFoundError(
                    "Category not found."
                )
            )

        professions = await (
            self.repository
            .list_active_professions_by_category(
                parsed_category_id,
                limit=max(1, int(limit)),
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=(
                SpecialistProfileProfessionCategory(
                    category=category,
                    professions=professions,
                )
            ),
        )

    async def toggle_profession(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        profession_index: int,
        selected_professions: list[dict],
        limit: int = 50,
    ) -> SpecialistProfileAction:
        category_action = await (
            self.open_profession_category(
                platform_user_id=(
                    platform_user_id
                ),
                category_id=category_id,
                limit=limit,
            )
        )
        actor = category_action.actor
        category = (
            category_action.result.category
        )
        professions = list(
            category_action
            .result.professions
        )

        try:
            index = int(
                profession_index
            )
            if index < 0:
                raise IndexError
            profession = professions[index]
        except (
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            raise (
                SpecialistProfileProfessionNotFoundError(
                    "Profession not found."
                )
            ) from exc

        profession_id = str(
            profession.id
        )
        selected = list(
            selected_professions
        )
        selected_ids = {
            str(
                item.get(
                    "profession_id"
                )
            )
            for item in selected
            if item.get(
                "profession_id"
            )
        }

        if profession_id in selected_ids:
            operation = "remove"
        else:
            category_id_text = str(
                profession.category_id
            )
            selected_category_ids = {
                str(
                    item.get(
                        "category_id"
                    )
                )
                for item in selected
                if item.get(
                    "category_id"
                )
            }

            if (
                category_id_text
                not in selected_category_ids
                and len(
                    selected_category_ids
                )
                >= MAX_SPECIALIST_CATEGORIES
            ):
                raise (
                    SpecialistProfileProfessionLimitError(
                        "categories"
                    )
                )

            professions_in_category = [
                item
                for item in selected
                if str(
                    item.get(
                        "category_id"
                    )
                )
                == category_id_text
            ]

            if (
                len(
                    professions_in_category
                )
                >= MAX_PROFESSIONS_PER_CATEGORY
            ):
                raise (
                    SpecialistProfileProfessionLimitError(
                        "per_category"
                    )
                )

            operation = "add"

        return SpecialistProfileAction(
            actor=actor,
            result=(
                SpecialistProfileProfessionToggle(
                    category=category,
                    profession=profession,
                    professions=professions,
                    operation=operation,
                )
            ),
        )

    async def save_basic_profile(
        self,
        *,
        platform_user_id: int | str,
        display_name: str | None = None,
        short_description: str | None = None,
        contact_text: str | None = None,
    ) -> SpecialistProfileAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        result = await (
            self.specialists
            .update_profile_with_audit(
                SpecialistProfileUpdateData(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    specialist_id=(
                        actor.specialist_id
                    ),
                    display_name=display_name,
                    short_description=(
                        short_description
                    ),
                    contact_text=contact_text,
                )
            )
        )

        return SpecialistProfileAction(
            actor=actor,
            result=result,
        )
