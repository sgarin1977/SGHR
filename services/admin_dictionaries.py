from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.dictionaries import (
    DictionaryRepository,
)
from database.repositories.moderation import (
    ModerationRepository,
)
from services.dictionaries import (
    DictionaryService,
    DictionaryServiceError,
)
from services.moderation import ModerationService
from services.user import UserService


class AdminDictionariesAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class AdminDictionariesActor:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AdminDictionariesPageResult:
    actor: AdminDictionariesActor
    items: tuple[object, ...]
    page: int
    has_next: bool


@dataclass(frozen=True)
class AdminDictionariesMoveTargetsResult:
    actor: AdminDictionariesActor
    category: object
    professions: tuple[object, ...]
    visible_professions: tuple[object, ...]
    page: int
    has_next: bool



@dataclass(frozen=True)
class AdminDictionariesArchiveResult:
    item: object
    archived: bool


class AdminDictionariesService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        moderation: ModerationService | None = None,
        dictionaries: DictionaryService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )
        self.dictionaries = (
            dictionaries
            or DictionaryService(
                DictionaryRepository(session)
            )
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminDictionariesActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminDictionariesAccessError(
                "Dictionary access denied."
            )

        roles = await self.moderation.get_admin_roles(
            user.id,
            tenant_id=user.tenant_id,
        )

        if "super_admin" not in roles:
            raise AdminDictionariesAccessError(
                "Dictionary access denied."
            )

        return AdminDictionariesActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=frozenset(roles),
        )

    async def list_categories(
        self,
        *,
        platform_user_id: int | str,
        language: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )

        items = await (
            self.dictionaries.list_category_cards(
                language=language,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )
        visible_items = tuple(
            items[:normalized_page_size]
        )

        return AdminDictionariesPageResult(
            actor=actor,
            items=visible_items,
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def get_category(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID,
        language: str,
    ):
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await self.dictionaries.get_category_card(
            category_id=category_id,
            language=language,
        )

    async def create_category(
        self,
        *,
        platform_user_id: int | str,
        title: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.create_category(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            title=title,
            language=language,
        )
        await self.session.commit()

        return result

    async def rename_category(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        title: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.rename_category(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            category_id=category_id,
            title=title,
            language=language,
        )
        await self.session.commit()

        return result

    async def toggle_category_visibility(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries
            .toggle_category_visibility(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                category_id=category_id,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def toggle_category_archive(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        current = await (
            self.dictionaries.get_category_card(
                category_id=category_id,
                language=language,
            )
        )

        if current is None:
            return None

        if current.status_code == "archived":
            result = await (
                self.dictionaries.unarchive_category(
                    admin_user_id=actor.user_id,
                    tenant_id=actor.tenant_id,
                    category_id=category_id,
                    language=language,
                )
            )
        else:
            result = await (
                self.dictionaries.archive_category(
                    admin_user_id=actor.user_id,
                    tenant_id=actor.tenant_id,
                    category_id=category_id,
                    language=language,
                )
            )

        await self.session.commit()
        return result

    async def update_category_sort_order(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        sort_order_text: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries
            .update_category_sort_order(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                category_id=category_id,
                sort_order_text=sort_order_text,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def list_category_specialists(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )

        items = await (
            self.dictionaries
            .list_category_specialists(
                category_id=category_id,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )
        visible_items = tuple(
            items[:normalized_page_size]
        )

        return AdminDictionariesPageResult(
            actor=actor,
            items=visible_items,
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def list_category_specialist_ids(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
    ) -> tuple[UUID, ...]:
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        specialist_ids = await (
            self.dictionaries
            .list_category_specialist_ids(
                category_id=category_id,
            )
        )

        return tuple(specialist_ids)

    async def list_move_target_categories(
        self,
        *,
        platform_user_id: int | str,
        language: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        categories = tuple(
            await self.dictionaries
            .list_specialist_move_target_categories(
                language=language,
            )
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )
        start = (
            normalized_page
            * normalized_page_size
        )
        visible = categories[
            start:start + normalized_page_size
        ]

        if not visible and normalized_page > 0:
            normalized_page = 0
            start = 0
            visible = categories[
                :normalized_page_size
            ]

        return AdminDictionariesPageResult(
            actor=actor,
            items=tuple(visible),
            page=normalized_page,
            has_next=(
                start + normalized_page_size
                < len(categories)
            ),
        )

    async def get_move_target_professions(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str,
        language: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesMoveTargetsResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        category = await (
            self.dictionaries.get_category_card(
                category_id=category_id,
                language=language,
            )
        )
        professions = tuple(
            await self.dictionaries
            .list_active_professions_for_category(
                category_id=category_id,
                language=language,
            )
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )
        start = (
            normalized_page
            * normalized_page_size
        )
        visible = professions[
            start:start + normalized_page_size
        ]

        if not visible and normalized_page > 0:
            normalized_page = 0
            start = 0
            visible = professions[
                :normalized_page_size
            ]

        return AdminDictionariesMoveTargetsResult(
            actor=actor,
            category=category,
            professions=professions,
            visible_professions=tuple(visible),
            page=normalized_page,
            has_next=(
                start + normalized_page_size
                < len(professions)
            ),
        )

    async def preview_multi_move(
        self,
        *,
        platform_user_id: int | str,
        source_type: str,
        source_id: UUID | str,
        target_category_id: UUID | str,
        target_profession_ids: list[str],
        specialist_ids: list[str],
        mode: str,
        language: str,
    ):
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.dictionaries
            .preview_multi_profession_move(
                source_type=source_type,
                source_id=source_id,
                target_category_id=(
                    target_category_id
                ),
                target_profession_ids=(
                    target_profession_ids
                ),
                specialist_ids=specialist_ids,
                mode=mode,
                language=language,
            )
        )

    async def execute_multi_move(
        self,
        *,
        platform_user_id: int | str,
        source_type: str,
        source_id: UUID | str,
        target_category_id: UUID | str,
        target_profession_ids: list[str],
        specialist_ids: list[str],
        mode: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.dictionaries
            .move_specialists_to_multiple_professions(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                source_type=source_type,
                source_id=source_id,
                target_category_id=(
                    target_category_id
                ),
                target_profession_ids=(
                    target_profession_ids
                ),
                specialist_ids=specialist_ids,
                mode=mode,
                language=language,
            )
        )

    async def list_professions(
        self,
        *,
        platform_user_id: int | str,
        language: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )

        items = await (
            self.dictionaries.list_profession_cards(
                language=language,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )
        visible_items = tuple(
            items[:normalized_page_size]
        )

        return AdminDictionariesPageResult(
            actor=actor,
            items=visible_items,
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def get_profession(
        self,
        *,
        platform_user_id: int | str,
        profession_id: UUID | str,
        language: str,
    ):
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.dictionaries.get_profession_card(
                profession_id=profession_id,
                language=language,
            )
        )

    async def create_profession(
        self,
        *,
        platform_user_id: int | str,
        category_id: UUID | str | None,
        category_code: str | None,
        title: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries.create_profession(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                category_id=category_id,
                category_code=category_code,
                title=title,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def rename_profession(
        self,
        *,
        platform_user_id: int | str,
        profession_id: UUID | str,
        title: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries.rename_profession(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                profession_id=profession_id,
                title=title,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def move_profession_to_category(
        self,
        *,
        platform_user_id: int | str,
        profession_id: UUID | str,
        category_code: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries
            .move_profession_to_category(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                profession_id=profession_id,
                category_code=category_code,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def toggle_profession_visibility(
        self,
        *,
        platform_user_id: int | str,
        profession_id: UUID | str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries
            .toggle_profession_visibility(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                profession_id=profession_id,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def toggle_profession_archive(
        self,
        *,
        platform_user_id: int | str,
        profession_id: UUID | str,
        language: str,
    ) -> AdminDictionariesArchiveResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        current = await (
            self.dictionaries.get_profession_card(
                profession_id=profession_id,
                language=language,
            )
        )

        if current is None:
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        if current.status_code == "archived":
            item = await (
                self.dictionaries
                .unarchive_profession(
                    admin_user_id=actor.user_id,
                    tenant_id=actor.tenant_id,
                    profession_id=profession_id,
                    language=language,
                )
            )
            archived = False
        else:
            item = await (
                self.dictionaries.archive_profession(
                    admin_user_id=actor.user_id,
                    tenant_id=actor.tenant_id,
                    profession_id=profession_id,
                    language=language,
                )
            )
            archived = True

        await self.session.commit()

        return AdminDictionariesArchiveResult(
            item=item,
            archived=archived,
        )

    async def list_profession_specialist_ids(
        self,
        *,
        platform_user_id: int | str,
        profession_id: UUID | str,
    ) -> tuple[str, ...]:
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        specialist_ids = await (
            self.dictionaries
            .list_profession_specialist_ids(
                profession_id=profession_id,
            )
        )

        return tuple(specialist_ids)

    async def list_profession_specialists(
        self,
        *,
        platform_user_id: int | str,
        profession_id: UUID | str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )

        items = await (
            self.dictionaries
            .list_profession_specialists(
                profession_id=profession_id,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )
        visible_items = tuple(
            items[:normalized_page_size]
        )

        return AdminDictionariesPageResult(
            actor=actor,
            items=visible_items,
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def list_countries(
        self,
        *,
        platform_user_id: int | str,
        language: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )

        items = await (
            self.dictionaries.list_country_cards(
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
                language=language,
            )
        )
        visible_items = tuple(
            items[:normalized_page_size]
        )

        return AdminDictionariesPageResult(
            actor=actor,
            items=visible_items,
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def get_country(
        self,
        *,
        platform_user_id: int | str,
        country_id: UUID | str,
        language: str,
    ):
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await self.dictionaries.get_country_card(
            country_id=country_id,
            language=language,
        )

    async def create_country(
        self,
        *,
        platform_user_id: int | str,
        payload: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.create_country(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            payload=payload,
            language=language,
        )
        await self.session.commit()

        return result

    async def import_countries(
        self,
        *,
        platform_user_id: int | str,
        payload: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.import_countries(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            payload=payload,
        )
        await self.session.commit()

        return result

    async def update_country(
        self,
        *,
        platform_user_id: int | str,
        country_id: UUID | str,
        payload: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.update_country(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            country_id=country_id,
            payload=payload,
            language=language,
        )
        await self.session.commit()

        return result

    async def toggle_country_visibility(
        self,
        *,
        platform_user_id: int | str,
        country_id: UUID | str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries
            .toggle_country_visibility(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                country_id=country_id,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def list_cities(
        self,
        *,
        platform_user_id: int | str,
        country_id: UUID | str,
        language: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )

        items = await (
            self.dictionaries.list_city_cards(
                country_id=country_id,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
                language=language,
            )
        )
        visible_items = tuple(
            items[:normalized_page_size]
        )

        return AdminDictionariesPageResult(
            actor=actor,
            items=visible_items,
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def get_city(
        self,
        *,
        platform_user_id: int | str,
        city_id: UUID | str,
        language: str,
    ):
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await self.dictionaries.get_city_card(
            city_id=city_id,
            language=language,
        )

    async def create_city(
        self,
        *,
        platform_user_id: int | str,
        country_id: UUID | str,
        payload: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.create_city(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            country_id=country_id,
            payload=payload,
            language=language,
        )
        await self.session.commit()

        return result

    async def import_cities(
        self,
        *,
        platform_user_id: int | str,
        country_id: UUID | str,
        payload: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.import_cities(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            country_id=country_id,
            payload=payload,
        )
        await self.session.commit()

        return result

    async def update_city(
        self,
        *,
        platform_user_id: int | str,
        city_id: UUID | str,
        payload: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.update_city(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            city_id=city_id,
            payload=payload,
            language=language,
        )
        await self.session.commit()

        return result

    async def update_city_geo(
        self,
        *,
        platform_user_id: int | str,
        city_id: UUID | str,
        payload: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.update_city_geo(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            city_id=city_id,
            payload=payload,
            language=language,
        )
        await self.session.commit()

        return result

    async def toggle_city_visibility(
        self,
        *,
        platform_user_id: int | str,
        city_id: UUID | str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries
            .toggle_city_visibility(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                city_id=city_id,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def list_languages(
        self,
        *,
        platform_user_id: int | str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )

        items = await (
            self.dictionaries.list_language_cards(
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )
        visible_items = tuple(
            items[:normalized_page_size]
        )

        return AdminDictionariesPageResult(
            actor=actor,
            items=visible_items,
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def get_language(
        self,
        *,
        platform_user_id: int | str,
        code: str,
    ):
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await self.dictionaries.get_language_card(
            code=code,
        )

    async def create_language(
        self,
        *,
        platform_user_id: int | str,
        payload: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.create_language(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            payload=payload,
        )
        await self.session.commit()

        return result

    async def rename_language(
        self,
        *,
        platform_user_id: int | str,
        code: str,
        payload: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.rename_language(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            code=code,
            payload=payload,
        )
        await self.session.commit()

        return result

    async def toggle_language_visibility(
        self,
        *,
        platform_user_id: int | str,
        code: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries
            .toggle_language_visibility(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                code=code,
            )
        )
        await self.session.commit()

        return result

    async def list_skills(
        self,
        *,
        platform_user_id: int | str,
        language: str,
        page: int = 0,
        page_size: int = 5,
    ) -> AdminDictionariesPageResult:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 50),
        )

        items = await self.dictionaries.list_skill_cards(
            language=language,
            limit=normalized_page_size + 1,
            offset=(
                normalized_page
                * normalized_page_size
            ),
        )
        visible_items = tuple(
            items[:normalized_page_size]
        )

        return AdminDictionariesPageResult(
            actor=actor,
            items=visible_items,
            page=normalized_page,
            has_next=(
                len(items) > normalized_page_size
            ),
        )

    async def get_skill(
        self,
        *,
        platform_user_id: int | str,
        skill_id: UUID | str,
        language: str,
    ):
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await self.dictionaries.get_skill_card(
            skill_id=skill_id,
            language=language,
        )

    async def create_skill(
        self,
        *,
        platform_user_id: int | str,
        title: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.create_skill(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            title=title,
            language=language,
        )
        await self.session.commit()

        return result

    async def rename_skill(
        self,
        *,
        platform_user_id: int | str,
        skill_id: UUID | str,
        title: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.rename_skill(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            skill_id=skill_id,
            title=title,
            language=language,
        )
        await self.session.commit()

        return result

    async def toggle_skill_visibility(
        self,
        *,
        platform_user_id: int | str,
        skill_id: UUID | str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.dictionaries
            .toggle_skill_visibility(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                skill_id=skill_id,
                language=language,
            )
        )
        await self.session.commit()

        return result

    async def preview_skill_merge(
        self,
        *,
        platform_user_id: int | str,
        source_skill_id: UUID | str,
        target_skill_value: str,
        language: str,
    ):
        await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.dictionaries.preview_skill_merge(
                source_skill_id=source_skill_id,
                target_skill_value=(
                    target_skill_value
                ),
                language=language,
            )
        )

    async def merge_skills(
        self,
        *,
        platform_user_id: int | str,
        source_skill_id: UUID | str,
        target_skill_value: str,
        language: str,
    ):
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await self.dictionaries.merge_skills(
            admin_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            source_skill_id=source_skill_id,
            target_skill_value=target_skill_value,
            language=language,
        )
        await self.session.commit()

        return result
