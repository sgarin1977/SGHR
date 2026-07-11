from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AdminAction,
    City,
    Country,
    Language,
    Profession,
    ProfessionSkill,
    Skill,
    Specialist,
    SpecialistCategory,
    SpecialistLanguage,
    SpecialistProfession,
    UserSkill,
)


@dataclass(frozen=True)
class AdminCategoryDictionaryRow:
    category_id: UUID
    code: str
    name: str
    name_ru: str | None
    name_en: str | None
    name_pt: str | None
    sort_order: int
    is_active: bool
    metadata: dict
    professions_count: int
    specialists_count: int

@dataclass(frozen=True)
class AdminCategorySpecialistRow:
    specialist_id: UUID
    display_name: str
    status: str
    profession_names: str
    is_verified: bool
    is_available: bool

@dataclass(frozen=True)
class AdminProfessionDictionaryRow:
    profession_id: UUID
    category_id: UUID
    code: str
    name: str
    name_ru: str | None
    name_en: str | None
    name_pt: str | None
    normalized_name: str | None
    sort_order: int
    is_active: bool
    metadata: dict
    category_name: str
    specialists_count: int

@dataclass(frozen=True)
class AdminSkillDictionaryRow:
    skill_id: UUID
    code: str
    name: str
    name_ru: str | None
    name_en: str | None
    name_pt: str | None
    is_active: bool
    profession_links_count: int
    user_links_count: int
    vacancy_links_count: int

@dataclass(frozen=True)
class AdminSkillMergeResult:
    moved_profession_links: int
    removed_duplicate_profession_links: int
    moved_user_links: int
    removed_duplicate_user_links: int

@dataclass(frozen=True)
class AdminLanguageDictionaryRow:
    code: str
    name: str
    native_name: str | None
    is_active: bool
    specialist_links_count: int

@dataclass(frozen=True)
class AdminCountryDictionaryRow:
    country_id: UUID
    code: str
    name: str
    name_ru: str | None
    name_en: str | None
    name_pt: str | None
    default_language: str | None
    default_currency: str | None
    phone_code: str | None
    is_active: bool
    metadata: dict
    cities_count: int
    specialists_count: int

@dataclass(frozen=True)
class AdminDictionaryImportResult:
    created_count: int
    updated_count: int
    skipped_count: int
    errors: tuple[str, ...]

@dataclass(frozen=True)
class AdminSpecialistMoveResult:
    requested_count: int
    moved_count: int
    archived_duplicate_count: int
    synchronized_primary_count: int
    missing_count: int
    source_profession_id: UUID
    target_profession_id: UUID
    target_category_id: UUID

@dataclass(frozen=True)
class AdminMultiProfessionMoveResult:
    requested_specialists_count: int
    selected_professions_count: int
    created_links_count: int
    reactivated_links_count: int
    existing_links_count: int
    deleted_old_links_count: int
    synchronized_primary_count: int
    missing_specialists_count: int
    target_category_id: UUID
    target_profession_ids: tuple[UUID, ...]
    mode: str

@dataclass(frozen=True)
class AdminCategorySpecialistMoveResult:
    requested_count: int
    moved_count: int
    archived_duplicate_count: int
    archived_extra_links_count: int
    synchronized_primary_count: int
    missing_count: int
    source_category_id: UUID
    target_profession_id: UUID
    target_category_id: UUID



@dataclass(frozen=True)
class AdminCityDictionaryRow:
    city_id: UUID
    country_id: UUID
    country_name: str
    name: str
    name_ru: str | None
    name_en: str | None
    name_pt: str | None
    latitude: float | None
    longitude: float | None
    timezone: str | None
    is_active: bool
    metadata: dict
    specialists_count: int

class DictionaryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_categories_for_admin(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminCategoryDictionaryRow]:
        profession_counts = (
            select(
                Profession.category_id.label("category_id"),
                func.count(Profession.id).label("professions_count"),
            )
            .group_by(Profession.category_id)
            .subquery()
        )

        specialist_counts = (
            select(
                SpecialistProfession.category_id.label("category_id"),
                func.count(func.distinct(SpecialistProfession.specialist_id)).label(
                    "specialists_count"
                ),
            )
            .join(
                Specialist,
                Specialist.id == SpecialistProfession.specialist_id,
            )
            .where(
                SpecialistProfession.status == "active",
            )
            .group_by(SpecialistProfession.category_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                SpecialistCategory.id,
                SpecialistCategory.code,
                SpecialistCategory.name,
                SpecialistCategory.name_ru,
                SpecialistCategory.name_en,
                SpecialistCategory.name_pt,
                SpecialistCategory.sort_order,
                SpecialistCategory.is_active,
                SpecialistCategory.extra_metadata,
                func.coalesce(profession_counts.c.professions_count, 0),
                func.coalesce(specialist_counts.c.specialists_count, 0),
            )
            .outerjoin(
                profession_counts,
                profession_counts.c.category_id == SpecialistCategory.id,
            )
            .outerjoin(
                specialist_counts,
                specialist_counts.c.category_id == SpecialistCategory.id,
            )
            .order_by(
                SpecialistCategory.sort_order,
                SpecialistCategory.name,
            )
            .offset(offset)
            .limit(limit)
        )

        return [
            AdminCategoryDictionaryRow(
                category_id=category_id,
                code=code,
                name=name,
                name_ru=name_ru,
                name_en=name_en,
                name_pt=name_pt,
                sort_order=sort_order,
                is_active=is_active,
                metadata=metadata or {},
                professions_count=int(professions_count or 0),
                specialists_count=int(specialists_count or 0),
            )
            for (
                category_id,
                code,
                name,
                name_ru,
                name_en,
                name_pt,
                sort_order,
                is_active,
                metadata,
                professions_count,
                specialists_count,
            ) in result.all()
        ]
    async def get_category_for_admin(
        self,
        category_id: UUID,
    ) -> AdminCategoryDictionaryRow | None:
        profession_counts = (
            select(
                Profession.category_id.label("category_id"),
                func.count(Profession.id).label("professions_count"),
            )
            .where(Profession.category_id == category_id)
            .group_by(Profession.category_id)
            .subquery()
        )

        specialist_counts = (
            select(
                SpecialistProfession.category_id.label("category_id"),
                func.count(func.distinct(SpecialistProfession.specialist_id)).label(
                    "specialists_count"
                ),
            )
            .join(
                Specialist,
                Specialist.id == SpecialistProfession.specialist_id,
            )
            .where(
                SpecialistProfession.category_id == category_id,
                SpecialistProfession.status == "active",
            )
            .group_by(SpecialistProfession.category_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                SpecialistCategory.id,
                SpecialistCategory.code,
                SpecialistCategory.name,
                SpecialistCategory.name_ru,
                SpecialistCategory.name_en,
                SpecialistCategory.name_pt,
                SpecialistCategory.sort_order,
                SpecialistCategory.is_active,
                SpecialistCategory.extra_metadata,
                func.coalesce(profession_counts.c.professions_count, 0),
                func.coalesce(specialist_counts.c.specialists_count, 0),
            )
            .outerjoin(
                profession_counts,
                profession_counts.c.category_id == SpecialistCategory.id,
            )
            .outerjoin(
                specialist_counts,
                specialist_counts.c.category_id == SpecialistCategory.id,
            )
            .where(SpecialistCategory.id == category_id)
        )

        row = result.one_or_none()
        if not row:
            return None

        (
            row_category_id,
            code,
            name,
            name_ru,
            name_en,
            name_pt,
            sort_order,
            is_active,
            metadata,
            professions_count,
            specialists_count,
        ) = row

        return AdminCategoryDictionaryRow(
            category_id=row_category_id,
            code=code,
            name=name,
            name_ru=name_ru,
            name_en=name_en,
            name_pt=name_pt,
            sort_order=sort_order,
            is_active=is_active,
            metadata=metadata or {},
            professions_count=int(professions_count or 0),
            specialists_count=int(specialists_count or 0),
        )
    
    async def category_name_exists(
        self,
        *,
        category_id: UUID,
        title: str,
    ) -> bool:
        normalized_title = title.strip().lower()

        result = await self.session.execute(
            select(SpecialistCategory.id).where(
                SpecialistCategory.id != category_id,
                or_(
                    func.lower(func.trim(SpecialistCategory.name)) == normalized_title,
                    func.lower(func.trim(SpecialistCategory.name_ru)) == normalized_title,
                    func.lower(func.trim(SpecialistCategory.name_en)) == normalized_title,
                    func.lower(func.trim(SpecialistCategory.name_pt)) == normalized_title,
                ),
            )
        )

        return result.scalar_one_or_none() is not None

    async def rename_category_for_admin(
        self,
        *,
        category_id: UUID,
        language: str,
        title: str,
    ) -> AdminCategoryDictionaryRow | None:
        category = await self.session.get(SpecialistCategory, category_id)

        if not category:
            return None

        if language == "en":
            category.name_en = title
        elif language == "pt":
            category.name_pt = title
        else:
            category.name_ru = title
            category.name = title

        await self.session.flush()

        return await self.get_category_for_admin(category_id)

    async def set_category_visibility_for_admin(
        self,
        *,
        category_id: UUID,
        is_active: bool,
    ) -> AdminCategoryDictionaryRow | None:
        category = await self.session.get(SpecialistCategory, category_id)

        if not category:
            return None

        category.is_active = is_active
        await self.session.flush()

        return await self.get_category_for_admin(category_id)

    async def archive_category_for_admin(
        self,
        *,
        category_id: UUID,
        admin_user_id: UUID,
    ) -> AdminCategoryDictionaryRow | None:
        category = await self.session.get(SpecialistCategory, category_id)

        if not category:
            return None

        metadata = dict(category.extra_metadata or {})
        metadata["archived"] = True
        metadata["archived_by"] = str(admin_user_id)

        category.extra_metadata = metadata
        category.is_active = False

        await self.session.flush()

        return await self.get_category_for_admin(category_id)

    async def unarchive_category_for_admin(
        self,
        *,
        category_id: UUID,
    ) -> AdminCategoryDictionaryRow | None:
        category = await self.session.get(SpecialistCategory, category_id)

        if not category:
            return None

        metadata = dict(category.extra_metadata or {})
        metadata.pop("archived", None)
        metadata.pop("archived_by", None)

        category.extra_metadata = metadata

        await self.session.flush()

        return await self.get_category_for_admin(category_id)

    async def update_category_sort_order_for_admin(
        self,
        *,
        category_id: UUID,
        sort_order: int,
    ) -> AdminCategoryDictionaryRow | None:
        category = await self.session.get(SpecialistCategory, category_id)

        if not category:
            return None

        category.sort_order = sort_order
        await self.session.flush()

        return await self.get_category_for_admin(category_id)

    async def log_dictionary_admin_action(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        action_type: str,
        target_type: str,
        target_id: UUID,
        before_state: dict,
        after_state: dict,
        reason: str,
    ) -> AdminAction:
        action = AdminAction(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def find_categories_by_title_for_admin(
        self,
        *,
        title: str,
        limit: int = 10,
    ) -> list[AdminCategoryDictionaryRow]:
        normalized_title = " ".join(
            (title or "").split()
        ).lower()

        if not normalized_title:
            return []

        result = await self.session.execute(
            select(SpecialistCategory.id)
            .where(
                or_(
                    func.lower(
                        func.trim(SpecialistCategory.name)
                    ) == normalized_title,
                    func.lower(
                        func.trim(SpecialistCategory.name_ru)
                    ) == normalized_title,
                    func.lower(
                        func.trim(SpecialistCategory.name_en)
                    ) == normalized_title,
                    func.lower(
                        func.trim(SpecialistCategory.name_pt)
                    ) == normalized_title,
                )
            )
            .order_by(
                SpecialistCategory.is_active.desc(),
                SpecialistCategory.sort_order,
                SpecialistCategory.name,
            )
            .limit(limit)
        )

        category_ids = list(result.scalars().all())
        rows = []

        for category_id in category_ids:
            row = await self.get_category_for_admin(
                category_id
            )

            if row:
                rows.append(row)

        return rows

    async def category_title_exists(
        self,
        *,
        title: str,
    ) -> bool:
        normalized_title = title.strip().lower()

        result = await self.session.execute(
            select(SpecialistCategory.id).where(
                or_(
                    func.lower(func.trim(SpecialistCategory.name)) == normalized_title,
                    func.lower(func.trim(SpecialistCategory.name_ru)) == normalized_title,
                    func.lower(func.trim(SpecialistCategory.name_en)) == normalized_title,
                    func.lower(func.trim(SpecialistCategory.name_pt)) == normalized_title,
                ),
            )
        )

        return result.scalar_one_or_none() is not None

    async def category_code_exists(
        self,
        *,
        code: str,
    ) -> bool:
        result = await self.session.execute(
            select(SpecialistCategory.id).where(
                func.lower(SpecialistCategory.code) == code.strip().lower(),
            )
        )

        return result.scalar_one_or_none() is not None

    async def next_category_sort_order(self) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(SpecialistCategory.sort_order), 0))
        )

        return int(result.scalar_one() or 0) + 10

    async def create_category_for_admin(
        self,
        *,
        code: str,
        title: str,
        language: str,
        sort_order: int,
    ) -> AdminCategoryDictionaryRow:
        name_ru = title if language == "ru" else None
        name_en = title if language == "en" else None
        name_pt = title if language == "pt" else None

        category = SpecialistCategory(
            code=code,
            name=title,
            name_ru=name_ru or title,
            name_en=name_en,
            name_pt=name_pt,
            sort_order=sort_order,
            is_active=True,
            extra_metadata={
                "source": "super_admin",
                "release": "specialists_directory_v1",
            },
        )

        self.session.add(category)
        await self.session.flush()

        return await self.get_category_for_admin(category.id)
    
    async def list_category_specialist_ids_for_admin(
        self,
        *,
        category_id: UUID,
    ) -> list[UUID]:
        result = await self.session.execute(
            select(
                SpecialistProfession.specialist_id
            )
            .where(
                SpecialistProfession.category_id
                == category_id,
                SpecialistProfession.status == "active",
            )
            .distinct()
            .order_by(
                SpecialistProfession.specialist_id
            )
        )

        return list(result.scalars().all())

    async def list_category_specialists_for_admin(
        self,
        *,
        category_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminCategorySpecialistRow]:
        result = await self.session.execute(
            select(
                Specialist.id,
                Specialist.display_name,
                Specialist.status,
                func.string_agg(
                    func.distinct(Profession.name),
                    ", ",
                ).label("profession_names"),
                Specialist.is_verified,
                Specialist.is_available,
            )
            .join(
                SpecialistProfession,
                SpecialistProfession.specialist_id == Specialist.id,
            )
            .join(
                Profession,
                Profession.id == SpecialistProfession.profession_id,
            )
            .where(
                SpecialistProfession.category_id == category_id,
                SpecialistProfession.status == "active",
            )
            .group_by(
                Specialist.id,
                Specialist.display_name,
                Specialist.status,
                Specialist.is_verified,
                Specialist.is_available,
            )
            .order_by(
                Specialist.display_name,
                Specialist.id,
            )
            .offset(offset)
            .limit(limit)
        )

        return [
            AdminCategorySpecialistRow(
                specialist_id=specialist_id,
                display_name=display_name,
                status=status,
                profession_names=profession_names or "-",
                is_verified=bool(is_verified),
                is_available=bool(is_available),
            )
            for (
                specialist_id,
                display_name,
                status,
                profession_names,
                is_verified,
                is_available,
            ) in result.all()
        ]
    
    async def list_professions_by_category_for_admin(
        self,
        *,
        category_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AdminProfessionDictionaryRow]:
        result = await self.session.execute(
            select(Profession.id)
            .where(
                Profession.category_id == category_id
            )
            .order_by(
                Profession.sort_order,
                Profession.name,
                Profession.id,
            )
            .offset(offset)
            .limit(limit)
        )

        profession_ids = list(result.scalars().all())
        rows = []

        for profession_id in profession_ids:
            row = await self.get_profession_for_admin(
                profession_id
            )

            if row:
                rows.append(row)

        return rows

    async def list_professions_for_admin(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminProfessionDictionaryRow]:
        specialist_counts = (
            select(
                SpecialistProfession.profession_id.label("profession_id"),
                func.count(func.distinct(SpecialistProfession.specialist_id)).label(
                    "specialists_count"
                ),
            )
            .where(SpecialistProfession.status == "active")
            .group_by(SpecialistProfession.profession_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                Profession.id,
                Profession.category_id,
                Profession.code,
                Profession.name,
                Profession.name_ru,
                Profession.name_en,
                Profession.name_pt,
                Profession.normalized_name,
                Profession.sort_order,
                Profession.is_active,
                Profession.extra_metadata,
                SpecialistCategory.name,
                func.coalesce(specialist_counts.c.specialists_count, 0),
            )
            .join(
                SpecialistCategory,
                SpecialistCategory.id == Profession.category_id,
            )
            .outerjoin(
                specialist_counts,
                specialist_counts.c.profession_id == Profession.id,
            )
            .order_by(
                SpecialistCategory.sort_order,
                Profession.sort_order,
                Profession.name,
            )
            .offset(offset)
            .limit(limit)
        )

        return [
            AdminProfessionDictionaryRow(
                profession_id=profession_id,
                category_id=category_id,
                code=code,
                name=name,
                name_ru=name_ru,
                name_en=name_en,
                name_pt=name_pt,
                normalized_name=normalized_name,
                sort_order=sort_order,
                is_active=is_active,
                metadata=metadata or {},
                category_name=category_name,
                specialists_count=int(specialists_count or 0),
            )
            for (
                profession_id,
                category_id,
                code,
                name,
                name_ru,
                name_en,
                name_pt,
                normalized_name,
                sort_order,
                is_active,
                metadata,
                category_name,
                specialists_count,
            ) in result.all()
        ]

    async def get_profession_for_admin(
        self,
        profession_id: UUID,
    ) -> AdminProfessionDictionaryRow | None:
        specialist_counts = (
            select(
                SpecialistProfession.profession_id.label("profession_id"),
                func.count(func.distinct(SpecialistProfession.specialist_id)).label(
                    "specialists_count"
                ),
            )
            .where(
                SpecialistProfession.profession_id == profession_id,
                SpecialistProfession.status == "active",
            )
            .group_by(SpecialistProfession.profession_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                Profession.id,
                Profession.category_id,
                Profession.code,
                Profession.name,
                Profession.name_ru,
                Profession.name_en,
                Profession.name_pt,
                Profession.normalized_name,
                Profession.sort_order,
                Profession.is_active,
                Profession.extra_metadata,
                SpecialistCategory.name,
                func.coalesce(specialist_counts.c.specialists_count, 0),
            )
            .join(
                SpecialistCategory,
                SpecialistCategory.id == Profession.category_id,
            )
            .outerjoin(
                specialist_counts,
                specialist_counts.c.profession_id == Profession.id,
            )
            .where(Profession.id == profession_id)
        )

        row = result.one_or_none()
        if not row:
            return None

        (
            profession_id,
            category_id,
            code,
            name,
            name_ru,
            name_en,
            name_pt,
            normalized_name,
            sort_order,
            is_active,
            metadata,
            category_name,
            specialists_count,
        ) = row

        return AdminProfessionDictionaryRow(
            profession_id=profession_id,
            category_id=category_id,
            code=code,
            name=name,
            name_ru=name_ru,
            name_en=name_en,
            name_pt=name_pt,
            normalized_name=normalized_name,
            sort_order=sort_order,
            is_active=is_active,
            metadata=metadata or {},
            category_name=category_name,
            specialists_count=int(specialists_count or 0),
        )
    
    async def get_category_by_code_for_admin(
        self,
        code: str,
    ) -> AdminCategoryDictionaryRow | None:
        result = await self.session.execute(
            select(SpecialistCategory.id).where(
                func.lower(SpecialistCategory.code) == code.strip().lower()
            )
        )

        category_id = result.scalar_one_or_none()

        if not category_id:
            return None

        return await self.get_category_for_admin(category_id)

    async def profession_title_exists(
        self,
        *,
        category_id: UUID,
        title: str,
    ) -> bool:
        normalized_title = title.strip().lower()

        result = await self.session.execute(
            select(Profession.id).where(
                Profession.category_id == category_id,
                or_(
                    func.lower(func.trim(Profession.name)) == normalized_title,
                    func.lower(func.trim(Profession.name_ru)) == normalized_title,
                    func.lower(func.trim(Profession.name_en)) == normalized_title,
                    func.lower(func.trim(Profession.name_pt)) == normalized_title,
                    func.lower(func.trim(Profession.normalized_name)) == normalized_title,
                ),
            )
        )

        return result.scalar_one_or_none() is not None

    async def find_professions_by_title_for_admin(
        self,
        *,
        title: str,
        limit: int = 10,
    ) -> list[AdminProfessionDictionaryRow]:
        normalized_title = " ".join((title or "").split()).lower()

        if not normalized_title:
            return []

        result = await self.session.execute(
            select(Profession.id)
            .where(
                or_(
                    func.lower(func.trim(Profession.name))
                    == normalized_title,
                    func.lower(func.trim(Profession.name_ru))
                    == normalized_title,
                    func.lower(func.trim(Profession.name_en))
                    == normalized_title,
                    func.lower(func.trim(Profession.name_pt))
                    == normalized_title,
                )
            )
            .order_by(
                Profession.is_active.desc(),
                Profession.sort_order,
                Profession.name,
            )
            .limit(limit)
        )

        profession_ids = list(result.scalars().all())
        rows = []

        for profession_id in profession_ids:
            row = await self.get_profession_for_admin(profession_id)

            if row:
                rows.append(row)

        return rows

    async def profession_code_exists(
        self,
        *,
        code: str,
    ) -> bool:
        result = await self.session.execute(
            select(Profession.id).where(
                func.lower(Profession.code) == code.strip().lower()
            )
        )

        return result.scalar_one_or_none() is not None

    async def next_profession_sort_order(
        self,
        *,
        category_id: UUID,
    ) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(Profession.sort_order), 0)).where(
                Profession.category_id == category_id
            )
        )

        return int(result.scalar_one() or 0) + 10

    async def create_profession_for_admin(
        self,
        *,
        category_id: UUID,
        code: str,
        title: str,
        language: str,
        sort_order: int,
        release: str | None,
    ) -> AdminProfessionDictionaryRow:
        name_ru = title if language == "ru" else None
        name_en = title if language == "en" else None
        name_pt = title if language == "pt" else None

        profession = Profession(
            category_id=category_id,
            code=code,
            name=title,
            name_ru=name_ru or title,
            name_en=name_en,
            name_pt=name_pt,
            normalized_name=title.strip().lower(),
            sort_order=sort_order,
            is_active=True,
            extra_metadata={
                "source": "super_admin",
                "release": release or "specialists_directory_v1",
            },
        )

        self.session.add(profession)
        await self.session.flush()

        return await self.get_profession_for_admin(profession.id)
    
    async def rename_profession_for_admin(
        self,
        *,
        profession_id: UUID,
        language: str,
        title: str,
    ) -> AdminProfessionDictionaryRow | None:
        profession = await self.session.get(Profession, profession_id)

        if not profession:
            return None

        if language == "en":
            profession.name_en = title
        elif language == "pt":
            profession.name_pt = title
        else:
            profession.name_ru = title
            profession.name = title

        profession.normalized_name = title.strip().lower()

        await self.session.flush()

        return await self.get_profession_for_admin(profession_id)
    
    async def move_profession_to_category_for_admin(
        self,
        *,
        profession_id: UUID,
        category_id: UUID,
    ) -> AdminProfessionDictionaryRow | None:
        profession = await self.session.get(Profession, profession_id)

        if not profession:
            return None

        profession.category_id = category_id

        await self.session.execute(
            update(SpecialistProfession)
            .where(SpecialistProfession.profession_id == profession_id)
            .values(category_id=category_id)
        )

        await self.session.flush()

        return await self.get_profession_for_admin(profession_id)
    
    async def set_profession_visibility_for_admin(
        self,
        *,
        profession_id: UUID,
        is_active: bool,
    ) -> AdminProfessionDictionaryRow | None:
        profession = await self.session.get(Profession, profession_id)

        if not profession:
            return None

        profession.is_active = is_active
        await self.session.flush()

        return await self.get_profession_for_admin(profession_id)
    
    async def archive_profession_for_admin(
        self,
        *,
        profession_id: UUID,
        admin_user_id: UUID,
    ) -> AdminProfessionDictionaryRow | None:
        profession = await self.session.get(Profession, profession_id)

        if not profession:
            return None

        metadata = dict(profession.extra_metadata or {})
        metadata["archived"] = True
        metadata["archived_by"] = str(admin_user_id)

        profession.extra_metadata = metadata
        profession.is_active = False

        await self.session.flush()

        return await self.get_profession_for_admin(profession_id)

    async def unarchive_profession_for_admin(
        self,
        *,
        profession_id: UUID,
    ) -> AdminProfessionDictionaryRow | None:
        profession = await self.session.get(Profession, profession_id)

        if not profession:
            return None

        metadata = dict(profession.extra_metadata or {})
        metadata.pop("archived", None)
        metadata.pop("archived_by", None)

        profession.extra_metadata = metadata

        await self.session.flush()

        return await self.get_profession_for_admin(profession_id)

    async def list_profession_specialist_ids_for_admin(
        self,
        *,
        profession_id: UUID,
    ) -> list[UUID]:
        result = await self.session.execute(
            select(SpecialistProfession.specialist_id)
            .where(
                SpecialistProfession.profession_id == profession_id,
                SpecialistProfession.status == "active",
            )
            .order_by(SpecialistProfession.specialist_id)
        )

        return list(result.scalars().all())

    async def move_specialists_to_multiple_professions_for_admin(
        self,
        *,
        source_type: str,
        source_id: UUID,
        target_category_id: UUID,
        target_profession_ids: list[UUID],
        specialist_ids: list[UUID],
        mode: str,
    ) -> AdminMultiProfessionMoveResult:
        unique_specialist_ids = list(
            dict.fromkeys(specialist_ids)
        )
        unique_target_profession_ids = list(
            dict.fromkeys(target_profession_ids)
        )

        if (
            not unique_specialist_ids
            or not unique_target_profession_ids
        ):
            return AdminMultiProfessionMoveResult(
                requested_specialists_count=len(
                    unique_specialist_ids
                ),
                selected_professions_count=len(
                    unique_target_profession_ids
                ),
                created_links_count=0,
                reactivated_links_count=0,
                existing_links_count=0,
                deleted_old_links_count=0,
                synchronized_primary_count=0,
                missing_specialists_count=len(
                    unique_specialist_ids
                ),
                target_category_id=target_category_id,
                target_profession_ids=tuple(
                    unique_target_profession_ids
                ),
                mode=mode,
            )

        specialist_result = await self.session.execute(
            select(Specialist).where(
                Specialist.id.in_(
                    unique_specialist_ids
                )
            )
        )
        specialists_by_id = {
            specialist.id: specialist
            for specialist
            in specialist_result.scalars().all()
        }

        source_statement = select(
            SpecialistProfession
        ).where(
            SpecialistProfession.specialist_id.in_(
                unique_specialist_ids
            ),
            SpecialistProfession.status == "active",
        )

        if source_type == "category":
            source_statement = source_statement.where(
                SpecialistProfession.category_id == source_id
            )
        else:
            source_statement = source_statement.where(
                SpecialistProfession.profession_id == source_id
            )

        source_result = await self.session.execute(
            source_statement.order_by(
                SpecialistProfession.specialist_id,
                SpecialistProfession.is_primary.desc(),
                SpecialistProfession.created_at,
                SpecialistProfession.id,
            )
        )

        source_links_by_specialist = {}

        for link in source_result.scalars().all():
            source_links_by_specialist.setdefault(
                link.specialist_id,
                [],
            ).append(link)

        target_result = await self.session.execute(
            select(SpecialistProfession).where(
                SpecialistProfession.specialist_id.in_(
                    unique_specialist_ids
                ),
                SpecialistProfession.profession_id.in_(
                    unique_target_profession_ids
                ),
                SpecialistProfession.status.in_(
                    {"active", "paused"}
                ),
            )
        )

        target_links = {
            (
                link.specialist_id,
                link.profession_id,
            ): link
            for link in target_result.scalars().all()
        }

        active_result = await self.session.execute(
            select(SpecialistProfession).where(
                SpecialistProfession.specialist_id.in_(
                    unique_specialist_ids
                ),
                SpecialistProfession.status == "active",
            )
        )

        active_links_by_specialist = {}

        for link in active_result.scalars().all():
            active_links_by_specialist.setdefault(
                link.specialist_id,
                [],
            ).append(link)

        created_links_count = 0
        reactivated_links_count = 0
        existing_links_count = 0
        deleted_old_links_count = 0
        missing_specialists_count = 0

        for specialist_id in unique_specialist_ids:
            specialist = specialists_by_id.get(
                specialist_id
            )

            if not specialist:
                missing_specialists_count += 1
                continue

            source_links = source_links_by_specialist.get(
                specialist_id,
                [],
            )
            active_links = active_links_by_specialist.get(
                specialist_id,
                [],
            )
            selected_target_links = []

            for profession_id in unique_target_profession_ids:
                key = (
                    specialist_id,
                    profession_id,
                )
                target_link = target_links.get(key)

                if target_link:
                    target_link.category_id = (
                        target_category_id
                    )

                    if target_link.status == "paused":
                        target_link.status = "active"
                        target_link.updated_at = func.now()
                        reactivated_links_count += 1
                    else:
                        existing_links_count += 1

                else:
                    target_link = SpecialistProfession(
                        specialist_id=specialist_id,
                        category_id=target_category_id,
                        profession_id=profession_id,
                        is_primary=False,
                        status="active",
                    )
                    self.session.add(target_link)
                    target_links[key] = target_link
                    active_links.append(target_link)
                    created_links_count += 1

                selected_target_links.append(
                    target_link
                )

            primary_was_removed = False

            if mode == "replace":
                for source_link in source_links:
                    if (
                        source_link.profession_id
                        in unique_target_profession_ids
                    ):
                        continue

                    if source_link.is_primary:
                        primary_was_removed = True

                    source_link.status = "deleted"
                    source_link.is_primary = False
                    source_link.updated_at = func.now()
                    deleted_old_links_count += 1

            active_primary_links = [
                link
                for link in active_links
                if link.status == "active"
                and link.is_primary
            ]

            if (
                primary_was_removed
                or not active_primary_links
            ):
                new_primary_link = selected_target_links[0]

                for link in active_links:
                    if link.status == "active":
                        link.is_primary = (
                            link is new_primary_link
                        )

                new_primary_link.is_primary = True

        await self.session.flush()

        synchronized_primary_count = (
            await self
            .sync_specialist_primary_professions_for_admin(
                specialist_ids=unique_specialist_ids,
            )
        )

        return AdminMultiProfessionMoveResult(
            requested_specialists_count=len(
                unique_specialist_ids
            ),
            selected_professions_count=len(
                unique_target_profession_ids
            ),
            created_links_count=created_links_count,
            reactivated_links_count=(
                reactivated_links_count
            ),
            existing_links_count=existing_links_count,
            deleted_old_links_count=(
                deleted_old_links_count
            ),
            synchronized_primary_count=(
                synchronized_primary_count
            ),
            missing_specialists_count=(
                missing_specialists_count
            ),
            target_category_id=target_category_id,
            target_profession_ids=tuple(
                unique_target_profession_ids
            ),
            mode=mode,
        )

    async def sync_specialist_primary_professions_for_admin(
        self,
        *,
        specialist_ids: list[UUID],
    ) -> int:
        unique_specialist_ids = list(
            dict.fromkeys(specialist_ids)
        )

        if not unique_specialist_ids:
            return 0

        primary_result = await self.session.execute(
            select(SpecialistProfession)
            .where(
                SpecialistProfession.specialist_id.in_(
                    unique_specialist_ids
                ),
                SpecialistProfession.status == "active",
                SpecialistProfession.is_primary.is_(True),
            )
            .order_by(
                SpecialistProfession.specialist_id,
                SpecialistProfession.updated_at.desc(),
                SpecialistProfession.id,
            )
        )

        primary_links = {}

        for link in primary_result.scalars().all():
            primary_links.setdefault(
                link.specialist_id,
                link,
            )

        specialist_result = await self.session.execute(
            select(Specialist).where(
                Specialist.id.in_(unique_specialist_ids)
            )
        )

        synchronized_count = 0

        for specialist in specialist_result.scalars().all():
            primary_link = primary_links.get(
                specialist.id
            )

            if not primary_link:
                continue

            if (
                specialist.category_id
                == primary_link.category_id
                and specialist.profession_id
                == primary_link.profession_id
            ):
                continue

            specialist.category_id = primary_link.category_id
            specialist.profession_id = (
                primary_link.profession_id
            )
            specialist.updated_at = func.now()
            synchronized_count += 1

        await self.session.flush()

        return synchronized_count

    async def move_category_specialists_to_profession_for_admin(
        self,
        *,
        source_category_id: UUID,
        target_profession_id: UUID,
        target_category_id: UUID,
        specialist_ids: list[UUID],
    ) -> AdminCategorySpecialistMoveResult:
        unique_specialist_ids = list(dict.fromkeys(specialist_ids))

        if not unique_specialist_ids:
            return AdminCategorySpecialistMoveResult(
                requested_count=0,
                moved_count=0,
                archived_duplicate_count=0,
                archived_extra_links_count=0,
                synchronized_primary_count=0,
                missing_count=0,
                source_category_id=source_category_id,
                target_profession_id=target_profession_id,
                target_category_id=target_category_id,
            )

        source_result = await self.session.execute(
            select(SpecialistProfession)
            .where(
                SpecialistProfession.category_id
                == source_category_id,
                SpecialistProfession.specialist_id.in_(
                    unique_specialist_ids
                ),
                SpecialistProfession.status == "active",
            )
            .order_by(
                SpecialistProfession.specialist_id,
                SpecialistProfession.is_primary.desc(),
                SpecialistProfession.created_at,
                SpecialistProfession.id,
            )
        )

        source_links_by_specialist = {}

        for link in source_result.scalars().all():
            source_links_by_specialist.setdefault(
                link.specialist_id,
                [],
            ).append(link)

        target_result = await self.session.execute(
            select(SpecialistProfession.specialist_id).where(
                SpecialistProfession.profession_id
                == target_profession_id,
                SpecialistProfession.specialist_id.in_(
                    unique_specialist_ids
                ),
                SpecialistProfession.status == "active",
            )
        )

        target_specialist_ids = set(
            target_result.scalars().all()
        )

        moved_count = 0
        archived_duplicate_count = 0
        archived_extra_links_count = 0
        missing_count = 0

        for specialist_id in unique_specialist_ids:
            source_links = source_links_by_specialist.get(
                specialist_id,
                [],
            )

            if not source_links:
                missing_count += 1
                continue

            if specialist_id in target_specialist_ids:
                archived_duplicate_count += 1

                for link in source_links:
                    if (
                        link.profession_id
                        == target_profession_id
                    ):
                        continue

                    link.status = "deleted"
                    link.updated_at = func.now()
                    archived_extra_links_count += 1

                continue

            primary_link = source_links[0]
            primary_link.category_id = target_category_id
            primary_link.profession_id = target_profession_id
            primary_link.updated_at = func.now()
            moved_count += 1

            for extra_link in source_links[1:]:
                extra_link.status = "deleted"
                extra_link.updated_at = func.now()
                archived_extra_links_count += 1

        await self.session.flush()

        synchronized_primary_count = (
            await self
            .sync_specialist_primary_professions_for_admin(
                specialist_ids=unique_specialist_ids,
            )
        )

        return AdminCategorySpecialistMoveResult(
            requested_count=len(unique_specialist_ids),
            moved_count=moved_count,
            archived_duplicate_count=(
                archived_duplicate_count
            ),
            archived_extra_links_count=(
                archived_extra_links_count
            ),
            synchronized_primary_count=(
                synchronized_primary_count
            ),
            missing_count=missing_count,
            source_category_id=source_category_id,
            target_profession_id=target_profession_id,
            target_category_id=target_category_id,
        )

    async def move_specialists_to_profession_for_admin(
        self,
        *,
        source_profession_id: UUID,
        target_profession_id: UUID,
        target_category_id: UUID,
        specialist_ids: list[UUID],
    ) -> AdminSpecialistMoveResult:
        if not specialist_ids:
            return AdminSpecialistMoveResult(
                requested_count=0,
                moved_count=0,
                archived_duplicate_count=0,
                synchronized_primary_count=0,
                missing_count=0,
                source_profession_id=source_profession_id,
                target_profession_id=target_profession_id,
                target_category_id=target_category_id,
            )

        source_result = await self.session.execute(
            select(SpecialistProfession).where(
                SpecialistProfession.profession_id == source_profession_id,
                SpecialistProfession.specialist_id.in_(specialist_ids),
                SpecialistProfession.status == "active",
            )
        )
        source_links = list(source_result.scalars().all())

        target_result = await self.session.execute(
            select(SpecialistProfession.specialist_id).where(
                SpecialistProfession.profession_id == target_profession_id,
                SpecialistProfession.specialist_id.in_(specialist_ids),
                SpecialistProfession.status == "active",
            )
        )
        target_specialist_ids = {
            specialist_id
            for specialist_id in target_result.scalars().all()
        }

        moved_count = 0
        archived_duplicate_count = 0

        for link in source_links:
            if link.specialist_id in target_specialist_ids:
                link.status = "deleted"
                link.updated_at = func.now()
                archived_duplicate_count += 1
                continue

            link.category_id = target_category_id
            link.profession_id = target_profession_id
            link.updated_at = func.now()
            moved_count += 1

        await self.session.flush()

        synchronized_primary_count = (
            await self
            .sync_specialist_primary_professions_for_admin(
                specialist_ids=specialist_ids,
            )
        )

        processed_count = moved_count + archived_duplicate_count
        missing_count = max(len(set(specialist_ids)) - processed_count, 0)

        return AdminSpecialistMoveResult(
            requested_count=len(set(specialist_ids)),
            moved_count=moved_count,
            archived_duplicate_count=archived_duplicate_count,
            synchronized_primary_count=(
                synchronized_primary_count
            ),
            missing_count=missing_count,
            source_profession_id=source_profession_id,
            target_profession_id=target_profession_id,
            target_category_id=target_category_id,
        )


    async def list_profession_specialists_for_admin(
        self,
        *,
        profession_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminCategorySpecialistRow]:
        result = await self.session.execute(
            select(
                Specialist.id,
                Specialist.display_name,
                Specialist.status,
                func.string_agg(
                    func.distinct(Profession.name),
                    ", ",
                ).label("profession_names"),
                Specialist.is_verified,
                Specialist.is_available,
            )
            .join(
                SpecialistProfession,
                SpecialistProfession.specialist_id == Specialist.id,
            )
            .join(
                Profession,
                Profession.id == SpecialistProfession.profession_id,
            )
            .where(
                SpecialistProfession.profession_id == profession_id,
                SpecialistProfession.status == "active",
            )
            .group_by(
                Specialist.id,
                Specialist.display_name,
                Specialist.status,
                Specialist.is_verified,
                Specialist.is_available,
            )
            .order_by(
                Specialist.display_name,
                Specialist.id,
            )
            .offset(offset)
            .limit(limit)
        )

        return [
            AdminCategorySpecialistRow(
                specialist_id=specialist_id,
                display_name=display_name,
                status=status,
                profession_names=profession_names or "-",
                is_verified=bool(is_verified),
                is_available=bool(is_available),
            )
            for (
                specialist_id,
                display_name,
                status,
                profession_names,
                is_verified,
                is_available,
            ) in result.all()
        ]
    
    async def list_skills_for_admin(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminSkillDictionaryRow]:
        profession_counts = (
            select(
                ProfessionSkill.skill_id.label("skill_id"),
                func.count(ProfessionSkill.id).label("profession_links_count"),
            )
            .group_by(ProfessionSkill.skill_id)
            .subquery()
        )

        user_counts = (
            select(
                UserSkill.skill_id.label("skill_id"),
                func.count(UserSkill.id).label("user_links_count"),
            )
            .group_by(UserSkill.skill_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                Skill.id,
                Skill.code,
                Skill.name,
                Skill.name_ru,
                Skill.name_en,
                Skill.name_pt,
                Skill.is_active,
                func.coalesce(profession_counts.c.profession_links_count, 0),
                func.coalesce(user_counts.c.user_links_count, 0),
                literal(0),
            )
            .outerjoin(
                profession_counts,
                profession_counts.c.skill_id == Skill.id,
            )
            .outerjoin(
                user_counts,
                user_counts.c.skill_id == Skill.id,
            )
            .order_by(
                Skill.name,
                Skill.code,
            )
            .offset(offset)
            .limit(limit)
        )

        return [
            AdminSkillDictionaryRow(
                skill_id=skill_id,
                code=code,
                name=name,
                name_ru=name_ru,
                name_en=name_en,
                name_pt=name_pt,
                is_active=is_active,
                profession_links_count=int(profession_links_count or 0),
                user_links_count=int(user_links_count or 0),
                vacancy_links_count=int(vacancy_links_count or 0),
            )
            for (
                skill_id,
                code,
                name,
                name_ru,
                name_en,
                name_pt,
                is_active,
                profession_links_count,
                user_links_count,
                vacancy_links_count,
            ) in result.all()
        ]

    async def get_skill_for_admin(
        self,
        skill_id: UUID,
    ) -> AdminSkillDictionaryRow | None:
        profession_counts = (
            select(
                ProfessionSkill.skill_id.label("skill_id"),
                func.count(ProfessionSkill.id).label("profession_links_count"),
            )
            .where(ProfessionSkill.skill_id == skill_id)
            .group_by(ProfessionSkill.skill_id)
            .subquery()
        )

        user_counts = (
            select(
                UserSkill.skill_id.label("skill_id"),
                func.count(UserSkill.id).label("user_links_count"),
            )
            .where(UserSkill.skill_id == skill_id)
            .group_by(UserSkill.skill_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                Skill.id,
                Skill.code,
                Skill.name,
                Skill.name_ru,
                Skill.name_en,
                Skill.name_pt,
                Skill.is_active,
                func.coalesce(profession_counts.c.profession_links_count, 0),
                func.coalesce(user_counts.c.user_links_count, 0),
                literal(0),
            )
            .outerjoin(
                profession_counts,
                profession_counts.c.skill_id == Skill.id,
            )
            .outerjoin(
                user_counts,
                user_counts.c.skill_id == Skill.id,
            )
            .where(Skill.id == skill_id)
        )

        row = result.one_or_none()

        if not row:
            return None

        (
            skill_id,
            code,
            name,
            name_ru,
            name_en,
            name_pt,
            is_active,
            profession_links_count,
            user_links_count,
            vacancy_links_count,
        ) = row

        return AdminSkillDictionaryRow(
            skill_id=skill_id,
            code=code,
            name=name,
            name_ru=name_ru,
            name_en=name_en,
            name_pt=name_pt,
            is_active=is_active,
            profession_links_count=int(profession_links_count or 0),
            user_links_count=int(user_links_count or 0),
            vacancy_links_count=int(vacancy_links_count or 0),
        )

    async def get_skill_by_code_or_title_for_admin(
        self,
        value: str,
    ) -> AdminSkillDictionaryRow | None:
        normalized_value = value.strip().lower()

        result = await self.session.execute(
            select(Skill.id).where(
                or_(
                    func.lower(Skill.code) == normalized_value,
                    func.lower(Skill.name) == normalized_value,
                    func.lower(Skill.name_ru) == normalized_value,
                    func.lower(Skill.name_en) == normalized_value,
                    func.lower(Skill.name_pt) == normalized_value,
                )
            )
        )

        skill_id = result.scalar_one_or_none()

        if not skill_id:
            return None

        return await self.get_skill_for_admin(skill_id)

    async def skill_title_exists(
        self,
        *,
        title: str,
    ) -> bool:
        normalized_title = title.strip().lower()

        result = await self.session.execute(
            select(Skill.id).where(
                or_(
                    func.lower(func.trim(Skill.name)) == normalized_title,
                    func.lower(func.trim(Skill.name_ru)) == normalized_title,
                    func.lower(func.trim(Skill.name_en)) == normalized_title,
                    func.lower(func.trim(Skill.name_pt)) == normalized_title,
                ),
            )
        )

        return result.scalar_one_or_none() is not None

    async def skill_code_exists(
        self,
        *,
        code: str,
    ) -> bool:
        result = await self.session.execute(
            select(Skill.id).where(
                func.lower(Skill.code) == code.strip().lower()
            )
        )

        return result.scalar_one_or_none() is not None

    async def create_skill_for_admin(
        self,
        *,
        code: str,
        title: str,
        language: str,
    ) -> AdminSkillDictionaryRow:
        name_ru = title if language == "ru" else None
        name_en = title if language == "en" else None
        name_pt = title if language == "pt" else None

        skill = Skill(
            code=code,
            name=title,
            name_ru=name_ru or title,
            name_en=name_en,
            name_pt=name_pt,
            is_active=True,
        )

        self.session.add(skill)
        await self.session.flush()

        return await self.get_skill_for_admin(skill.id)
    
    async def rename_skill_for_admin(
        self,
        *,
        skill_id: UUID,
        language: str,
        title: str,
    ) -> AdminSkillDictionaryRow | None:
        skill = await self.session.get(Skill, skill_id)

        if not skill:
            return None

        if language == "en":
            skill.name_en = title
        elif language == "pt":
            skill.name_pt = title
        else:
            skill.name_ru = title
            skill.name = title

        await self.session.flush()

        return await self.get_skill_for_admin(skill_id)
    
    async def set_skill_visibility_for_admin(
        self,
        *,
        skill_id: UUID,
        is_active: bool,
    ) -> AdminSkillDictionaryRow | None:
        skill = await self.session.get(Skill, skill_id)

        if not skill:
            return None

        skill.is_active = is_active
        await self.session.flush()

        return await self.get_skill_for_admin(skill_id)
    
    async def merge_skill_links_for_admin(
        self,
        *,
        source_skill_id: UUID,
        target_skill_id: UUID,
    ) -> AdminSkillMergeResult:
        target_profession_ids_result = await self.session.execute(
            select(ProfessionSkill.profession_id).where(
                ProfessionSkill.skill_id == target_skill_id
            )
        )
        target_profession_ids = set(target_profession_ids_result.scalars().all())

        source_profession_links_result = await self.session.execute(
            select(ProfessionSkill).where(
                ProfessionSkill.skill_id == source_skill_id
            )
        )
        source_profession_links = list(source_profession_links_result.scalars().all())

        moved_profession_links = 0
        removed_duplicate_profession_links = 0

        for link in source_profession_links:
            if link.profession_id in target_profession_ids:
                await self.session.delete(link)
                removed_duplicate_profession_links += 1
            else:
                link.skill_id = target_skill_id
                target_profession_ids.add(link.profession_id)
                moved_profession_links += 1

        target_user_ids_result = await self.session.execute(
            select(UserSkill.user_id).where(
                UserSkill.skill_id == target_skill_id
            )
        )
        target_user_ids = set(target_user_ids_result.scalars().all())

        source_user_links_result = await self.session.execute(
            select(UserSkill).where(
                UserSkill.skill_id == source_skill_id
            )
        )
        source_user_links = list(source_user_links_result.scalars().all())

        moved_user_links = 0
        removed_duplicate_user_links = 0

        for link in source_user_links:
            if link.user_id in target_user_ids:
                await self.session.delete(link)
                removed_duplicate_user_links += 1
            else:
                link.skill_id = target_skill_id
                target_user_ids.add(link.user_id)
                moved_user_links += 1

        source_skill = await self.session.get(Skill, source_skill_id)
        if source_skill:
            source_skill.is_active = False

        await self.session.flush()

        return AdminSkillMergeResult(
            moved_profession_links=moved_profession_links,
            removed_duplicate_profession_links=removed_duplicate_profession_links,
            moved_user_links=moved_user_links,
            removed_duplicate_user_links=removed_duplicate_user_links,
        )
    
    async def list_languages_for_admin(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminLanguageDictionaryRow]:
        specialist_counts = (
            select(
                SpecialistLanguage.language_code.label("language_code"),
                func.count(SpecialistLanguage.id).label("specialist_links_count"),
            )
            .group_by(SpecialistLanguage.language_code)
            .subquery()
        )

        result = await self.session.execute(
            select(
                Language.code,
                Language.name,
                Language.native_name,
                Language.is_active,
                func.coalesce(
                    specialist_counts.c.specialist_links_count,
                    0,
                ).label("specialist_links_count"),
            )
            .outerjoin(
                specialist_counts,
                specialist_counts.c.language_code == Language.code,
            )
            .order_by(Language.is_active.desc(), Language.name)
            .offset(offset)
            .limit(limit)
        )

        return [
            AdminLanguageDictionaryRow(
                code=row.code,
                name=row.name,
                native_name=row.native_name,
                is_active=row.is_active,
                specialist_links_count=row.specialist_links_count,
            )
            for row in result
        ]

    async def get_language_for_admin(
        self,
        code: str,
    ) -> AdminLanguageDictionaryRow | None:
        clean_code = code.strip().lower()

        specialist_counts = (
            select(
                SpecialistLanguage.language_code.label("language_code"),
                func.count(SpecialistLanguage.id).label("specialist_links_count"),
            )
            .where(SpecialistLanguage.language_code == clean_code)
            .group_by(SpecialistLanguage.language_code)
            .subquery()
        )

        result = await self.session.execute(
            select(
                Language.code,
                Language.name,
                Language.native_name,
                Language.is_active,
                func.coalesce(
                    specialist_counts.c.specialist_links_count,
                    0,
                ).label("specialist_links_count"),
            )
            .outerjoin(
                specialist_counts,
                specialist_counts.c.language_code == Language.code,
            )
            .where(Language.code == clean_code)
        )

        row = result.first()

        if not row:
            return None

        return AdminLanguageDictionaryRow(
            code=row.code,
            name=row.name,
            native_name=row.native_name,
            is_active=row.is_active,
            specialist_links_count=row.specialist_links_count,
        )

    async def language_code_exists(
        self,
        *,
        code: str,
    ) -> bool:
        result = await self.session.execute(
            select(Language.code).where(Language.code == code.strip().lower())
        )
        return result.scalar_one_or_none() is not None

    async def language_name_exists(
        self,
        *,
        name: str,
        exclude_code: str | None = None,
    ) -> bool:
        normalized_name = name.strip().lower()

        conditions = [
            func.lower(func.trim(Language.name)) == normalized_name,
            func.lower(func.trim(Language.native_name)) == normalized_name,
        ]

        query = select(Language.code).where(or_(*conditions))

        if exclude_code:
            query = query.where(Language.code != exclude_code.strip().lower())

        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def create_language_for_admin(
        self,
        *,
        code: str,
        name: str,
        native_name: str | None,
    ) -> AdminLanguageDictionaryRow:
        language = Language(
            code=code.strip().lower(),
            name=name.strip(),
            native_name=(native_name or "").strip() or None,
            is_active=True,
        )

        self.session.add(language)
        await self.session.flush()

        return await self.get_language_for_admin(language.code)

    async def rename_language_for_admin(
        self,
        *,
        code: str,
        name: str,
        native_name: str | None,
    ) -> AdminLanguageDictionaryRow | None:
        clean_code = code.strip().lower()
        language = await self.session.get(Language, clean_code)

        if not language:
            return None

        language.name = name.strip()
        language.native_name = (native_name or "").strip() or None

        await self.session.flush()

        return await self.get_language_for_admin(clean_code)

    async def set_language_visibility_for_admin(
        self,
        *,
        code: str,
        is_active: bool,
    ) -> AdminLanguageDictionaryRow | None:
        clean_code = code.strip().lower()
        language = await self.session.get(Language, clean_code)

        if not language:
            return None

        language.is_active = is_active
        await self.session.flush()

        return await self.get_language_for_admin(clean_code)

    async def country_code_exists(
        self,
        *,
        code: str,
    ) -> bool:
        result = await self.session.execute(
            select(Country.id).where(
                func.lower(Country.code) == code.strip().lower()
            )
        )
        return result.scalar_one_or_none() is not None

    async def country_name_exists(
        self,
        *,
        name: str,
        exclude_country_id: UUID | None = None,
    ) -> bool:
        normalized_name = name.strip().lower()

        query = select(Country.id).where(
            or_(
                func.lower(func.trim(Country.name)) == normalized_name,
                func.lower(func.trim(Country.name_ru)) == normalized_name,
                func.lower(func.trim(Country.name_en)) == normalized_name,
                func.lower(func.trim(Country.name_pt)) == normalized_name,
            )
        )

        if exclude_country_id:
            query = query.where(Country.id != exclude_country_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def list_countries_for_admin(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminCountryDictionaryRow]:
        city_counts = (
            select(
                City.country_id.label("country_id"),
                func.count(City.id).label("cities_count"),
            )
            .group_by(City.country_id)
            .subquery()
        )

        specialist_counts = (
            select(
                Specialist.country_id.label("country_id"),
                func.count(Specialist.id).label("specialists_count"),
            )
            .where(Specialist.country_id.is_not(None))
            .group_by(Specialist.country_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                Country.id,
                Country.code,
                Country.name,
                Country.name_ru,
                Country.name_en,
                Country.name_pt,
                Country.default_language,
                Country.default_currency,
                Country.phone_code,
                Country.is_active,
                Country.extra_metadata,
                func.coalesce(city_counts.c.cities_count, 0).label(
                    "cities_count"
                ),
                func.coalesce(
                    specialist_counts.c.specialists_count,
                    0,
                ).label("specialists_count"),
            )
            .outerjoin(city_counts, city_counts.c.country_id == Country.id)
            .outerjoin(
                specialist_counts,
                specialist_counts.c.country_id == Country.id,
            )
            .order_by(Country.is_active.desc(), Country.name)
            .offset(offset)
            .limit(limit)
        )

        return [
            AdminCountryDictionaryRow(
                country_id=row.id,
                code=row.code,
                name=row.name,
                name_ru=row.name_ru,
                name_en=row.name_en,
                name_pt=row.name_pt,
                default_language=row.default_language,
                default_currency=row.default_currency,
                phone_code=row.phone_code,
                is_active=row.is_active,
                metadata=row.extra_metadata or {},
                cities_count=row.cities_count,
                specialists_count=row.specialists_count,
            )
            for row in result
        ]

    async def get_country_for_admin(
        self,
        country_id: UUID,
    ) -> AdminCountryDictionaryRow | None:
        city_counts = (
            select(
                City.country_id.label("country_id"),
                func.count(City.id).label("cities_count"),
            )
            .where(City.country_id == country_id)
            .group_by(City.country_id)
            .subquery()
        )

        specialist_counts = (
            select(
                Specialist.country_id.label("country_id"),
                func.count(Specialist.id).label("specialists_count"),
            )
            .where(Specialist.country_id == country_id)
            .group_by(Specialist.country_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                Country.id,
                Country.code,
                Country.name,
                Country.name_ru,
                Country.name_en,
                Country.name_pt,
                Country.default_language,
                Country.default_currency,
                Country.phone_code,
                Country.is_active,
                Country.extra_metadata,
                func.coalesce(city_counts.c.cities_count, 0).label(
                    "cities_count"
                ),
                func.coalesce(
                    specialist_counts.c.specialists_count,
                    0,
                ).label("specialists_count"),
            )
            .outerjoin(city_counts, city_counts.c.country_id == Country.id)
            .outerjoin(
                specialist_counts,
                specialist_counts.c.country_id == Country.id,
            )
            .where(Country.id == country_id)
        )

        row = result.first()

        if not row:
            return None

        return AdminCountryDictionaryRow(
            country_id=row.id,
            code=row.code,
            name=row.name,
            name_ru=row.name_ru,
            name_en=row.name_en,
            name_pt=row.name_pt,
            default_language=row.default_language,
            default_currency=row.default_currency,
            phone_code=row.phone_code,
            is_active=row.is_active,
            metadata=row.extra_metadata or {},
            cities_count=row.cities_count,
            specialists_count=row.specialists_count,
        )

    async def set_country_visibility_for_admin(
        self,
        *,
        country_id: UUID,
        is_active: bool,
    ) -> AdminCountryDictionaryRow | None:
        country = await self.session.get(Country, country_id)

        if not country:
            return None

        country.is_active = is_active
        await self.session.flush()

        return await self.get_country_for_admin(country_id)

    async def upsert_country_for_admin(
        self,
        *,
        code: str,
        name: str,
        name_ru: str | None,
        name_en: str | None,
        name_pt: str | None,
        default_language: str | None,
        default_currency: str | None,
        phone_code: str | None,
    ) -> tuple[AdminCountryDictionaryRow, bool]:
        clean_code = code.strip().upper()

        result = await self.session.execute(
            select(Country).where(
                func.lower(Country.code) == clean_code.lower()
            )
        )
        country = result.scalar_one_or_none()
        created = False

        if not country:
            country = Country(
                code=clean_code,
                name=name.strip(),
                is_active=True,
                extra_metadata={
                    "source": "super_admin_import",
                },
            )
            self.session.add(country)
            created = True

        country.name = name.strip()
        country.name_ru = (name_ru or "").strip() or None
        country.name_en = (name_en or "").strip() or None
        country.name_pt = (name_pt or "").strip() or None
        country.default_language = (default_language or "").strip() or None
        country.default_currency = (default_currency or "").strip() or None
        country.phone_code = (phone_code or "").strip() or None

        await self.session.flush()

        return await self.get_country_for_admin(country.id), created

    async def create_country_for_admin(
        self,
        *,
        code: str,
        name: str,
        name_ru: str | None,
        name_en: str | None,
        name_pt: str | None,
        default_language: str | None,
        default_currency: str | None,
        phone_code: str | None,
    ) -> AdminCountryDictionaryRow:
        country = Country(
            code=code.strip().upper(),
            name=name.strip(),
            name_ru=(name_ru or "").strip() or None,
            name_en=(name_en or "").strip() or None,
            name_pt=(name_pt or "").strip() or None,
            default_language=(default_language or "").strip() or None,
            default_currency=(default_currency or "").strip() or None,
            phone_code=(phone_code or "").strip() or None,
            is_active=True,
            extra_metadata={
                "source": "super_admin",
            },
        )

        self.session.add(country)
        await self.session.flush()

        return await self.get_country_for_admin(country.id)

    async def update_country_for_admin(
        self,
        *,
        country_id: UUID,
        name: str,
        name_ru: str | None,
        name_en: str | None,
        name_pt: str | None,
        default_language: str | None,
        default_currency: str | None,
        phone_code: str | None,
    ) -> AdminCountryDictionaryRow | None:
        country = await self.session.get(Country, country_id)

        if not country:
            return None

        country.name = name
        country.name_ru = name_ru
        country.name_en = name_en
        country.name_pt = name_pt
        country.default_language = default_language
        country.default_currency = default_currency
        country.phone_code = phone_code

        await self.session.flush()

        return await self.get_country_for_admin(country_id)

    async def city_name_exists(
        self,
        *,
        country_id: UUID,
        name: str,
        exclude_city_id: UUID | None = None,
    ) -> bool:
        normalized_name = name.strip().lower()

        query = select(City.id).where(
            City.country_id == country_id,
            or_(
                func.lower(func.trim(City.name)) == normalized_name,
                func.lower(func.trim(City.name_ru)) == normalized_name,
                func.lower(func.trim(City.name_en)) == normalized_name,
                func.lower(func.trim(City.name_pt)) == normalized_name,
            ),
        )

        if exclude_city_id:
            query = query.where(City.id != exclude_city_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def list_cities_for_admin(
        self,
        *,
        country_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminCityDictionaryRow]:
        specialist_counts = (
            select(
                Specialist.city_id.label("city_id"),
                func.count(Specialist.id).label("specialists_count"),
            )
            .where(Specialist.city_id.is_not(None))
            .group_by(Specialist.city_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                City.id,
                City.country_id,
                Country.name.label("country_name"),
                City.name,
                City.name_ru,
                City.name_en,
                City.name_pt,
                City.latitude,
                City.longitude,
                City.timezone,
                City.is_active,
                City.extra_metadata,
                func.coalesce(
                    specialist_counts.c.specialists_count,
                    0,
                ).label("specialists_count"),
            )
            .join(Country, Country.id == City.country_id)
            .outerjoin(specialist_counts, specialist_counts.c.city_id == City.id)
            .where(City.country_id == country_id)
            .order_by(City.is_active.desc(), City.name)
            .offset(offset)
            .limit(limit)
        )

        return [
            AdminCityDictionaryRow(
                city_id=row.id,
                country_id=row.country_id,
                country_name=row.country_name,
                name=row.name,
                name_ru=row.name_ru,
                name_en=row.name_en,
                name_pt=row.name_pt,
                latitude=float(row.latitude) if row.latitude is not None else None,
                longitude=(
                    float(row.longitude) if row.longitude is not None else None
                ),
                timezone=row.timezone,
                is_active=row.is_active,
                metadata=row.extra_metadata or {},
                specialists_count=row.specialists_count,
            )
            for row in result
        ]

    async def get_city_for_admin(
        self,
        city_id: UUID,
    ) -> AdminCityDictionaryRow | None:
        specialist_counts = (
            select(
                Specialist.city_id.label("city_id"),
                func.count(Specialist.id).label("specialists_count"),
            )
            .where(Specialist.city_id == city_id)
            .group_by(Specialist.city_id)
            .subquery()
        )

        result = await self.session.execute(
            select(
                City.id,
                City.country_id,
                Country.name.label("country_name"),
                City.name,
                City.name_ru,
                City.name_en,
                City.name_pt,
                City.latitude,
                City.longitude,
                City.timezone,
                City.is_active,
                City.extra_metadata,
                func.coalesce(
                    specialist_counts.c.specialists_count,
                    0,
                ).label("specialists_count"),
            )
            .join(Country, Country.id == City.country_id)
            .outerjoin(specialist_counts, specialist_counts.c.city_id == City.id)
            .where(City.id == city_id)
        )

        row = result.first()

        if not row:
            return None

        return AdminCityDictionaryRow(
            city_id=row.id,
            country_id=row.country_id,
            country_name=row.country_name,
            name=row.name,
            name_ru=row.name_ru,
            name_en=row.name_en,
            name_pt=row.name_pt,
            latitude=float(row.latitude) if row.latitude is not None else None,
            longitude=float(row.longitude) if row.longitude is not None else None,
            timezone=row.timezone,
            is_active=row.is_active,
            metadata=row.extra_metadata or {},
            specialists_count=row.specialists_count,
        )
    
    async def set_city_visibility_for_admin(
        self,
        *,
        city_id: UUID,
        is_active: bool,
    ) -> AdminCityDictionaryRow | None:
        city = await self.session.get(City, city_id)

        if not city:
            return None

        city.is_active = is_active
        await self.session.flush()

        return await self.get_city_for_admin(city_id)

    async def upsert_city_for_admin(
        self,
        *,
        country_id: UUID,
        name: str,
        name_ru: str | None,
        name_en: str | None,
        name_pt: str | None,
        timezone: str | None,
        latitude: float | None,
        longitude: float | None,
    ) -> tuple[AdminCityDictionaryRow, bool]:
        normalized_names = [
            value.strip().lower()
            for value in (name, name_ru, name_en, name_pt)
            if value and value.strip()
        ]

        result = await self.session.execute(
            select(City).where(
                City.country_id == country_id,
                or_(
                    func.lower(func.trim(City.name)).in_(normalized_names),
                    func.lower(func.trim(City.name_ru)).in_(normalized_names),
                    func.lower(func.trim(City.name_en)).in_(normalized_names),
                    func.lower(func.trim(City.name_pt)).in_(normalized_names),
                ),
            )
        )
        city = result.scalar_one_or_none()
        created = False

        if not city:
            city = City(
                country_id=country_id,
                name=name.strip(),
                is_active=True,
                extra_metadata={
                    "source": "super_admin_import",
                },
            )
            self.session.add(city)
            created = True

        city.name = name.strip()
        city.name_ru = (name_ru or "").strip() or None
        city.name_en = (name_en or "").strip() or None
        city.name_pt = (name_pt or "").strip() or None
        city.timezone = (timezone or "").strip() or None
        city.latitude = latitude
        city.longitude = longitude

        await self.session.flush()

        return await self.get_city_for_admin(city.id), created

    async def create_city_for_admin(
        self,
        *,
        country_id: UUID,
        name: str,
        name_ru: str | None,
        name_en: str | None,
        name_pt: str | None,
        timezone: str | None,
    ) -> AdminCityDictionaryRow:
        city = City(
            country_id=country_id,
            name=name.strip(),
            name_ru=(name_ru or "").strip() or None,
            name_en=(name_en or "").strip() or None,
            name_pt=(name_pt or "").strip() or None,
            timezone=(timezone or "").strip() or None,
            is_active=True,
            extra_metadata={
                "source": "super_admin",
            },
        )

        self.session.add(city)
        await self.session.flush()

        return await self.get_city_for_admin(city.id)

    async def update_city_for_admin(
        self,
        *,
        city_id: UUID,
        name: str,
        name_ru: str | None,
        name_en: str | None,
        name_pt: str | None,
        latitude: float | None,
        longitude: float | None,
        timezone: str | None,
    ) -> AdminCityDictionaryRow | None:
        city = await self.session.get(City, city_id)

        if not city:
            return None

        city.name = name
        city.name_ru = name_ru
        city.name_en = name_en
        city.name_pt = name_pt
        city.latitude = latitude
        city.longitude = longitude
        city.timezone = timezone

        await self.session.flush()

        return await self.get_city_for_admin(city_id)