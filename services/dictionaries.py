import csv
import re
from dataclasses import dataclass
from uuid import UUID
from services.specialist import MAX_PROFESSIONS_PER_CATEGORY
from database.repositories.dictionaries import (
    AdminCategoryDictionaryRow,
    AdminCategorySpecialistRow,
    AdminCategorySpecialistMoveResult,
    AdminCityDictionaryRow,
    AdminCountryDictionaryRow,
    AdminLanguageDictionaryRow,
    AdminMultiProfessionMoveResult,
    AdminProfessionDictionaryRow,
    AdminSkillDictionaryRow,
    AdminSkillMergeResult,
    AdminSpecialistMoveResult,
    DictionaryRepository,
)


@dataclass(frozen=True)
class AdminCategoryDictionaryCard:
    category_id: UUID
    code: str
    title: str
    sort_order: int
    status: str
    status_code: str
    release: str | None
    professions_count: int
    specialists_count: int

@dataclass(frozen=True)
class AdminCategorySpecialistCard:
    specialist_id: UUID
    display_name: str
    status: str
    profession_names: str
    is_verified: bool
    is_available: bool

@dataclass(frozen=True)
class AdminProfessionDictionaryCard:
    profession_id: UUID
    category_id: UUID
    code: str
    title: str
    category_name: str
    sort_order: int
    status: str
    status_code: str
    release: str | None
    specialists_count: int

@dataclass(frozen=True)
class AdminMultiProfessionMovePreviewCard:
    source_type: str
    source_title: str
    target_category: AdminCategoryDictionaryCard
    target_professions: tuple[
        AdminProfessionDictionaryCard,
        ...,
    ]
    selected_specialists: tuple[
        AdminCategorySpecialistCard,
        ...,
    ]
    mode: str


@dataclass(frozen=True)
class AdminMultiProfessionMoveCard:
    source_type: str
    source_title: str
    target_category: AdminCategoryDictionaryCard
    target_professions: tuple[
        AdminProfessionDictionaryCard,
        ...,
    ]
    mode: str
    requested_specialists_count: int
    selected_professions_count: int
    created_links_count: int
    reactivated_links_count: int
    existing_links_count: int
    deleted_old_links_count: int
    synchronized_primary_count: int
    missing_specialists_count: int

@dataclass(frozen=True)
class AdminCategorySpecialistMovePreviewCard:
    source_category: AdminCategoryDictionaryCard
    target_profession: AdminProfessionDictionaryCard
    selected_specialists: tuple[
        AdminCategorySpecialistCard,
        ...,
    ]


@dataclass(frozen=True)
class AdminCategorySpecialistMoveCard:
    source_category: AdminCategoryDictionaryCard
    target_profession: AdminProfessionDictionaryCard
    requested_count: int
    moved_count: int
    archived_duplicate_count: int
    archived_extra_links_count: int
    synchronized_primary_count: int
    missing_count: int

@dataclass(frozen=True)
class AdminSpecialistMovePreviewCard:
    source_profession: AdminProfessionDictionaryCard
    target_profession: AdminProfessionDictionaryCard
    selected_specialists: tuple[AdminCategorySpecialistCard, ...]


@dataclass(frozen=True)
class AdminSpecialistMoveCard:
    source_profession: AdminProfessionDictionaryCard
    target_profession: AdminProfessionDictionaryCard
    requested_count: int
    moved_count: int
    archived_duplicate_count: int
    synchronized_primary_count: int
    missing_count: int


@dataclass(frozen=True)
class AdminSkillDictionaryCard:
    skill_id: UUID
    code: str
    title: str
    status: str
    status_code: str
    profession_links_count: int
    user_links_count: int
    vacancy_links_count: int

@dataclass(frozen=True)
class AdminSkillMergeCard:
    target_skill: AdminSkillDictionaryCard
    moved_profession_links: int
    removed_duplicate_profession_links: int
    moved_user_links: int
    removed_duplicate_user_links: int

@dataclass(frozen=True)
class AdminSkillMergePreviewCard:
    source_skill: AdminSkillDictionaryCard
    target_skill: AdminSkillDictionaryCard

@dataclass(frozen=True)
class AdminLanguageDictionaryCard:
    code: str
    title: str
    native_name: str | None
    status: str
    status_code: str
    specialist_links_count: int

@dataclass(frozen=True)
class AdminCountryDictionaryCard:
    country_id: UUID
    code: str
    title: str
    status: str
    status_code: str
    default_language: str | None
    default_currency: str | None
    phone_code: str | None
    cities_count: int
    specialists_count: int

@dataclass(frozen=True)
class AdminDictionaryImportCard:
    created_count: int
    updated_count: int
    skipped_count: int
    errors: tuple[str, ...]

@dataclass(frozen=True)
class AdminCityDictionaryCard:
    city_id: UUID
    country_id: UUID
    title: str
    country_name: str
    status: str
    status_code: str
    latitude: float | None
    longitude: float | None
    timezone: str | None
    specialists_count: int

class DictionaryServiceError(Exception):
    def __init__(self, text_key: str):
        self.text_key = text_key

class DictionaryService:
    def __init__(self, repository: DictionaryRepository):
        self.repository = repository

    async def list_category_cards(
        self,
        *,
        language: str = "ru",
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminCategoryDictionaryCard]:
        rows = await self.repository.list_categories_for_admin(
            limit=limit,
            offset=offset,
        )

        return [
            self._category_card(row, language)
            for row in rows
        ]

    def _category_card(
        self,
        row: AdminCategoryDictionaryRow,
        language: str,
    ) -> AdminCategoryDictionaryCard:
        title = {
            "ru": row.name_ru,
            "en": row.name_en,
            "pt": row.name_pt,
        }.get(language) or row.name_ru or row.name_en or row.name_pt or row.name

        release = None
        if row.metadata:
            release = row.metadata.get("release")

        status_labels = {
            "ru": {
                "active": "Активна",
                "hidden": "Скрыта",
                "archived": "Архив",
            },
            "en": {
                "active": "Active",
                "hidden": "Hidden",
                "archived": "Archived",
            },
            "pt": {
                "active": "Ativa",
                "hidden": "Oculta",
                "archived": "Arquivada",
            },
        }

        status_code = "active" if row.is_active else "hidden"

        if row.metadata and row.metadata.get("archived"):
            status_code = "archived"

        status = status_labels.get(language, status_labels["ru"]).get(
            status_code,
            status_code,
        )

        return AdminCategoryDictionaryCard(
            category_id=row.category_id,
            code=row.code,
            title=title,
            sort_order=row.sort_order,
            status=status,
            status_code=status_code,
            release=release,
            professions_count=row.professions_count,
            specialists_count=row.specialists_count,
        )
    

    async def get_category_card(
        self,
        *,
        category_id: str,
        language: str = "ru",
    ) -> AdminCategoryDictionaryCard | None:
        try:
            parsed_category_id = UUID(str(category_id))
        except (TypeError, ValueError):
            return None

        row = await self.repository.get_category_for_admin(parsed_category_id)

        if not row:
            return None

        return self._category_card(row, language)
    
    async def rename_category(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        category_id: str,
        title: str,
        language: str = "ru",
    ) -> AdminCategoryDictionaryCard:
        cleaned_title = " ".join((title or "").split())

        if len(cleaned_title) < 2:
            raise DictionaryServiceError("admin_dict_category_rename_empty")

        try:
            parsed_category_id = UUID(str(category_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_category_for_admin(parsed_category_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        if await self.repository.category_name_exists(
            category_id=parsed_category_id,
            title=cleaned_title,
        ):
            raise DictionaryServiceError("admin_dict_category_rename_duplicate")

        after = await self.repository.rename_category_for_admin(
            category_id=parsed_category_id,
            language=language,
            title=cleaned_title,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_category_renamed",
            target_type="specialist_category",
            target_id=parsed_category_id,
            before_state={
                "name": before.name,
                "name_ru": before.name_ru,
                "name_en": before.name_en,
                "name_pt": before.name_pt,
            },
            after_state={
                "name": after.name,
                "name_ru": after.name_ru,
                "name_en": after.name_en,
                "name_pt": after.name_pt,
            },
            reason="Category renamed from Super Admin dictionaries",
        )

        return self._category_card(after, language)
    
    async def toggle_category_visibility(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        category_id: str,
        language: str = "ru",
    ) -> AdminCategoryDictionaryCard:
        try:
            parsed_category_id = UUID(str(category_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_category_for_admin(parsed_category_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        new_is_active = not before.is_active

        after = await self.repository.set_category_visibility_for_admin(
            category_id=parsed_category_id,
            is_active=new_is_active,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_category_visibility_changed",
            target_type="specialist_category",
            target_id=parsed_category_id,
            before_state={
                "is_active": before.is_active,
            },
            after_state={
                "is_active": after.is_active,
            },
            reason="Category visibility changed from Super Admin dictionaries",
        )

        return self._category_card(after, language)
    
    async def archive_category(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        category_id: str,
        language: str = "ru",
    ) -> AdminCategoryDictionaryCard:
        try:
            parsed_category_id = UUID(str(category_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_category_for_admin(parsed_category_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        if before.metadata and before.metadata.get("archived"):
            raise DictionaryServiceError("admin_dict_category_already_archived")

        after = await self.repository.archive_category_for_admin(
            category_id=parsed_category_id,
            admin_user_id=admin_user_id,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_category_archived",
            target_type="specialist_category",
            target_id=parsed_category_id,
            before_state={
                "is_active": before.is_active,
                "metadata": before.metadata,
            },
            after_state={
                "is_active": after.is_active,
                "metadata": after.metadata,
            },
            reason="Category archived from Super Admin dictionaries",
        )

        return self._category_card(after, language)
    
    async def unarchive_category(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        category_id: str,
        language: str = "ru",
    ) -> AdminCategoryDictionaryCard:
        try:
            parsed_category_id = UUID(str(category_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_category_for_admin(parsed_category_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        if not before.metadata or not before.metadata.get("archived"):
            raise DictionaryServiceError("admin_dict_category_not_archived")

        after = await self.repository.unarchive_category_for_admin(
            category_id=parsed_category_id,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_category_unarchived",
            target_type="specialist_category",
            target_id=parsed_category_id,
            before_state={
                "is_active": before.is_active,
                "metadata": before.metadata,
            },
            after_state={
                "is_active": after.is_active,
                "metadata": after.metadata,
            },
            reason="Category returned from archive in Super Admin dictionaries",
        )

        return self._category_card(after, language)
    
    async def update_category_sort_order(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        category_id: str,
        sort_order_text: str,
        language: str = "ru",
    ) -> AdminCategoryDictionaryCard:
        try:
            parsed_category_id = UUID(str(category_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        try:
            sort_order = int((sort_order_text or "").strip())
        except ValueError:
            raise DictionaryServiceError("admin_dict_category_sort_order_invalid")

        if sort_order < 0 or sort_order > 10000:
            raise DictionaryServiceError("admin_dict_category_sort_order_invalid")

        before = await self.repository.get_category_for_admin(parsed_category_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        after = await self.repository.update_category_sort_order_for_admin(
            category_id=parsed_category_id,
            sort_order=sort_order,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_category_sort_order_changed",
            target_type="specialist_category",
            target_id=parsed_category_id,
            before_state={
                "sort_order": before.sort_order,
            },
            after_state={
                "sort_order": after.sort_order,
            },
            reason="Category sort order changed from Super Admin dictionaries",
        )

        return self._category_card(after, language)

    def _clean_optional_text(
        self,
        value: str | None,
    ) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None

    def _parse_csv_rows(
        self,
        payload: str,
    ) -> list[dict[str, str]]:
        cleaned_payload = (payload or "").strip()

        if not cleaned_payload:
            raise DictionaryServiceError("admin_dict_import_empty")

        reader = csv.DictReader(cleaned_payload.splitlines())
        return [
            {
                key.strip(): (value or "").strip()
                for key, value in row.items()
                if key
            }
            for row in reader
        ]

    def _parse_country_create_payload(
        self,
        payload: str,
    ) -> tuple[
        str,
        str,
        str,
        str,
    ]:
        parts = [part.strip() for part in (payload or "").split("|")]

        if len(parts) != 4:
            raise DictionaryServiceError("admin_dict_country_create_bad_format")

        code = parts[0].upper()
        name_ru = parts[1]
        name_en = parts[2]
        name_pt = parts[3]

        if not re.fullmatch(r"[A-Z]{2}", code):
            raise DictionaryServiceError("admin_dict_country_code_invalid")

        if len(name_ru) < 2 or len(name_en) < 2 or len(name_pt) < 2:
            raise DictionaryServiceError("admin_dict_country_create_name_empty")

        return code, name_ru, name_en, name_pt

    def _parse_country_update_payload(
        self,
        payload: str,
    ) -> tuple[
        str,
        str | None,
        str | None,
        str | None,
        str | None,
        str | None,
        str | None,
    ]:
        parts = [part.strip() for part in (payload or "").split("|")]

        if len(parts) != 7:
            raise DictionaryServiceError("admin_dict_country_update_bad_format")

        name = parts[0]

        if len(name) < 2:
            raise DictionaryServiceError("admin_dict_country_update_name_empty")

        return (
            name,
            self._clean_optional_text(parts[1]),
            self._clean_optional_text(parts[2]),
            self._clean_optional_text(parts[3]),
            self._clean_optional_text(parts[4]),
            self._clean_optional_text(parts[5]),
            self._clean_optional_text(parts[6]),
        )

    def _parse_optional_float(
        self,
        value: str,
        *,
        text_key: str,
    ) -> float | None:
        cleaned = (value or "").strip()

        if not cleaned:
            return None

        try:
            return float(cleaned.replace(",", "."))
        except ValueError:
            raise DictionaryServiceError(text_key)

    def _parse_city_create_payload(
        self,
        payload: str,
    ) -> tuple[
        str,
        str,
        str,
        str | None,
    ]:
        parts = [part.strip() for part in (payload or "").split("|")]

        if len(parts) != 4:
            raise DictionaryServiceError("admin_dict_city_create_bad_format")

        name_ru = parts[0]
        name_en = parts[1]
        name_pt = parts[2]
        timezone = self._clean_optional_text(parts[3])

        if len(name_ru) < 2 or len(name_en) < 2 or len(name_pt) < 2:
            raise DictionaryServiceError("admin_dict_city_create_name_empty")

        return name_ru, name_en, name_pt, timezone

    def _parse_city_update_payload(
        self,
        payload: str,
    ) -> tuple[
        str,
        str | None,
        str | None,
        str | None,
        str | None,
    ]:
        parts = [part.strip() for part in (payload or "").split("|")]

        if len(parts) != 5:
            raise DictionaryServiceError("admin_dict_city_update_bad_format")

        name = parts[0]

        if len(name) < 2:
            raise DictionaryServiceError("admin_dict_city_update_name_empty")

        return (
            name,
            self._clean_optional_text(parts[1]),
            self._clean_optional_text(parts[2]),
            self._clean_optional_text(parts[3]),
            self._clean_optional_text(parts[4]),
        )

    def _parse_city_geo_payload(
        self,
        payload: str,
    ) -> tuple[float, float]:
        parts = [part.strip() for part in (payload or "").split(",")]

        if len(parts) != 2:
            raise DictionaryServiceError("admin_dict_city_geo_bad_format")

        latitude = self._parse_optional_float(
            parts[0],
            text_key="admin_dict_city_geo_latitude_invalid",
        )
        longitude = self._parse_optional_float(
            parts[1],
            text_key="admin_dict_city_geo_longitude_invalid",
        )

        if latitude is None:
            raise DictionaryServiceError("admin_dict_city_geo_latitude_invalid")

        if longitude is None:
            raise DictionaryServiceError("admin_dict_city_geo_longitude_invalid")

        if latitude < -90 or latitude > 90:
            raise DictionaryServiceError("admin_dict_city_geo_latitude_invalid")

        if longitude < -180 or longitude > 180:
            raise DictionaryServiceError("admin_dict_city_geo_longitude_invalid")

        return latitude, longitude

    def _parse_language_payload(
        self,
        payload: str,
    ) -> tuple[str, str, str | None]:
        parts = [part.strip() for part in (payload or "").split("|")]

        if len(parts) < 2:
            raise DictionaryServiceError("admin_dict_language_bad_format")

        code = parts[0].lower()
        name = parts[1]
        native_name = parts[2] if len(parts) > 2 else None

        if not re.fullmatch(r"[a-z]{2,10}", code):
            raise DictionaryServiceError("admin_dict_language_code_invalid")

        if len(name) < 2:
            raise DictionaryServiceError("admin_dict_language_name_empty")

        if native_name is not None and len(native_name) == 0:
            native_name = None

        return code, name, native_name

    async def list_language_cards(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminLanguageDictionaryCard]:
        rows = await self.repository.list_languages_for_admin(
            limit=limit,
            offset=offset,
        )
        return [self._language_card(row) for row in rows]

    def _language_card(
        self,
        row: AdminLanguageDictionaryRow,
    ) -> AdminLanguageDictionaryCard:
        status_code = "active" if row.is_active else "hidden"
        status = "Активна" if row.is_active else "Скрыта"

        return AdminLanguageDictionaryCard(
            code=row.code,
            title=row.name,
            native_name=row.native_name,
            status=status,
            status_code=status_code,
            specialist_links_count=row.specialist_links_count,
        )

    async def get_language_card(
        self,
        code: str,
    ) -> AdminLanguageDictionaryCard | None:
        row = await self.repository.get_language_for_admin(code)

        if not row:
            return None

        return self._language_card(row)

    async def create_language(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        payload: str,
    ) -> AdminLanguageDictionaryCard:
        code, name, native_name = self._parse_language_payload(payload)

        if await self.repository.language_code_exists(code=code):
            raise DictionaryServiceError("admin_dict_language_code_duplicate")

        if await self.repository.language_name_exists(name=name):
            raise DictionaryServiceError("admin_dict_language_name_duplicate")

        row = await self.repository.create_language_for_admin(
            code=code,
            name=name,
            native_name=native_name,
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_language_created",
            target_type="language",
            target_id=None,
            before_state={},
            after_state={
                "code": row.code,
                "name": row.name,
                "native_name": row.native_name,
                "is_active": row.is_active,
            },
            reason="Language created from Super Admin dictionaries",
        )

        return self._language_card(row)

    async def rename_language(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        code: str,
        payload: str,
    ) -> AdminLanguageDictionaryCard:
        clean_code = code.strip().lower()
        _, name, native_name = self._parse_language_payload(
            f"{clean_code} | {payload}"
        )

        before = await self.repository.get_language_for_admin(clean_code)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        if await self.repository.language_name_exists(
            name=name,
            exclude_code=clean_code,
        ):
            raise DictionaryServiceError("admin_dict_language_name_duplicate")

        after = await self.repository.rename_language_for_admin(
            code=clean_code,
            name=name,
            native_name=native_name,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_language_renamed",
            target_type="language",
            target_id=None,
            before_state={
                "code": before.code,
                "name": before.name,
                "native_name": before.native_name,
            },
            after_state={
                "code": after.code,
                "name": after.name,
                "native_name": after.native_name,
            },
            reason="Language renamed from Super Admin dictionaries",
        )

        return self._language_card(after)

    async def toggle_language_visibility(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        code: str,
    ) -> AdminLanguageDictionaryCard:
        clean_code = code.strip().lower()
        before = await self.repository.get_language_for_admin(clean_code)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        after = await self.repository.set_language_visibility_for_admin(
            code=clean_code,
            is_active=not before.is_active,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_language_visibility_changed",
            target_type="language",
            target_id=None,
            before_state={
                "code": before.code,
                "is_active": before.is_active,
            },
            after_state={
                "code": after.code,
                "is_active": after.is_active,
            },
            reason="Language visibility changed from Super Admin dictionaries",
        )

        return self._language_card(after)

    async def import_countries(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        payload: str,
    ) -> AdminDictionaryImportCard:
        rows = self._parse_csv_rows(payload)
        required_columns = {
            "code",
            "name_ru",
            "name_en",
            "name_pt",
        }

        created_count = 0
        updated_count = 0
        skipped_count = 0
        errors: list[str] = []

        for row_number, row in enumerate(rows, start=2):
            missing_columns = required_columns - set(row)

            if missing_columns:
                skipped_count += 1
                errors.append(
                    f"row {row_number}: missing columns "
                    f"{', '.join(sorted(missing_columns))}"
                )
                continue

            code = row.get("code", "").upper()
            name_ru = row.get("name_ru", "")
            name_en = row.get("name_en", "")
            name_pt = row.get("name_pt", "")

            if not re.fullmatch(r"[A-Z]{2}", code):
                skipped_count += 1
                errors.append(f"row {row_number}: invalid country code")
                continue

            if len(name_ru) < 2 or len(name_en) < 2 or len(name_pt) < 2:
                skipped_count += 1
                errors.append(f"row {row_number}: empty country name")
                continue

            row_card, created = await self.repository.upsert_country_for_admin(
                code=code,
                name=name_en or name_ru,
                name_ru=name_ru,
                name_en=name_en,
                name_pt=name_pt,
                default_language=self._clean_optional_text(
                    row.get("default_language")
                ),
                default_currency=self._clean_optional_text(
                    row.get("default_currency")
                ),
                phone_code=self._clean_optional_text(row.get("phone_code")),
            )

            if created:
                created_count += 1
            else:
                updated_count += 1

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="import_countries",
            target_type="country",
            target_id=None,
            before_state={},
            after_state={
                "created": created_count,
                "updated": updated_count,
                "skipped": skipped_count,
                "errors": errors[:20],
            },
            reason="Countries imported from Super Admin dictionaries",
        )

        return AdminDictionaryImportCard(
            created_count=created_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
            errors=tuple(errors),
        )

    async def create_country(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        payload: str,
        language: str = "ru",
    ) -> AdminCountryDictionaryCard:
        (
            code,
            name_ru,
            name_en,
            name_pt,
        ) = self._parse_country_create_payload(payload)

        name = name_en or name_ru
        default_language = None
        default_currency = None
        phone_code = None

        if await self.repository.country_code_exists(code=code):
            raise DictionaryServiceError("admin_dict_country_code_duplicate")

        if await self.repository.country_name_exists(name=name):
            raise DictionaryServiceError("admin_dict_country_name_duplicate")

        row = await self.repository.create_country_for_admin(
            code=code,
            name=name,
            name_ru=name_ru,
            name_en=name_en,
            name_pt=name_pt,
            default_language=default_language,
            default_currency=default_currency,
            phone_code=phone_code,
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="create_country",
            target_type="country",
            target_id=row.country_id,
            before_state={},
            after_state={
                "code": row.code,
                "name": row.name,
                "name_ru": row.name_ru,
                "name_en": row.name_en,
                "name_pt": row.name_pt,
                "default_language": row.default_language,
                "default_currency": row.default_currency,
                "phone_code": row.phone_code,
                "is_active": row.is_active,
            },
            reason="Country created from Super Admin dictionaries",
        )

        return self._country_card(row, language)

    async def list_country_cards(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        language: str = "ru",
    ) -> list[AdminCountryDictionaryCard]:
        rows = await self.repository.list_countries_for_admin(
            limit=limit,
            offset=offset,
        )
        return [self._country_card(row, language) for row in rows]

    def _country_card(
        self,
        row: AdminCountryDictionaryRow,
        language: str,
    ) -> AdminCountryDictionaryCard:
        title = {
            "ru": row.name_ru,
            "en": row.name_en,
            "pt": row.name_pt,
        }.get(language) or row.name

        status_code = "active" if row.is_active else "hidden"
        status = "Активна" if row.is_active else "Скрыта"

        return AdminCountryDictionaryCard(
            country_id=row.country_id,
            code=row.code,
            title=title,
            status=status,
            status_code=status_code,
            default_language=row.default_language,
            default_currency=row.default_currency,
            phone_code=row.phone_code,
            cities_count=row.cities_count,
            specialists_count=row.specialists_count,
        )

    async def get_country_card(
        self,
        country_id: str,
        *,
        language: str = "ru",
    ) -> AdminCountryDictionaryCard | None:
        try:
            parsed_country_id = UUID(str(country_id))
        except (TypeError, ValueError):
            return None

        row = await self.repository.get_country_for_admin(parsed_country_id)

        if not row:
            return None

        return self._country_card(row, language)

    async def toggle_country_visibility(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        country_id: str,
        language: str = "ru",
    ) -> AdminCountryDictionaryCard:
        try:
            parsed_country_id = UUID(str(country_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_country_for_admin(parsed_country_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        after = await self.repository.set_country_visibility_for_admin(
            country_id=parsed_country_id,
            is_active=not before.is_active,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        action_type = (
            "activate_country"
            if after.is_active
            else "deactivate_country"
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type=action_type,
            target_type="country",
            target_id=parsed_country_id,
            before_state={
                "code": before.code,
                "is_active": before.is_active,
            },
            after_state={
                "code": after.code,
                "is_active": after.is_active,
            },
            reason="Country visibility changed from Super Admin dictionaries",
        )

        return self._country_card(after, language)

    async def update_country(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        country_id: str,
        payload: str,
        language: str = "ru",
    ) -> AdminCountryDictionaryCard:
        try:
            parsed_country_id = UUID(str(country_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        (
            name,
            name_ru,
            name_en,
            name_pt,
            default_language,
            default_currency,
            phone_code,
        ) = self._parse_country_update_payload(payload)

        before = await self.repository.get_country_for_admin(parsed_country_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        after = await self.repository.update_country_for_admin(
            country_id=parsed_country_id,
            name=name,
            name_ru=name_ru,
            name_en=name_en,
            name_pt=name_pt,
            default_language=default_language,
            default_currency=default_currency,
            phone_code=phone_code,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="update_country",
            target_type="country",
            target_id=parsed_country_id,
            before_state={
                "code": before.code,
                "name": before.name,
                "name_ru": before.name_ru,
                "name_en": before.name_en,
                "name_pt": before.name_pt,
                "default_language": before.default_language,
                "default_currency": before.default_currency,
                "phone_code": before.phone_code,
            },
            after_state={
                "code": after.code,
                "name": after.name,
                "name_ru": after.name_ru,
                "name_en": after.name_en,
                "name_pt": after.name_pt,
                "default_language": after.default_language,
                "default_currency": after.default_currency,
                "phone_code": after.phone_code,
            },
            reason="Country updated from Super Admin dictionaries",
        )

        return self._country_card(after, language)

    async def import_cities(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        country_id: str,
        payload: str,
    ) -> AdminDictionaryImportCard:
        try:
            parsed_country_id = UUID(str(country_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        country = await self.repository.get_country_for_admin(parsed_country_id)

        if not country:
            raise DictionaryServiceError("admin_item_not_found")

        rows = self._parse_csv_rows(payload)
        required_columns = {
            "name_ru",
            "name_en",
            "name_pt",
            "timezone",
            "latitude",
            "longitude",
        }

        created_count = 0
        updated_count = 0
        skipped_count = 0
        errors: list[str] = []

        for row_number, row in enumerate(rows, start=2):
            missing_columns = required_columns - set(row)

            if missing_columns:
                skipped_count += 1
                errors.append(
                    f"row {row_number}: missing columns "
                    f"{', '.join(sorted(missing_columns))}"
                )
                continue

            name_ru = row.get("name_ru", "")
            name_en = row.get("name_en", "")
            name_pt = row.get("name_pt", "")

            if len(name_ru) < 2 or len(name_en) < 2 or len(name_pt) < 2:
                skipped_count += 1
                errors.append(f"row {row_number}: empty city name")
                continue

            try:
                latitude = self._parse_optional_float(
                    row.get("latitude", ""),
                    text_key="admin_dict_city_geo_latitude_invalid",
                )
                longitude = self._parse_optional_float(
                    row.get("longitude", ""),
                    text_key="admin_dict_city_geo_longitude_invalid",
                )
            except DictionaryServiceError:
                skipped_count += 1
                errors.append(f"row {row_number}: invalid coordinates")
                continue

            if latitude is None or latitude < -90 or latitude > 90:
                skipped_count += 1
                errors.append(f"row {row_number}: invalid latitude")
                continue

            if longitude is None or longitude < -180 or longitude > 180:
                skipped_count += 1
                errors.append(f"row {row_number}: invalid longitude")
                continue

            row_card, created = await self.repository.upsert_city_for_admin(
                country_id=parsed_country_id,
                name=name_en or name_ru,
                name_ru=name_ru,
                name_en=name_en,
                name_pt=name_pt,
                timezone=self._clean_optional_text(row.get("timezone")),
                latitude=latitude,
                longitude=longitude,
            )

            if created:
                created_count += 1
            else:
                updated_count += 1

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="import_cities",
            target_type="country",
            target_id=parsed_country_id,
            before_state={},
            after_state={
                "country_code": country.code,
                "created": created_count,
                "updated": updated_count,
                "skipped": skipped_count,
                "errors": errors[:20],
            },
            reason="Cities imported from Super Admin dictionaries",
        )

        return AdminDictionaryImportCard(
            created_count=created_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
            errors=tuple(errors),
        )

    async def create_city(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        country_id: str,
        payload: str,
        language: str = "ru",
    ) -> AdminCityDictionaryCard:
        try:
            parsed_country_id = UUID(str(country_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        country = await self.repository.get_country_for_admin(parsed_country_id)

        if not country:
            raise DictionaryServiceError("admin_item_not_found")

        name_ru, name_en, name_pt, timezone = self._parse_city_create_payload(
            payload
        )

        if await self.repository.city_name_exists(
            country_id=parsed_country_id,
            name=name_ru,
        ):
            raise DictionaryServiceError("admin_dict_city_name_duplicate")

        row = await self.repository.create_city_for_admin(
            country_id=parsed_country_id,
            name=name_en or name_ru,
            name_ru=name_ru,
            name_en=name_en,
            name_pt=name_pt,
            timezone=timezone,
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="create_city",
            target_type="city",
            target_id=row.city_id,
            before_state={},
            after_state={
                "country_id": str(row.country_id),
                "country_name": row.country_name,
                "name": row.name,
                "name_ru": row.name_ru,
                "name_en": row.name_en,
                "name_pt": row.name_pt,
                "timezone": row.timezone,
                "is_active": row.is_active,
            },
            reason="City created from Super Admin dictionaries",
        )

        return self._city_card(row, language)

    async def list_city_cards(
        self,
        *,
        country_id: str,
        limit: int = 50,
        offset: int = 0,
        language: str = "ru",
    ) -> list[AdminCityDictionaryCard]:
        try:
            parsed_country_id = UUID(str(country_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        rows = await self.repository.list_cities_for_admin(
            country_id=parsed_country_id,
            limit=limit,
            offset=offset,
        )
        return [self._city_card(row, language) for row in rows]

    def _city_card(
        self,
        row: AdminCityDictionaryRow,
        language: str,
    ) -> AdminCityDictionaryCard:
        title = {
            "ru": row.name_ru,
            "en": row.name_en,
            "pt": row.name_pt,
        }.get(language) or row.name

        status_code = "active" if row.is_active else "hidden"
        status = "Активен" if row.is_active else "Скрыт"

        return AdminCityDictionaryCard(
            city_id=row.city_id,
            country_id=row.country_id,
            title=title,
            country_name=row.country_name,
            status=status,
            status_code=status_code,
            latitude=row.latitude,
            longitude=row.longitude,
            timezone=row.timezone,
            specialists_count=row.specialists_count,
        )

    async def get_city_card(
        self,
        city_id: str,
        *,
        language: str = "ru",
    ) -> AdminCityDictionaryCard | None:
        try:
            parsed_city_id = UUID(str(city_id))
        except (TypeError, ValueError):
            return None

        row = await self.repository.get_city_for_admin(parsed_city_id)

        if not row:
            return None

        return self._city_card(row, language)

    async def toggle_city_visibility(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        city_id: str,
        language: str = "ru",
    ) -> AdminCityDictionaryCard:
        try:
            parsed_city_id = UUID(str(city_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_city_for_admin(parsed_city_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        after = await self.repository.set_city_visibility_for_admin(
            city_id=parsed_city_id,
            is_active=not before.is_active,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        action_type = (
            "activate_city"
            if after.is_active
            else "deactivate_city"
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type=action_type,
            target_type="city",
            target_id=parsed_city_id,
            before_state={
                "name": before.name,
                "country_id": str(before.country_id),
                "is_active": before.is_active,
            },
            after_state={
                "name": after.name,
                "country_id": str(after.country_id),
                "is_active": after.is_active,
            },
            reason="City visibility changed from Super Admin dictionaries",
        )

        return self._city_card(after, language)

    async def update_city(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        city_id: str,
        payload: str,
        language: str = "ru",
    ) -> AdminCityDictionaryCard:
        try:
            parsed_city_id = UUID(str(city_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        (
            name,
            name_ru,
            name_en,
            name_pt,
            timezone,
        ) = self._parse_city_update_payload(payload)

        before = await self.repository.get_city_for_admin(parsed_city_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        after = await self.repository.update_city_for_admin(
            city_id=parsed_city_id,
            name=name,
            name_ru=name_ru,
            name_en=name_en,
            name_pt=name_pt,
            latitude=before.latitude,
            longitude=before.longitude,
            timezone=timezone,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="update_city",
            target_type="city",
            target_id=parsed_city_id,
            before_state={
                "name": before.name,
                "name_ru": before.name_ru,
                "name_en": before.name_en,
                "name_pt": before.name_pt,
                "country_id": str(before.country_id),
                "timezone": before.timezone,
            },
            after_state={
                "name": after.name,
                "name_ru": after.name_ru,
                "name_en": after.name_en,
                "name_pt": after.name_pt,
                "country_id": str(after.country_id),
                "timezone": after.timezone,
            },
            reason="City updated from Super Admin dictionaries",
        )

        return self._city_card(after, language)

    async def update_city_geo(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        city_id: str,
        payload: str,
        language: str = "ru",
    ) -> AdminCityDictionaryCard:
        try:
            parsed_city_id = UUID(str(city_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        latitude, longitude = self._parse_city_geo_payload(payload)

        before = await self.repository.get_city_for_admin(parsed_city_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        after = await self.repository.update_city_for_admin(
            city_id=parsed_city_id,
            name=before.name,
            name_ru=before.name_ru,
            name_en=before.name_en,
            name_pt=before.name_pt,
            latitude=latitude,
            longitude=longitude,
            timezone=before.timezone,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="update_city_geo",
            target_type="city",
            target_id=parsed_city_id,
            before_state={
                "name": before.name,
                "name_ru": before.name_ru,
                "name_en": before.name_en,
                "name_pt": before.name_pt,
                "country_id": str(before.country_id),
                "latitude": before.latitude,
                "longitude": before.longitude,
            },
            after_state={
                "name": after.name,
                "name_ru": after.name_ru,
                "name_en": after.name_en,
                "name_pt": after.name_pt,
                "country_id": str(after.country_id),
                "latitude": after.latitude,
                "longitude": after.longitude,
            },
            reason="City geo updated from Super Admin dictionaries",
        )

        return self._city_card(after, language)

    def _category_code_from_title(self, title: str) -> str:
        translit_map = str.maketrans({
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
            "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
            "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
            "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
            "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
            "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
            "э": "e", "ю": "yu", "я": "ya",
        })

        base = title.strip().lower().translate(translit_map)
        base = re.sub(r"[^a-z0-9]+", "_", base)
        base = re.sub(r"_+", "_", base).strip("_")

        return base[:64] or "category"

    async def create_category(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        title: str,
        language: str = "ru",
    ) -> AdminCategoryDictionaryCard:
        cleaned_title = " ".join((title or "").split())

        if len(cleaned_title) < 2:
            raise DictionaryServiceError("admin_dict_category_create_empty")

        if await self.repository.category_title_exists(title=cleaned_title):
            raise DictionaryServiceError("admin_dict_category_rename_duplicate")

        base_code = self._category_code_from_title(cleaned_title)
        code = base_code
        suffix = 2

        while await self.repository.category_code_exists(code=code):
            code = f"{base_code}_{suffix}"
            suffix += 1

            if suffix > 100:
                raise DictionaryServiceError("admin_dict_category_create_duplicate_code")

        sort_order = await self.repository.next_category_sort_order()

        row = await self.repository.create_category_for_admin(
            code=code,
            title=cleaned_title,
            language=language,
            sort_order=sort_order,
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_category_created",
            target_type="specialist_category",
            target_id=row.category_id,
            before_state={},
            after_state={
                "code": row.code,
                "name": row.name,
                "name_ru": row.name_ru,
                "name_en": row.name_en,
                "name_pt": row.name_pt,
                "sort_order": row.sort_order,
                "is_active": row.is_active,
                "metadata": row.metadata,
            },
            reason="Category created from Super Admin dictionaries",
        )

        return self._category_card(row, language)
    
    async def list_category_specialist_ids(
        self,
        *,
        category_id: str,
    ) -> list[str]:
        try:
            parsed_category_id = UUID(
                str(category_id)
            )
        except (TypeError, ValueError):
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        category = (
            await self.repository.get_category_for_admin(
                parsed_category_id
            )
        )

        if not category:
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        specialist_ids = (
            await self.repository
            .list_category_specialist_ids_for_admin(
                category_id=parsed_category_id
            )
        )

        return [
            str(specialist_id)
            for specialist_id in specialist_ids
        ]

    async def list_category_specialists(
        self,
        *,
        category_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminCategorySpecialistCard]:
        try:
            parsed_category_id = UUID(str(category_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        category = await self.repository.get_category_for_admin(parsed_category_id)

        if not category:
            raise DictionaryServiceError("admin_item_not_found")

        rows = await self.repository.list_category_specialists_for_admin(
            category_id=parsed_category_id,
            limit=limit,
            offset=offset,
        )

        return [
            AdminCategorySpecialistCard(
                specialist_id=row.specialist_id,
                display_name=row.display_name,
                status=row.status,
                profession_names=row.profession_names,
                is_verified=row.is_verified,
                is_available=row.is_available,
            )
            for row in rows
        ]

    async def list_specialist_move_target_categories(
        self,
        *,
        language: str = "ru",
    ) -> list[AdminCategoryDictionaryCard]:
        rows = await self.repository.list_categories_for_admin(
            limit=500,
            offset=0,
        )

        return [
            self._category_card(row, language)
            for row in rows
            if row.is_active
            and not (
                row.metadata
                and row.metadata.get("archived")
            )
        ]

    async def find_specialist_move_target_categories(
        self,
        *,
        title: str,
        language: str = "ru",
    ) -> list[AdminCategoryDictionaryCard]:
        cleaned_title = " ".join(
            (title or "").split()
        )

        if not cleaned_title:
            raise DictionaryServiceError(
                "admin_dict_target_category_not_found"
            )

        rows = (
            await self.repository
            .find_categories_by_title_for_admin(
                title=cleaned_title,
            )
        )

        cards = [
            self._category_card(row, language)
            for row in rows
            if row.is_active
            and not (
                row.metadata
                and row.metadata.get("archived")
            )
        ]

        if not cards:
            raise DictionaryServiceError(
                "admin_dict_target_category_not_found"
            )

        return cards

    async def list_active_professions_for_category(
        self,
        *,
        category_id: str,
        language: str = "ru",
    ) -> list[AdminProfessionDictionaryCard]:
        try:
            parsed_category_id = UUID(
                str(category_id)
            )
        except (TypeError, ValueError):
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        category = (
            await self.repository.get_category_for_admin(
                parsed_category_id
            )
        )

        if not category:
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        if (
            not category.is_active
            or (
                category.metadata
                and category.metadata.get("archived")
            )
        ):
            raise DictionaryServiceError(
                "admin_dict_target_category_unavailable"
            )

        rows = (
            await self.repository
            .list_professions_by_category_for_admin(
                category_id=parsed_category_id,
                limit=500,
                offset=0,
            )
        )

        cards = [
            self._profession_card(row, language)
            for row in rows
            if row.is_active
            and not (
                row.metadata
                and row.metadata.get("archived")
            )
        ]

        if not cards:
            raise DictionaryServiceError(
                "admin_dict_target_category_no_professions"
            )

        return cards

    async def list_profession_cards(
        self,
        *,
        language: str = "ru",
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminProfessionDictionaryCard]:
        rows = await self.repository.list_professions_for_admin(
            limit=limit,
            offset=offset,
        )

        return [
            self._profession_card(row, language)
            for row in rows
        ]

    def _profession_card(
        self,
        row: AdminProfessionDictionaryRow,
        language: str,
    ) -> AdminProfessionDictionaryCard:
        title = {
            "ru": row.name_ru,
            "en": row.name_en,
            "pt": row.name_pt,
        }.get(language) or row.name_ru or row.name_en or row.name_pt or row.name

        release = None
        if row.metadata:
            release = row.metadata.get("release")

        status_code = "active" if row.is_active else "hidden"

        if row.metadata and row.metadata.get("archived"):
            status_code = "archived"

        status_labels = {
            "ru": {
                "active": "Активно",
                "hidden": "Скрыто",
                "archived": "Архив",
            },
            "en": {
                "active": "Active",
                "hidden": "Hidden",
                "archived": "Archived",
            },
            "pt": {
                "active": "Ativo",
                "hidden": "Oculto",
                "archived": "Arquivado",
            },
        }

        status = status_labels.get(language, status_labels["ru"]).get(
            status_code,
            status_code,
        )

        return AdminProfessionDictionaryCard(
            profession_id=row.profession_id,
            category_id=row.category_id,
            code=row.code,
            title=title,
            category_name=row.category_name,
            sort_order=row.sort_order,
            status=status,
            status_code=status_code,
            release=release,
            specialists_count=row.specialists_count,
        )

    async def get_profession_card(
        self,
        *,
        profession_id: str,
        language: str = "ru",
    ) -> AdminProfessionDictionaryCard | None:
        try:
            parsed_profession_id = UUID(str(profession_id))
        except (TypeError, ValueError):
            return None

        row = await self.repository.get_profession_for_admin(
            parsed_profession_id
        )

        if not row:
            return None

        return self._profession_card(row, language)
    
    async def find_specialist_move_targets(
        self,
        *,
        title: str,
        source_profession_id: str | None = None,
        language: str = "ru",
    ) -> list[AdminProfessionDictionaryCard]:
        cleaned_title = " ".join((title or "").split())

        if not cleaned_title:
            raise DictionaryServiceError(
                "admin_dict_specialist_move_target_not_found"
            )

        parsed_source_profession_id = None

        if source_profession_id:
            try:
                parsed_source_profession_id = UUID(
                    str(source_profession_id)
                )
            except (TypeError, ValueError):
                raise DictionaryServiceError(
                    "admin_item_not_found"
                )

        rows = (
            await self.repository
            .find_professions_by_title_for_admin(
                title=cleaned_title,
            )
        )

        targets = []

        for row in rows:
            if (
                parsed_source_profession_id
                and row.profession_id
                == parsed_source_profession_id
            ):
                continue

            if not row.is_active:
                continue

            if (
                row.metadata
                and row.metadata.get("archived")
            ):
                continue

            targets.append(
                self._profession_card(row, language)
            )

        if not targets:
            raise DictionaryServiceError(
                "admin_dict_specialist_move_target_not_found"
            )

        return targets
    
    async def create_profession(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        category_id: str | None,
        category_code: str | None,
        title: str,
        language: str = "ru",
    ) -> AdminProfessionDictionaryCard:
        cleaned_title = " ".join((title or "").split())

        if len(cleaned_title) < 2:
            raise DictionaryServiceError("admin_dict_profession_create_empty")

        category = None

        if category_id:
            try:
                parsed_category_id = UUID(str(category_id))
            except (TypeError, ValueError):
                raise DictionaryServiceError("admin_item_not_found")

            category = await self.repository.get_category_for_admin(
                parsed_category_id
            )

        elif category_code:
            category = await self.repository.get_category_by_code_or_title_for_admin(
                category_code
            )

        if not category:
            raise DictionaryServiceError("admin_dict_profession_category_not_found")

        if category.metadata and category.metadata.get("archived"):
            raise DictionaryServiceError("admin_dict_profession_category_archived")

        if await self.repository.profession_title_exists(
            category_id=category.category_id,
            title=cleaned_title,
        ):
            raise DictionaryServiceError("admin_dict_profession_create_duplicate")

        base_code = self._category_code_from_title(cleaned_title)
        code = base_code
        suffix = 2

        while await self.repository.profession_code_exists(code=code):
            code = f"{base_code}_{suffix}"
            suffix += 1

            if suffix > 100:
                raise DictionaryServiceError("admin_dict_profession_create_duplicate_code")

        sort_order = await self.repository.next_profession_sort_order(
            category_id=category.category_id,
        )

        release = None
        if category.metadata:
            release = category.metadata.get("release")

        row = await self.repository.create_profession_for_admin(
            category_id=category.category_id,
            code=code,
            title=cleaned_title,
            language=language,
            sort_order=sort_order,
            release=release,
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_profession_created",
            target_type="profession",
            target_id=row.profession_id,
            before_state={},
            after_state={
                "category_id": str(row.category_id),
                "code": row.code,
                "name": row.name,
                "name_ru": row.name_ru,
                "name_en": row.name_en,
                "name_pt": row.name_pt,
                "normalized_name": row.normalized_name,
                "sort_order": row.sort_order,
                "is_active": row.is_active,
                "metadata": row.metadata,
            },
            reason="Profession created from Super Admin dictionaries",
        )

        return self._profession_card(row, language)
    
    async def rename_profession(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        profession_id: str,
        title: str,
        language: str = "ru",
    ) -> AdminProfessionDictionaryCard:
        cleaned_title = " ".join((title or "").split())

        if len(cleaned_title) < 2:
            raise DictionaryServiceError("admin_dict_profession_rename_empty")

        try:
            parsed_profession_id = UUID(str(profession_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_profession_for_admin(
            parsed_profession_id
        )

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        if await self.repository.profession_title_exists(
            category_id=before.category_id,
            title=cleaned_title,
        ):
            raise DictionaryServiceError("admin_dict_profession_rename_duplicate")

        after = await self.repository.rename_profession_for_admin(
            profession_id=parsed_profession_id,
            language=language,
            title=cleaned_title,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_profession_renamed",
            target_type="profession",
            target_id=parsed_profession_id,
            before_state={
                "name": before.name,
                "name_ru": before.name_ru,
                "name_en": before.name_en,
                "name_pt": before.name_pt,
                "normalized_name": before.normalized_name,
            },
            after_state={
                "name": after.name,
                "name_ru": after.name_ru,
                "name_en": after.name_en,
                "name_pt": after.name_pt,
                "normalized_name": after.normalized_name,
            },
            reason="Profession renamed from Super Admin dictionaries",
        )

        return self._profession_card(after, language)
    
    async def move_profession_to_category(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        profession_id: str,
        category_code: str,
        language: str = "ru",
    ) -> AdminProfessionDictionaryCard:
        cleaned_category_code = (category_code or "").strip()

        if not cleaned_category_code:
            raise DictionaryServiceError("admin_dict_profession_move_category_empty")

        try:
            parsed_profession_id = UUID(str(profession_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_profession_for_admin(
            parsed_profession_id
        )

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        category = await self.repository.get_category_by_code_or_title_for_admin(
            cleaned_category_code
        )

        if not category:
            raise DictionaryServiceError("admin_dict_profession_category_not_found")

        if category.metadata and category.metadata.get("archived"):
            raise DictionaryServiceError("admin_dict_profession_category_archived")

        if category.category_id == before.category_id:
            raise DictionaryServiceError("admin_dict_profession_move_same_category")

        if await self.repository.profession_title_exists(
            category_id=category.category_id,
            title=before.name,
        ):
            raise DictionaryServiceError("admin_dict_profession_move_duplicate")

        after = await self.repository.move_profession_to_category_for_admin(
            profession_id=parsed_profession_id,
            category_id=category.category_id,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_profession_moved",
            target_type="profession",
            target_id=parsed_profession_id,
            before_state={
                "category_id": str(before.category_id),
                "category_name": before.category_name,
            },
            after_state={
                "category_id": str(after.category_id),
                "category_name": after.category_name,
            },
            reason="Profession moved to another category from Super Admin dictionaries",
        )

        return self._profession_card(after, language)
    
    async def toggle_profession_visibility(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        profession_id: str,
        language: str = "ru",
    ) -> AdminProfessionDictionaryCard:
        try:
            parsed_profession_id = UUID(str(profession_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_profession_for_admin(
            parsed_profession_id
        )

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        new_is_active = not before.is_active

        after = await self.repository.set_profession_visibility_for_admin(
            profession_id=parsed_profession_id,
            is_active=new_is_active,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_profession_visibility_changed",
            target_type="profession",
            target_id=parsed_profession_id,
            before_state={
                "is_active": before.is_active,
            },
            after_state={
                "is_active": after.is_active,
            },
            reason="Profession visibility changed from Super Admin dictionaries",
        )

        return self._profession_card(after, language)
    
    async def archive_profession(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        profession_id: str,
        language: str = "ru",
    ) -> AdminProfessionDictionaryCard:
        try:
            parsed_profession_id = UUID(str(profession_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_profession_for_admin(
            parsed_profession_id
        )

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        if before.metadata and before.metadata.get("archived"):
            raise DictionaryServiceError("admin_dict_profession_already_archived")

        after = await self.repository.archive_profession_for_admin(
            profession_id=parsed_profession_id,
            admin_user_id=admin_user_id,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_profession_archived",
            target_type="profession",
            target_id=parsed_profession_id,
            before_state={
                "is_active": before.is_active,
                "metadata": before.metadata,
            },
            after_state={
                "is_active": after.is_active,
                "metadata": after.metadata,
            },
            reason="Profession archived from Super Admin dictionaries",
        )

        return self._profession_card(after, language)

    async def unarchive_profession(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        profession_id: str,
        language: str = "ru",
    ) -> AdminProfessionDictionaryCard:
        try:
            parsed_profession_id = UUID(str(profession_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_profession_for_admin(
            parsed_profession_id
        )

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        if not before.metadata or not before.metadata.get("archived"):
            raise DictionaryServiceError("admin_dict_profession_not_archived")

        after = await self.repository.unarchive_profession_for_admin(
            profession_id=parsed_profession_id,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_profession_unarchived",
            target_type="profession",
            target_id=parsed_profession_id,
            before_state={
                "is_active": before.is_active,
                "metadata": before.metadata,
            },
            after_state={
                "is_active": after.is_active,
                "metadata": after.metadata,
            },
            reason="Profession returned from archive in Super Admin dictionaries",
        )

        return self._profession_card(after, language)
    
    async def list_profession_specialist_ids(
        self,
        *,
        profession_id: str,
    ) -> list[str]:
        try:
            parsed_profession_id = UUID(str(profession_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        profession = await self.repository.get_profession_for_admin(
            parsed_profession_id
        )

        if not profession:
            raise DictionaryServiceError("admin_item_not_found")

        specialist_ids = await self.repository.list_profession_specialist_ids_for_admin(
            profession_id=parsed_profession_id,
        )

        return [
            str(specialist_id)
            for specialist_id in specialist_ids
        ]

    async def preview_multi_profession_move(
        self,
        *,
        source_type: str,
        source_id: str,
        target_category_id: str,
        target_profession_ids: list[str],
        specialist_ids: list[str],
        mode: str,
        language: str = "ru",
    ) -> AdminMultiProfessionMovePreviewCard:
        if source_type not in {
            "category",
            "profession",
        }:
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        if mode not in {"replace", "add"}:
            raise DictionaryServiceError(
                "admin_dict_move_mode_invalid"
            )

        try:
            parsed_source_id = UUID(str(source_id))
            parsed_target_category_id = UUID(
                str(target_category_id)
            )
            parsed_target_profession_ids = [
                UUID(str(profession_id))
                for profession_id
                in target_profession_ids
            ]
            parsed_specialist_ids = [
                UUID(str(specialist_id))
                for specialist_id in specialist_ids
            ]
        except (TypeError, ValueError):
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        parsed_target_profession_ids = list(
            dict.fromkeys(parsed_target_profession_ids)
        )
        if (
            len(parsed_target_profession_ids)
            > MAX_PROFESSIONS_PER_CATEGORY
        ):
            raise DictionaryServiceError(
                "spec_profession_limit_per_category"
            )
        parsed_specialist_ids = list(
            dict.fromkeys(parsed_specialist_ids)
        )

        if (
            not parsed_target_profession_ids
            or not parsed_specialist_ids
        ):
            raise DictionaryServiceError(
                "admin_dict_specialist_move_empty"
            )

        target_category = (
            await self.repository.get_category_for_admin(
                parsed_target_category_id
            )
        )

        if (
            not target_category
            or not target_category.is_active
            or (
                target_category.metadata
                and target_category.metadata.get("archived")
            )
        ):
            raise DictionaryServiceError(
                "admin_dict_target_category_unavailable"
            )

        target_profession_rows = []

        for profession_id in parsed_target_profession_ids:
            profession = (
                await self.repository
                .get_profession_for_admin(
                    profession_id
                )
            )

            if (
                not profession
                or not profession.is_active
                or (
                    profession.metadata
                    and profession.metadata.get("archived")
                )
                or profession.category_id
                != parsed_target_category_id
            ):
                raise DictionaryServiceError(
                    "admin_dict_move_profession_category_mismatch"
                )

            target_profession_rows.append(
                profession
            )

        if source_type == "category":
            source = (
                await self.repository
                .get_category_for_admin(
                    parsed_source_id
                )
            )

            if not source:
                raise DictionaryServiceError(
                    "admin_item_not_found"
                )

            source_title = self._category_card(
                source,
                language,
            ).title

            source_specialists = (
                await self.repository
                .list_category_specialists_for_admin(
                    category_id=parsed_source_id,
                    limit=500,
                    offset=0,
                )
            )

        else:
            source = (
                await self.repository
                .get_profession_for_admin(
                    parsed_source_id
                )
            )

            if not source:
                raise DictionaryServiceError(
                    "admin_item_not_found"
                )

            source_title = self._profession_card(
                source,
                language,
            ).title

            source_specialists = (
                await self.repository
                .list_profession_specialists_for_admin(
                    profession_id=parsed_source_id,
                    limit=500,
                    offset=0,
                )
            )

        selected_ids = set(parsed_specialist_ids)

        selected_specialists = tuple(
            AdminCategorySpecialistCard(
                specialist_id=row.specialist_id,
                display_name=row.display_name,
                status=row.status,
                profession_names=row.profession_names,
                is_verified=row.is_verified,
                is_available=row.is_available,
            )
            for row in source_specialists
            if row.specialist_id in selected_ids
        )

        if not selected_specialists:
            raise DictionaryServiceError(
                "admin_dict_specialist_move_empty"
            )

        return AdminMultiProfessionMovePreviewCard(
            source_type=source_type,
            source_title=source_title,
            target_category=self._category_card(
                target_category,
                language,
            ),
            target_professions=tuple(
                self._profession_card(
                    profession,
                    language,
                )
                for profession in target_profession_rows
            ),
            selected_specialists=selected_specialists,
            mode=mode,
        )

    async def move_specialists_to_multiple_professions(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        source_type: str,
        source_id: str,
        target_category_id: str,
        target_profession_ids: list[str],
        specialist_ids: list[str],
        mode: str,
        language: str = "ru",
    ) -> AdminMultiProfessionMoveCard:
        preview = await self.preview_multi_profession_move(
            source_type=source_type,
            source_id=source_id,
            target_category_id=target_category_id,
            target_profession_ids=target_profession_ids,
            specialist_ids=specialist_ids,
            mode=mode,
            language=language,
        )

        parsed_source_id = UUID(str(source_id))
        parsed_target_category_id = UUID(
            str(target_category_id)
        )
        parsed_target_profession_ids = [
            UUID(str(profession_id))
            for profession_id in target_profession_ids
        ]
        parsed_specialist_ids = [
            UUID(str(specialist_id))
            for specialist_id in specialist_ids
        ]

        result: AdminMultiProfessionMoveResult = (
            await self.repository
            .move_specialists_to_multiple_professions_for_admin(
                source_type=source_type,
                source_id=parsed_source_id,
                target_category_id=(
                    parsed_target_category_id
                ),
                target_profession_ids=(
                    parsed_target_profession_ids
                ),
                specialist_ids=parsed_specialist_ids,
                mode=mode,
            )
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type=(
                "dictionary_specialists_multi_moved"
            ),
            target_type=source_type,
            target_id=parsed_source_id,
            before_state={
                "source_type": source_type,
                "source_id": str(parsed_source_id),
                "source_title": preview.source_title,
                "target_category_id": str(
                    preview.target_category.category_id
                ),
                "target_category_title": (
                    preview.target_category.title
                ),
                "target_professions": [
                    {
                        "profession_id": str(
                            profession.profession_id
                        ),
                        "title": profession.title,
                    }
                    for profession
                    in preview.target_professions
                ],
                "specialist_ids": [
                    str(specialist_id)
                    for specialist_id
                    in parsed_specialist_ids
                ],
                "mode": mode,
            },
            after_state={
                "requested_specialists_count": (
                    result.requested_specialists_count
                ),
                "selected_professions_count": (
                    result.selected_professions_count
                ),
                "created_links_count": (
                    result.created_links_count
                ),
                "reactivated_links_count": (
                    result.reactivated_links_count
                ),
                "existing_links_count": (
                    result.existing_links_count
                ),
                "deleted_old_links_count": (
                    result.deleted_old_links_count
                ),
                "synchronized_primary_count": (
                    result.synchronized_primary_count
                ),
                "missing_specialists_count": (
                    result.missing_specialists_count
                ),
                "target_category_id": str(
                    result.target_category_id
                ),
                "target_profession_ids": [
                    str(profession_id)
                    for profession_id
                    in result.target_profession_ids
                ],
                "mode": result.mode,
            },
            reason=(
                "Specialists moved to multiple professions "
                "from Super Admin dictionaries"
            ),
        )

        return AdminMultiProfessionMoveCard(
            source_type=preview.source_type,
            source_title=preview.source_title,
            target_category=preview.target_category,
            target_professions=(
                preview.target_professions
            ),
            mode=result.mode,
            requested_specialists_count=(
                result.requested_specialists_count
            ),
            selected_professions_count=(
                result.selected_professions_count
            ),
            created_links_count=(
                result.created_links_count
            ),
            reactivated_links_count=(
                result.reactivated_links_count
            ),
            existing_links_count=(
                result.existing_links_count
            ),
            deleted_old_links_count=(
                result.deleted_old_links_count
            ),
            synchronized_primary_count=(
                result.synchronized_primary_count
            ),
            missing_specialists_count=(
                result.missing_specialists_count
            ),
        )

    async def preview_category_specialist_move(
        self,
        *,
        source_category_id: str,
        target_profession_id: str,
        specialist_ids: list[str],
        language: str = "ru",
    ) -> AdminCategorySpecialistMovePreviewCard:
        try:
            parsed_source_category_id = UUID(
                str(source_category_id)
            )
            parsed_target_profession_id = UUID(
                str(target_profession_id)
            )
            parsed_specialist_ids = [
                UUID(str(specialist_id))
                for specialist_id in specialist_ids
            ]
        except (TypeError, ValueError):
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        if not parsed_specialist_ids:
            raise DictionaryServiceError(
                "admin_dict_specialist_move_empty"
            )

        source_category = (
            await self.repository.get_category_for_admin(
                parsed_source_category_id
            )
        )
        target_profession = (
            await self.repository.get_profession_for_admin(
                parsed_target_profession_id
            )
        )

        if not source_category or not target_profession:
            raise DictionaryServiceError(
                "admin_item_not_found"
            )

        if (
            not target_profession.is_active
            or (
                target_profession.metadata
                and target_profession.metadata.get("archived")
            )
        ):
            raise DictionaryServiceError(
                "admin_dict_specialist_move_target_not_found"
            )

        source_specialists = (
            await self.repository
            .list_category_specialists_for_admin(
                category_id=parsed_source_category_id,
                limit=500,
                offset=0,
            )
        )

        selected_ids = set(parsed_specialist_ids)

        selected_specialists = tuple(
            AdminCategorySpecialistCard(
                specialist_id=row.specialist_id,
                display_name=row.display_name,
                status=row.status,
                profession_names=row.profession_names,
                is_verified=row.is_verified,
                is_available=row.is_available,
            )
            for row in source_specialists
            if row.specialist_id in selected_ids
        )

        if not selected_specialists:
            raise DictionaryServiceError(
                "admin_dict_specialist_move_empty"
            )

        return AdminCategorySpecialistMovePreviewCard(
            source_category=self._category_card(
                source_category,
                language,
            ),
            target_profession=self._profession_card(
                target_profession,
                language,
            ),
            selected_specialists=selected_specialists,
        )

    async def move_category_specialists_to_profession(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        source_category_id: str,
        target_profession_id: str,
        specialist_ids: list[str],
        language: str = "ru",
    ) -> AdminCategorySpecialistMoveCard:
        preview = await self.preview_category_specialist_move(
            source_category_id=source_category_id,
            target_profession_id=target_profession_id,
            specialist_ids=specialist_ids,
            language=language,
        )

        parsed_specialist_ids = [
            UUID(str(specialist_id))
            for specialist_id in specialist_ids
        ]

        result: AdminCategorySpecialistMoveResult = (
            await self.repository
            .move_category_specialists_to_profession_for_admin(
                source_category_id=(
                    preview.source_category.category_id
                ),
                target_profession_id=(
                    preview.target_profession.profession_id
                ),
                target_category_id=(
                    preview.target_profession.category_id
                ),
                specialist_ids=parsed_specialist_ids,
            )
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type=(
                "dictionary_category_specialists_moved"
            ),
            target_type="category",
            target_id=preview.source_category.category_id,
            before_state={
                "source_category_id": str(
                    preview.source_category.category_id
                ),
                "source_category_title": (
                    preview.source_category.title
                ),
                "target_category_id": str(
                    preview.target_profession.category_id
                ),
                "target_category_title": (
                    preview.target_profession.category_name
                ),
                "target_profession_id": str(
                    preview.target_profession.profession_id
                ),
                "target_profession_title": (
                    preview.target_profession.title
                ),
                "specialist_ids": [
                    str(specialist_id)
                    for specialist_id in parsed_specialist_ids
                ],
            },
            after_state={
                "requested_count": result.requested_count,
                "moved_count": result.moved_count,
                "archived_duplicate_count": (
                    result.archived_duplicate_count
                ),
                "archived_extra_links_count": (
                    result.archived_extra_links_count
                ),
                "synchronized_primary_count": (
                    result.synchronized_primary_count
                ),
                "missing_count": result.missing_count,
                "target_category_id": str(
                    result.target_category_id
                ),
                "target_profession_id": str(
                    result.target_profession_id
                ),
            },
            reason=(
                "Specialists moved from category through "
                "Super Admin dictionaries"
            ),
        )

        return AdminCategorySpecialistMoveCard(
            source_category=preview.source_category,
            target_profession=preview.target_profession,
            requested_count=result.requested_count,
            moved_count=result.moved_count,
            archived_duplicate_count=(
                result.archived_duplicate_count
            ),
            archived_extra_links_count=(
                result.archived_extra_links_count
            ),
            synchronized_primary_count=(
                result.synchronized_primary_count
            ),
            missing_count=result.missing_count,
        )

    async def preview_specialist_move(
        self,
        *,
        source_profession_id: str,
        target_profession_id: str,
        specialist_ids: list[str],
        language: str = "ru",
    ) -> AdminSpecialistMovePreviewCard:
        try:
            parsed_source_profession_id = UUID(str(source_profession_id))
            parsed_target_profession_id = UUID(str(target_profession_id))
            parsed_specialist_ids = [
                UUID(str(specialist_id))
                for specialist_id in specialist_ids
            ]
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        if not parsed_specialist_ids:
            raise DictionaryServiceError("admin_dict_specialist_move_empty")

        source = await self.repository.get_profession_for_admin(
            parsed_source_profession_id
        )
        target = await self.repository.get_profession_for_admin(
            parsed_target_profession_id
        )

        if not source or not target:
            raise DictionaryServiceError("admin_item_not_found")

        source_specialists = await self.repository.list_profession_specialists_for_admin(
            profession_id=parsed_source_profession_id,
            limit=500,
            offset=0,
        )
        selected_ids = set(parsed_specialist_ids)
        selected_specialists = tuple(
            AdminCategorySpecialistCard(
                specialist_id=row.specialist_id,
                display_name=row.display_name,
                status=row.status,
                profession_names=row.profession_names,
                is_verified=row.is_verified,
                is_available=row.is_available,
            )
            for row in source_specialists
            if row.specialist_id in selected_ids
        )

        if not selected_specialists:
            raise DictionaryServiceError("admin_dict_specialist_move_empty")

        return AdminSpecialistMovePreviewCard(
            source_profession=self._profession_card(source, language),
            target_profession=self._profession_card(target, language),
            selected_specialists=selected_specialists,
        )

    async def move_specialists_to_profession(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        source_profession_id: str,
        target_profession_id: str,
        specialist_ids: list[str],
        language: str = "ru",
    ) -> AdminSpecialistMoveCard:
        preview = await self.preview_specialist_move(
            source_profession_id=source_profession_id,
            target_profession_id=target_profession_id,
            specialist_ids=specialist_ids,
            language=language,
        )

        parsed_specialist_ids = [
            UUID(str(specialist_id))
            for specialist_id in specialist_ids
        ]

        result = await self.repository.move_specialists_to_profession_for_admin(
            source_profession_id=preview.source_profession.profession_id,
            target_profession_id=preview.target_profession.profession_id,
            target_category_id=preview.target_profession.category_id,
            specialist_ids=parsed_specialist_ids,
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_specialists_moved",
            target_type="profession",
            target_id=preview.target_profession.profession_id,
            before_state={
                "source_profession_id": str(preview.source_profession.profession_id),
                "source_profession_title": preview.source_profession.title,
                "target_profession_id": str(preview.target_profession.profession_id),
                "target_profession_title": preview.target_profession.title,
                "specialist_ids": [
                    str(specialist_id)
                    for specialist_id in parsed_specialist_ids
                ],
            },
            after_state={
                "requested_count": result.requested_count,
                "moved_count": result.moved_count,
                "archived_duplicate_count": result.archived_duplicate_count,
                "synchronized_primary_count": (
                    result.synchronized_primary_count
                ),
                "missing_count": result.missing_count,
                "target_category_id": str(result.target_category_id),
                "target_profession_id": str(result.target_profession_id),
            },
            reason="Specialists moved between professions from Super Admin dictionaries",
        )

        return AdminSpecialistMoveCard(
            source_profession=preview.source_profession,
            target_profession=preview.target_profession,
            requested_count=result.requested_count,
            moved_count=result.moved_count,
            archived_duplicate_count=result.archived_duplicate_count,
            synchronized_primary_count=(
                result.synchronized_primary_count
            ),
            missing_count=result.missing_count,
        )


    async def list_profession_specialists(
        self,
        *,
        profession_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminCategorySpecialistCard]:
        try:
            parsed_profession_id = UUID(str(profession_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        profession = await self.repository.get_profession_for_admin(
            parsed_profession_id
        )

        if not profession:
            raise DictionaryServiceError("admin_item_not_found")

        rows = await self.repository.list_profession_specialists_for_admin(
            profession_id=parsed_profession_id,
            limit=limit,
            offset=offset,
        )

        return [
            AdminCategorySpecialistCard(
                specialist_id=row.specialist_id,
                display_name=row.display_name,
                status=row.status,
                profession_names=row.profession_names,
                is_verified=row.is_verified,
                is_available=row.is_available,
            )
            for row in rows
        ]
    
    async def list_skill_cards(
        self,
        *,
        language: str = "ru",
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminSkillDictionaryCard]:
        rows = await self.repository.list_skills_for_admin(
            limit=limit,
            offset=offset,
        )

        return [
            self._skill_card(row, language)
            for row in rows
        ]

    def _skill_card(
        self,
        row: AdminSkillDictionaryRow,
        language: str,
    ) -> AdminSkillDictionaryCard:
        title = {
            "ru": row.name_ru,
            "en": row.name_en,
            "pt": row.name_pt,
        }.get(language) or row.name_ru or row.name_en or row.name_pt or row.name

        status_code = "active" if row.is_active else "hidden"

        status_labels = {
            "ru": {
                "active": "Активно",
                "hidden": "Скрыто",
            },
            "en": {
                "active": "Active",
                "hidden": "Hidden",
            },
            "pt": {
                "active": "Ativo",
                "hidden": "Oculto",
            },
        }

        status = status_labels.get(language, status_labels["ru"]).get(
            status_code,
            status_code,
        )

        return AdminSkillDictionaryCard(
            skill_id=row.skill_id,
            code=row.code,
            title=title,
            status=status,
            status_code=status_code,
            profession_links_count=row.profession_links_count,
            user_links_count=row.user_links_count,
            vacancy_links_count=row.vacancy_links_count,
        )

    async def get_skill_card(
        self,
        *,
        skill_id: str,
        language: str = "ru",
    ) -> AdminSkillDictionaryCard | None:
        try:
            parsed_skill_id = UUID(str(skill_id))
        except (TypeError, ValueError):
            return None

        row = await self.repository.get_skill_for_admin(parsed_skill_id)

        if not row:
            return None

        return self._skill_card(row, language)
    
    async def create_skill(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        title: str,
        language: str = "ru",
    ) -> AdminSkillDictionaryCard:
        cleaned_title = " ".join((title or "").split())

        if len(cleaned_title) < 2:
            raise DictionaryServiceError("admin_dict_skill_create_empty")

        if await self.repository.skill_title_exists(title=cleaned_title):
            raise DictionaryServiceError("admin_dict_skill_create_duplicate")

        base_code = self._category_code_from_title(cleaned_title)
        code = base_code
        suffix = 2

        while await self.repository.skill_code_exists(code=code):
            code = f"{base_code}_{suffix}"
            suffix += 1

            if suffix > 100:
                raise DictionaryServiceError("admin_dict_skill_create_duplicate_code")

        row = await self.repository.create_skill_for_admin(
            code=code,
            title=cleaned_title,
            language=language,
        )

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_skill_created",
            target_type="skill",
            target_id=row.skill_id,
            before_state={},
            after_state={
                "code": row.code,
                "name": row.name,
                "name_ru": row.name_ru,
                "name_en": row.name_en,
                "name_pt": row.name_pt,
                "is_active": row.is_active,
            },
            reason="Skill created from Super Admin dictionaries",
        )

        return self._skill_card(row, language)
    
    async def rename_skill(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        skill_id: str,
        title: str,
        language: str = "ru",
    ) -> AdminSkillDictionaryCard:
        cleaned_title = " ".join((title or "").split())

        if len(cleaned_title) < 2:
            raise DictionaryServiceError("admin_dict_skill_rename_empty")

        try:
            parsed_skill_id = UUID(str(skill_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_skill_for_admin(parsed_skill_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        if await self.repository.skill_title_exists(title=cleaned_title):
            raise DictionaryServiceError("admin_dict_skill_rename_duplicate")

        after = await self.repository.rename_skill_for_admin(
            skill_id=parsed_skill_id,
            language=language,
            title=cleaned_title,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_skill_renamed",
            target_type="skill",
            target_id=parsed_skill_id,
            before_state={
                "name": before.name,
                "name_ru": before.name_ru,
                "name_en": before.name_en,
                "name_pt": before.name_pt,
                "code": before.code,
            },
            after_state={
                "name": after.name,
                "name_ru": after.name_ru,
                "name_en": after.name_en,
                "name_pt": after.name_pt,
                "code": after.code,
            },
            reason="Skill renamed from Super Admin dictionaries",
        )

        return self._skill_card(after, language)
    
    async def toggle_skill_visibility(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        skill_id: str,
        language: str = "ru",
    ) -> AdminSkillDictionaryCard:
        try:
            parsed_skill_id = UUID(str(skill_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        before = await self.repository.get_skill_for_admin(parsed_skill_id)

        if not before:
            raise DictionaryServiceError("admin_item_not_found")

        new_is_active = not before.is_active

        after = await self.repository.set_skill_visibility_for_admin(
            skill_id=parsed_skill_id,
            is_active=new_is_active,
        )

        if not after:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_skill_visibility_changed",
            target_type="skill",
            target_id=parsed_skill_id,
            before_state={
                "is_active": before.is_active,
            },
            after_state={
                "is_active": after.is_active,
            },
            reason="Skill visibility changed from Super Admin dictionaries",
        )

        return self._skill_card(after, language)

    async def preview_skill_merge(
        self,
        *,
        source_skill_id: str,
        target_skill_value: str,
        language: str,
    ) -> AdminSkillMergePreviewCard:
        cleaned_target_value = " ".join((target_skill_value or "").split())

        if len(cleaned_target_value) < 2:
            raise DictionaryServiceError("admin_dict_skill_merge_empty")

        try:
            parsed_source_skill_id = UUID(str(source_skill_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        source = await self.repository.get_skill_for_admin(parsed_source_skill_id)

        if not source:
            raise DictionaryServiceError("admin_item_not_found")

        target = await self.repository.get_skill_by_code_or_title_for_admin(
            cleaned_target_value
        )

        if not target:
            raise DictionaryServiceError("admin_dict_skill_merge_target_not_found")

        if target.skill_id == source.skill_id:
            raise DictionaryServiceError("admin_dict_skill_merge_same_skill")

        return AdminSkillMergePreviewCard(
            source_skill=self._skill_card(source, language),
            target_skill=self._skill_card(target, language),
        )

    async def merge_skills(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        source_skill_id: str,
        target_skill_value: str,
        language: str,
    ) -> AdminSkillMergeCard:
        cleaned_target_value = " ".join((target_skill_value or "").split())

        if len(cleaned_target_value) < 2:
            raise DictionaryServiceError("admin_dict_skill_merge_empty")

        try:
            parsed_source_skill_id = UUID(str(source_skill_id))
        except (TypeError, ValueError):
            raise DictionaryServiceError("admin_item_not_found")

        source = await self.repository.get_skill_for_admin(parsed_source_skill_id)

        if not source:
            raise DictionaryServiceError("admin_item_not_found")

        target = await self.repository.get_skill_by_code_or_title_for_admin(
            cleaned_target_value
        )

        if not target:
            raise DictionaryServiceError("admin_dict_skill_merge_target_not_found")

        if target.skill_id == source.skill_id:
            raise DictionaryServiceError("admin_dict_skill_merge_same_skill")

        merge_result = await self.repository.merge_skill_links_for_admin(
            source_skill_id=source.skill_id,
            target_skill_id=target.skill_id,
        )

        updated_source = await self.repository.get_skill_for_admin(source.skill_id)
        updated_target = await self.repository.get_skill_for_admin(target.skill_id)

        if not updated_target:
            raise DictionaryServiceError("admin_item_not_found")

        await self.repository.log_dictionary_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action_type="dictionary_skill_merged",
            target_type="skill",
            target_id=target.skill_id,
            before_state={
                "source_skill_id": str(source.skill_id),
                "source_code": source.code,
                "source_name": source.name,
                "source_is_active": source.is_active,
                "target_skill_id": str(target.skill_id),
                "target_code": target.code,
                "target_name": target.name,
                "target_profession_links_count": target.profession_links_count,
                "target_user_links_count": target.user_links_count,
            },
            after_state={
                "source_skill_id": str(source.skill_id),
                "source_is_active": (
                    updated_source.is_active if updated_source else None
                ),
                "target_skill_id": str(updated_target.skill_id),
                "target_profession_links_count": (
                    updated_target.profession_links_count
                ),
                "target_user_links_count": updated_target.user_links_count,
                "moved_profession_links": (
                    merge_result.moved_profession_links
                ),
                "removed_duplicate_profession_links": (
                    merge_result.removed_duplicate_profession_links
                ),
                "moved_user_links": merge_result.moved_user_links,
                "removed_duplicate_user_links": (
                    merge_result.removed_duplicate_user_links
                ),
            },
            reason="Skill duplicate merged from Super Admin dictionaries",
        )

        return AdminSkillMergeCard(
            target_skill=self._skill_card(updated_target, language),
            moved_profession_links=merge_result.moved_profession_links,
            removed_duplicate_profession_links=(
                merge_result.removed_duplicate_profession_links
            ),
            moved_user_links=merge_result.moved_user_links,
            removed_duplicate_user_links=(
                merge_result.removed_duplicate_user_links
            ),
        )
    
