from dataclasses import dataclass
from uuid import UUID

from database.repositories.legal import LegalRepository
from database.repositories.event import EventRepository
from database.repositories.geo_repository import (
    GeoRepository,
)
from database.repositories.rate_limit import RateLimitRepository
from database.repositories.specialist import SpecialistRepository
from services.legal import LegalService, MissingLegalDocumentError
from services.rate_limit import RateLimitError, RateLimitService
from services.geo_service import (
    GeoService,
    SavedGeoPlace,
)
from services.geo_provider import GeoPlaceCandidate
from services.user import UserService

MAX_SPECIALIST_CATEGORIES = 3
MAX_PROFESSIONS_PER_CATEGORY = 3

class SpecialistRegistrationError(Exception):
    pass

@dataclass(frozen=True)
class SpecialistTextSearchQuery:
    original_query: str
    profession_query: str
    city_id: UUID | None = None
    city_name: str | None = None
    country_id: UUID | None = None
    country_name: str | None = None

@dataclass(frozen=True)
class SearchCategorySelectionResult:
    category_id: UUID
    category_name: str

@dataclass(frozen=True)
class SearchCategoryOption:
    id: UUID
    name: str

@dataclass(frozen=True)
class SearchProfessionSelectionResult:
    profession_id: UUID
    profession_name: str
    category_id: UUID

@dataclass(frozen=True)
class SearchProfessionOption:
    id: UUID
    category_id: UUID
    name: str

@dataclass(frozen=True)
class SpecialistTextSearchResult:
    parsed_query: SpecialistTextSearchQuery
    professions: tuple[SearchProfessionOption, ...]


@dataclass
class SpecialistRegistrationData:
    tenant_id: UUID
    user_id: UUID
    category_id: UUID
    profession_id: UUID
    country_id: UUID | None
    city_id: UUID | None
    display_name: str
    short_description: str
    profession_selections: list[dict] | None = None
    full_description: str | None = None
    price_from: float | None = None
    price_to: float | None = None
    currency: str = "EUR"
    price_unit: str = "service"
    latitude: float | None = None
    longitude: float | None = None
    service_radius_km: int = 0
    languages: list[str] | None = None
    service_title: str | None = None
    service_description: str | None = None
    contact_text: str | None = None
    contact_visibility: str = "platform_only"
    allow_requests: bool = True
    language: str = "ru"
    work_format: str = "mixed"

@dataclass
class SpecialistProfileUpdateData:
    tenant_id: UUID
    user_id: UUID
    specialist_id: UUID
    display_name: str | None = None
    short_description: str | None = None
    contact_text: str | None = None
    category_id: UUID | None = None
    profession_id: UUID | None = None
    country_id: UUID | None = None
    city_id: UUID | None = None
    latitude: float | None = None
    longitude: float | None = None
    service_radius_km: int | None = None
    clear_city: bool = False
    clear_coordinates: bool = False

@dataclass(frozen=True)
class SpecialistProfileUpdateResult:
    specialist_id: UUID
    changed: bool

@dataclass(frozen=True)
class SpecialistReadOnlyPublicProfile:
    display_name: str
    professions: tuple[str, ...]
    location: str
    short_description: str | None
    status: str
    is_available: bool
    work_format: str | None



@dataclass
class SpecialistServiceItemData:
    tenant_id: UUID
    user_id: UUID
    specialist_id: UUID
    title: str
    description: str
    price_from: float | None = None
    price_to: float | None = None
    currency: str = "EUR"
    category_id: UUID | None = None
    profession_id: UUID | None = None
    service_id: UUID | None = None

@dataclass(frozen=True)
class SpecialistServiceEditData:
    service_id: UUID
    category_id: UUID | None
    profession_id: UUID | None
    title: str
    description: str
    price_from: float | None
    price_to: float | None
    currency: str

@dataclass(frozen=True)
class SpecialistSkillOption:
    id: UUID
    name: str


@dataclass(frozen=True)
class SpecialistSkillsEditData:
    skills: tuple[
        SpecialistSkillOption,
        ...,
    ]
    selected_ids: tuple[UUID, ...]

class SpecialistSearchTextService:
    def __init__(self, repository: SpecialistRepository):
        self.repository = repository

    async def parse_text_query(
        self,
        query: str,
        *,
        language: str = "ru",
    ) -> SpecialistTextSearchQuery:
        original_query = (query or "").strip()
        profession_query = original_query

        city = await self.repository.find_active_city_in_text(original_query)
        if city:
            profession_query = self._strip_location_tail(profession_query)

            city_names = [
                city.name,
                city.name_ru,
                city.name_en,
                city.name_pt,
            ]
            for city_name in city_names:
                if city_name:
                    profession_query = self._remove_city_from_query(
                        profession_query,
                        city_name,
                    )

            profession_query = self._cleanup_profession_query(profession_query)

            return SpecialistTextSearchQuery(
                original_query=original_query,
                profession_query=profession_query or original_query,
                city_id=city.id,
                city_name=_localized_model_name(city, language),
                country_id=city.country_id,
                country_name=None,
            )

        return SpecialistTextSearchQuery(
            original_query=original_query,
            profession_query=profession_query,
        )

    async def search(
        self,
        query: str,
        *,
        language: str = "ru",
        limit: int = 10,
    ) -> SpecialistTextSearchResult:
        parsed_query = await self.parse_text_query(
            query,
            language=language,
        )
        professions = await self.repository.search_professions_by_text(
            parsed_query.profession_query,
            limit=limit,
        )

        return SpecialistTextSearchResult(
            parsed_query=parsed_query,
            professions=tuple(
                SearchProfessionOption(
                    id=profession.id,
                    category_id=profession.category_id,
                    name=_localized_model_name(
                        profession,
                        language,
                    ),
                )
                for profession in professions
            ),
        )

    def _strip_location_tail(self, query: str) -> str:
        normalized = (query or "").strip()
        lowered = normalized.lower()

        for marker in (" в ", " во ", " у ", " in ", " em "):
            marker_index = lowered.rfind(marker)
            if marker_index > 0:
                return normalized[:marker_index].strip()

        return normalized

    def _remove_city_from_query(self, query: str, city_name: str) -> str:
        normalized = query

        for prefix in (
            " в ",
            " во ",
            " у ",
            " in ",
            " em ",
        ):
            normalized = normalized.replace(
                f"{prefix}{city_name}",
                " ",
            )
            normalized = normalized.replace(
                f"{prefix}{city_name.lower()}",
                " ",
            )

        normalized = normalized.replace(city_name, " ")
        normalized = normalized.replace(city_name.lower(), " ")
        return normalized

    def _cleanup_profession_query(self, query: str) -> str:
        words = [
            word.strip(" ,.;:!?")
            for word in (query or "").split()
        ]
        words = [
            word
            for word in words
            if word.lower() not in {"в", "во", "у", "in", "em"}
        ]
        return " ".join(words).strip()

class SpecialistSearchSelectionService:
    def __init__(
        self,
        repository: SpecialistRepository,
    ):
        self.repository = repository
        self.events = EventRepository(repository.session)

    async def list_active_categories(
        self,
        *,
        language: str,
        limit: int = 100,
    ) -> list[SearchCategoryOption]:
        categories = await (
            self.repository.list_active_categories(
                limit=limit
            )
        )

        return [
            SearchCategoryOption(
                id=category.id,
                name=_localized_model_name(
                    category,
                    language,
                ),
            )
            for category in categories
        ]

    async def select_category(
        self,
        *,
        category_id: UUID,
        language: str,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> SearchCategorySelectionResult | None:
        category = await self.repository.get_active_category(
            category_id
        )

        if not category:
            return None

        category_name = _localized_model_name(
            category,
            language,
        )

        if tenant_id and user_id:
            try:
                await self.events.create_event(
                    event_type="category_selected",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    entity_type="specialist_category",
                    entity_id=category.id,
                    payload={
                        "category_name": category_name,
                    },
                    platform="telegram",
                )
                await self.repository.session.commit()
            except Exception:
                await self.repository.session.rollback()
                raise

        return SearchCategorySelectionResult(
            category_id=category.id,
            category_name=category_name,
        )

    async def list_profession_options(
        self,
        *,
        category_id: UUID | None,
        language: str,
        limit: int = 100,
    ) -> list[SearchProfessionOption]:
        if category_id is not None:
            professions = await (
                self.repository
                .list_active_professions_by_category(
                    category_id,
                    limit=limit,
                )
            )
        else:
            professions = await (
                self.repository
                .list_active_professions(
                    limit=limit
                )
            )

        return [
            SearchProfessionOption(
                id=profession.id,
                category_id=(
                    profession.category_id
                ),
                name=_localized_model_name(
                    profession,
                    language,
                ),
            )
            for profession in professions
        ]

    async def select_profession(
        self,
        *,
        profession_id: UUID,
        category_id: UUID | None,
        language: str,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> SearchProfessionSelectionResult | None:
        profession = (
            await self.repository.get_active_profession(
                profession_id
            )
        )

        if not profession:
            return None

        if (
            category_id is not None
            and profession.category_id != category_id
        ):
            return None

        profession_name = _localized_model_name(
            profession,
            language,
        )

        if tenant_id and user_id:
            try:
                await self.events.create_event(
                    event_type="profession_selected",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    entity_type="profession",
                    entity_id=profession.id,
                    payload={
                        "profession_name": profession_name,
                        "category_id": str(
                            profession.category_id
                        ),
                    },
                    platform="telegram",
                )
                await self.repository.session.commit()
            except Exception:
                await self.repository.session.rollback()
                raise

        return SearchProfessionSelectionResult(
            profession_id=profession.id,
            profession_name=profession_name,
            category_id=profession.category_id,
        )

def _localized_model_name(item, language: str) -> str:
    if not item:
        return "-"

    return (
        getattr(item, f"name_{language}", None)
        or getattr(item, "name", None)
        or "-"
    )

@dataclass(frozen=True)
class SpecialistCabinetContext:
    user_found: bool
    specialist_found: bool
    profession_names: tuple[str, ...]
    status: str | None
    unread_count: int
    show_role_switch: bool
    show_moderation: bool

class SpecialistService:
    def __init__(
        self,
        repository: SpecialistRepository,
        rate_limit_service: RateLimitService | None = None,
    ):
        self.repository = repository
        if rate_limit_service is not None:
            self.rate_limit_service = rate_limit_service
        elif hasattr(repository, "session"):
            self.rate_limit_service = RateLimitService(
                RateLimitRepository(repository.session)
            )
        else:
            self.rate_limit_service = None

    async def record_cabinet_opened(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        status: str,
        unread_count: int,
    ) -> None:
        try:
            await EventRepository(
                self.repository.session
            ).create_event(
                event_type="specialist_menu",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="specialist",
                entity_id=specialist_id,
                payload={
                    "status": status,
                    "unread_count": unread_count,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

    async def open_specialist_cabinet(
        self,
        *,
        telegram_id: int | str,
        language: str,
    ) -> SpecialistCabinetContext:
        user_service = UserService(
            self.repository.session
        )

        user_context = await (
            user_service
            .get_specialist_context_by_telegram_id(
                telegram_id
            )
        )

        if not user_context:
            return SpecialistCabinetContext(
                user_found=False,
                specialist_found=False,
                profession_names=(),
                status=None,
                unread_count=0,
                show_role_switch=False,
                show_moderation=False,
            )

        specialist = user_context.specialist

        if not specialist:
            return SpecialistCabinetContext(
                user_found=True,
                specialist_found=False,
                profession_names=(),
                status=None,
                unread_count=0,
                show_role_switch=False,
                show_moderation=False,
            )

        role_context = (
            await user_service
            .get_role_switch_context(
                telegram_id,
                language,
            )
        )

        available_roles = (
            role_context.available_roles
            if role_context
            else []
        )

        unread_count = int(
            (
                role_context.unread_counts
                if role_context
                else {}
            ).get(
                "specialist",
                0,
            )
        )

        profession_names = tuple(
            await self.list_profile_profession_names(
                specialist_id=specialist.id,
                language=language,
            )
        )

        await self.record_cabinet_opened(
            tenant_id=user_context.tenant_id,
            user_id=user_context.user.id,
            specialist_id=specialist.id,
            status=specialist.status,
            unread_count=unread_count,
        )

        return SpecialistCabinetContext(
            user_found=True,
            specialist_found=True,
            profession_names=profession_names,
            status=specialist.status,
            unread_count=unread_count,
            show_role_switch=(
                len(available_roles) > 1
            ),
            show_moderation=(
                specialist.status != "approved"
            ),
        )

    async def list_service_items_page_for_viewer(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        page: int,
        page_size: int,
    ) -> tuple[int, list]:
        specialist = await self.repository.get_by_user_id(
            user_id
        )

        if (
            not specialist
            or specialist.id != specialist_id
        ):
            raise SpecialistRegistrationError(
                "Specialist profile not found."
            )

        normalized_page = max(0, page)
        normalized_page_size = max(1, page_size)

        total, services = (
            await self.repository
            .list_specialist_services_page(
                specialist_id=specialist_id,
                limit=normalized_page_size,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )

        try:
            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="service_list",
                entity_type="specialist",
                entity_id=specialist_id,
                payload={
                    "page": normalized_page,
                    "count": len(services),
                    "total": total,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return total, services

    async def _require_specialist_consents(self, data: SpecialistRegistrationData) -> None:
        legal_service = LegalService(LegalRepository(self.repository.session))

        try:
            has_consents = await legal_service.has_required_specialist_consents(
                tenant_id=data.tenant_id,
                user_id=data.user_id,
                language=data.language or "ru",
            )
        except MissingLegalDocumentError as exc:
            raise SpecialistRegistrationError(
                f"Legal documents are not configured: {exc}"
            ) from exc

        if not has_consents:
            raise SpecialistRegistrationError("Legal consents are required.")

    async def list_profile_profession_names(
        self,
        *,
        specialist_id: UUID,
        language: str = "ru",
    ) -> list[str]:
        rows = await self.repository.list_active_specialist_professions(
            specialist_id=specialist_id,
        )

        return [
            _localized_model_name(profession, language)
            for _, _, profession in rows
        ]
    async def get_read_only_public_profile(
        self,
        *,
        user_id: UUID,
        language: str,
    ) -> SpecialistReadOnlyPublicProfile | None:
        specialist = await self.repository.get_by_user_id(user_id)

        if not specialist:
            return None

        professions = await self.list_profile_profession_names(
            specialist_id=specialist.id,
            language=language,
        )
        city, country = await self.repository.get_specialist_location_parts(
            specialist=specialist,
        )

        location_parts = [
            _localized_model_name(item, language)
            for item in (city, country)
            if item
        ]

        return SpecialistReadOnlyPublicProfile(
            display_name=specialist.display_name or "-",
            professions=tuple(professions),
            location=", ".join(location_parts) or "-",
            short_description=specialist.short_description,
            status=specialist.status,
            is_available=bool(specialist.is_available),
            work_format=specialist.work_format,
        )
    
    async def get_profile_profession_selections(
        self,
        *,
        specialist_id: UUID,
        language: str = "ru",
    ) -> list[dict[str, str]]:
        rows = await self.repository.list_active_specialist_professions(
            specialist_id=specialist_id,
        )

        return [
            {
                "category_id": str(category.id),
                "category_name": _localized_model_name(
                    category,
                    language,
                ),
                "profession_id": str(profession.id),
                "profession_name": _localized_model_name(
                    profession,
                    language,
                ),
            }
            for _, category, profession in rows
        ]

    async def list_active_categories_for_profile_editor(
        self,
        *,
        limit: int = 50,
    ):
        return await self.repository.list_active_categories(
            limit=limit,
        )

    async def replace_profile_professions(
        self,
        *,
        specialist_id: UUID | str,
        user_id: UUID | str,
        profession_selections: list[dict],
    ):
        return await self.repository.replace_specialist_professions(
            specialist_id=UUID(str(specialist_id)),
            user_id=UUID(str(user_id)),
            profession_selections=profession_selections,
        )

    async def create_pending_profile(self, data: SpecialistRegistrationData):
        await self._require_specialist_consents(data)

        existing = await self.repository.get_by_user_id(data.user_id)
        if existing:
            raise SpecialistRegistrationError("Specialist profile already exists for this user.")

        category = await self.repository.get_active_category(data.category_id)
        if not category:
            raise SpecialistRegistrationError("Category not found or inactive.")

        profession = await self.repository.get_active_profession(data.profession_id)
        if not profession:
            raise SpecialistRegistrationError("Profession not found or inactive.")

        if profession.category_id != data.category_id:
            raise SpecialistRegistrationError("Profession does not belong to selected category.")

        if data.country_id:
            country = await self.repository.get_active_country(data.country_id)
            if not country:
                raise SpecialistRegistrationError("Country not found or inactive.")

        if data.city_id:
            city = await self.repository.get_active_city(data.city_id)
            if not city:
                raise SpecialistRegistrationError("City not found or inactive.")

            if data.country_id and city.country_id != data.country_id:
                raise SpecialistRegistrationError("City does not belong to selected country.")

        display_name = data.display_name.strip()
        short_description = data.short_description.strip()
        full_description = data.full_description.strip() if data.full_description else None
        service_title = data.service_title.strip() if data.service_title else None
        service_description = data.service_description.strip() if data.service_description else None
        contact_text = data.contact_text.strip() if data.contact_text else None
        if service_title and len(service_title) < 3:
            raise SpecialistRegistrationError("Service title is too short.")

        if len(display_name) < 2:
            raise SpecialistRegistrationError("Display name is too short.")

        if len(short_description) < 20:
            raise SpecialistRegistrationError("Short description must be at least 20 characters.")

        if data.price_from is not None and data.price_from < 0:
            raise SpecialistRegistrationError("Price from cannot be negative.")

        if data.price_to is not None and data.price_to < 0:
            raise SpecialistRegistrationError("Price to cannot be negative.")

        if (
            data.price_from is not None
            and data.price_to is not None
            and data.price_to < data.price_from
        ):
            raise SpecialistRegistrationError("Price to cannot be lower than price from.")

        if not contact_text:
            raise SpecialistRegistrationError("Contact is required.")

        languages = data.languages or ["ru"]
        languages = [item.strip().lower()[:10] for item in languages if item and item.strip()]
        if not languages:
            languages = ["ru"]

        return await self.repository.create_specialist_profile(
            tenant_id=data.tenant_id,
            user_id=data.user_id,
            category_id=data.category_id,
            profession_id=data.profession_id,
            profession_selections=data.profession_selections,
            country_id=data.country_id,
            city_id=data.city_id,
            display_name=display_name,
            short_description=short_description,
            full_description=full_description,
            price_from=data.price_from,
            price_to=data.price_to,
            currency=(data.currency or "EUR")[:3].upper(),
            price_unit=data.price_unit or "service",
            latitude=data.latitude,
            longitude=data.longitude,
            service_radius_km=data.service_radius_km,
            languages=languages,
            service_title=service_title,
            service_description=service_description,
            contact_text=contact_text,
            contact_visibility=data.contact_visibility or "platform_only",
            allow_requests=data.allow_requests,
            work_format=data.work_format or "mixed",
        )
    
    async def update_profile(self, data: SpecialistProfileUpdateData):
        existing = await self.repository.get_by_user_id(data.user_id)
        if not existing or existing.id != data.specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        if self.rate_limit_service is not None:
            try:
                await self.rate_limit_service.ensure_profile_edit_allowed(
                    tenant_id=data.tenant_id,
                    user_id=data.user_id,
                )
            except RateLimitError as exc:
                raise SpecialistRegistrationError(str(exc)) from exc

        display_name = data.display_name.strip() if data.display_name is not None else None
        short_description = (
            data.short_description.strip()
            if data.short_description is not None
            else None
        )
        contact_text = data.contact_text.strip() if data.contact_text is not None else None

        if display_name is not None and len(display_name) < 2:
            raise SpecialistRegistrationError("Display name is too short.")

        if short_description is not None and len(short_description) < 20:
            raise SpecialistRegistrationError("Short description must be at least 20 characters.")

        if contact_text is not None and len(contact_text) < 3:
            raise SpecialistRegistrationError("Contact is too short.")

        if data.category_id is not None:
            category = await self.repository.get_active_category(data.category_id)
            if not category:
                raise SpecialistRegistrationError("Category not found or inactive.")

        if data.profession_id is not None:
            profession = await self.repository.get_active_profession(data.profession_id)
            if not profession:
                raise SpecialistRegistrationError("Profession not found or inactive.")

            category_id = data.category_id or existing.category_id
            if profession.category_id != category_id:
                raise SpecialistRegistrationError("Profession does not belong to selected category.")

        if data.country_id:
            country = await self.repository.get_active_country(data.country_id)
            if not country:
                raise SpecialistRegistrationError("Country not found or inactive.")

        if data.city_id:
            city = await self.repository.get_active_city(data.city_id)
            if not city:
                raise SpecialistRegistrationError("City not found or inactive.")

            country_id = data.country_id or existing.country_id
            if country_id and city.country_id != country_id:
                raise SpecialistRegistrationError("City does not belong to selected country.")

        return await self.repository.update_specialist_profile_fields(
            specialist_id=data.specialist_id,
            user_id=data.user_id,
            display_name=display_name,
            short_description=short_description,
            contact_text=contact_text,
            category_id=data.category_id,
            profession_id=data.profession_id,
            country_id=data.country_id,
            city_id=data.city_id,
            latitude=data.latitude,
            longitude=data.longitude,
            service_radius_km=data.service_radius_km,
            clear_city=data.clear_city,
            clear_coordinates=data.clear_coordinates,
        )
    
    async def update_profile_with_audit(
        self,
        data: SpecialistProfileUpdateData,
    ) -> SpecialistProfileUpdateResult:
        existing = await self.repository.get_by_user_id(
            data.user_id
        )

        if (
            not existing
            or existing.id != data.specialist_id
        ):
            raise SpecialistRegistrationError(
                "Specialist profile not found."
            )

        if data.display_name is not None:
            field_name = "display_name"
            before_value = existing.display_name
            after_value = data.display_name.strip()

        elif data.short_description is not None:
            field_name = "short_description"
            before_value = (
                existing.short_description
            )
            after_value = (
                data.short_description.strip()
            )

        elif data.contact_text is not None:
            field_name = "contact_text"
            before_value = (
                existing.extra_metadata or {}
            ).get("contact_text")
            after_value = data.contact_text.strip()

        else:
            raise SpecialistRegistrationError(
                "No supported profile change provided."
            )

        if before_value == after_value:
            return SpecialistProfileUpdateResult(
                specialist_id=existing.id,
                changed=False,
            )

        try:
            updated_specialist = (
                await self.update_profile(data)
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=data.tenant_id,
                user_id=data.user_id,
                event_type="change_submitted",
                entity_type="specialist",
                entity_id=data.specialist_id,
                payload={
                    "field": field_name,
                    "before": before_value,
                    "after": after_value,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return SpecialistProfileUpdateResult(
            specialist_id=updated_specialist.id,
            changed=True,
        )

    async def update_location_from_candidate(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        candidate: dict,
        service_radius_km: int = 25,
    ) -> SavedGeoPlace:
        try:
            if self.rate_limit_service is not None:
                await (
                    self.rate_limit_service
                    .ensure_geo_change_allowed(
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                )

            place = await GeoService(
                GeoRepository(
                    self.repository.session
                )
            ).confirm_place(
                candidate,
                commit=False,
            )

            updated_specialist = (
                await self.update_profile(
                    SpecialistProfileUpdateData(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        specialist_id=specialist_id,
                        country_id=place.country_id,
                        city_id=place.city_id,
                        latitude=place.latitude,
                        longitude=place.longitude,
                        service_radius_km=(
                            service_radius_km
                        ),
                    )
                )
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                event_type="geo_change",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="city",
                entity_id=place.city_id,
                payload={
                    "source": (
                        "specialist_profile_edit"
                    ),
                    "specialist_id": str(
                        updated_specialist.id
                    ),
                    "country_id": str(
                        place.country_id
                    ),
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return place

    async def update_country_from_candidate(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        candidate: dict,
    ) -> None:
        try:
            try:
                place_candidate = (
                    GeoPlaceCandidate.from_state(
                        candidate
                    )
                )
            except (
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                raise SpecialistRegistrationError(
                    "Invalid country data."
                ) from exc

            if (
                not place_candidate.country_code
                or len(
                    place_candidate.country_code
                ) != 2
            ):
                raise SpecialistRegistrationError(
                    "Country data is required."
                )

            if self.rate_limit_service is not None:
                await (
                    self.rate_limit_service
                    .ensure_geo_change_allowed(
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                )

            country = await GeoRepository(
                self.repository.session
            ).ensure_country(
                place_candidate
            )

            updated_specialist = (
                await self.update_profile(
                    SpecialistProfileUpdateData(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        specialist_id=specialist_id,
                        country_id=country.id,
                        city_id=None,
                        latitude=None,
                        longitude=None,
                        service_radius_km=0,
                        clear_city=True,
                        clear_coordinates=True,
                    )
                )
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                event_type="geo_change",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="country",
                entity_id=country.id,
                payload={
                    "source": (
                        "specialist_profile_edit"
                    ),
                    "specialist_id": str(
                        updated_specialist.id
                    ),
                    "country_id": str(
                        country.id
                    ),
                    "whole_country": True,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

    async def get_service_item_for_editing(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        service_id: UUID,
    ) -> SpecialistServiceEditData:
        specialist = (
            await self.repository.get_by_user_id(
                user_id
            )
        )

        if (
            not specialist
            or specialist.id != specialist_id
        ):
            raise SpecialistRegistrationError(
                "Specialist profile not found."
            )

        service = await (
            self.repository
            .get_owned_service_item(
                specialist_id=specialist_id,
                user_id=user_id,
                service_id=service_id,
            )
        )

        if not service:
            raise SpecialistRegistrationError(
                "Service not found."
            )

        return SpecialistServiceEditData(
            service_id=service.id,
            category_id=service.category_id,
            profession_id=service.profession_id,
            title=service.title,
            description=(
                service.description or ""
            ),
            price_from=(
                float(service.price_from)
                if service.price_from is not None
                else None
            ),
            price_to=(
                float(service.price_to)
                if service.price_to is not None
                else None
            ),
            currency=service.currency or "EUR",
        )

    async def save_service_item(
        self,
        data: SpecialistServiceItemData,
    ):
        specialist = await self.repository.get_by_user_id(data.user_id)
        if not specialist or specialist.id != data.specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        title = data.title.strip()
        description = data.description.strip()
        currency = (data.currency or "EUR")[:3].upper()

        if len(title) < 3:
            raise SpecialistRegistrationError("Service title is too short.")

        if len(description) < 3:
            raise SpecialistRegistrationError("Service description is too short.")

        if data.price_from is not None and data.price_from < 0:
            raise SpecialistRegistrationError("Price from cannot be negative.")

        if data.price_to is not None and data.price_to < 0:
            raise SpecialistRegistrationError("Price to cannot be negative.")

        if (
            data.price_from is not None
            and data.price_to is not None
            and data.price_to < data.price_from
        ):
            raise SpecialistRegistrationError("Price to cannot be lower than price from.")

        if data.category_id is not None:
            category = await self.repository.get_active_category(data.category_id)
            if not category:
                raise SpecialistRegistrationError("Category not found or inactive.")

        if data.profession_id is not None:
            profession = await self.repository.get_active_profession(data.profession_id)
            if not profession:
                raise SpecialistRegistrationError("Profession not found or inactive.")

            category_id = data.category_id or specialist.category_id
            if profession.category_id != category_id:
                raise SpecialistRegistrationError("Profession does not belong to selected category.")

        try:
            if data.service_id:
                existing_service = (
                    await self.repository
                    .get_owned_service_item(
                        specialist_id=(
                            data.specialist_id
                        ),
                        user_id=data.user_id,
                        service_id=data.service_id,
                    )
                )

                if not existing_service:
                    raise SpecialistRegistrationError(
                        "Service not found."
                    )

                before_payload = {
                    "title": existing_service.title,
                    "description": (
                        existing_service.description
                    ),
                    "price_from": (
                        float(
                            existing_service.price_from
                        )
                        if existing_service.price_from
                        is not None
                        else None
                    ),
                    "price_to": (
                        float(
                            existing_service.price_to
                        )
                        if existing_service.price_to
                        is not None
                        else None
                    ),
                    "currency": (
                        existing_service.currency
                    ),
                    "status": existing_service.status,
                }

                service = await self.repository.update_specialist_service_item(
                    specialist_id=data.specialist_id,
                    user_id=data.user_id,
                    service_id=data.service_id,
                    title=title,
                    description=description,
                    price_from=data.price_from,
                    price_to=data.price_to,
                    currency=currency,
                    category_id=data.category_id,
                    profession_id=data.profession_id,
                )
                mode = "edit"

                event_payload = {
                    "mode": mode,
                    "before": before_payload,
                    "after": {
                        "title": service.title,
                        "description": (
                            service.description
                        ),
                        "price_from": (
                            float(service.price_from)
                            if service.price_from
                            is not None
                            else None
                        ),
                        "price_to": (
                            float(service.price_to)
                            if service.price_to
                            is not None
                            else None
                        ),
                        "currency": service.currency,
                        "status": service.status,
                    },
                }

            else:
                service = await self.repository.create_specialist_service_item(
                    tenant_id=data.tenant_id,
                    specialist_id=data.specialist_id,
                    category_id=data.category_id,
                    profession_id=data.profession_id,
                    title=title,
                    description=description,
                    price_from=data.price_from,
                    price_to=data.price_to,
                    currency=currency,
                )
                mode = "create"

                event_payload = {
                    "mode": mode,
                    "title": service.title,
                    "price_from": (
                        float(service.price_from)
                        if service.price_from
                        is not None
                        else None
                    ),
                    "price_to": (
                        float(service.price_to)
                        if service.price_to
                        is not None
                        else None
                    ),
                    "currency": service.currency,
                    "status": service.status,
                }

            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=data.tenant_id,
                user_id=data.user_id,
                event_type="service_saved",
                entity_type="specialist_service",
                entity_id=service.id,
                payload=event_payload,
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return service, mode

    async def toggle_service_item_status(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        service_id: UUID,
    ):
        specialist = await self.repository.get_by_user_id(
            user_id
        )

        if (
            not specialist
            or specialist.id != specialist_id
        ):
            raise SpecialistRegistrationError(
                "Specialist profile not found."
            )

        service = (
            await self.repository.get_owned_service_item(
                specialist_id=specialist_id,
                user_id=user_id,
                service_id=service_id,
            )
        )

        if not service:
            raise SpecialistRegistrationError(
                "Service not found."
            )

        before_status = service.status or "active"
        after_status = (
            "paused"
            if before_status == "active"
            else "active"
        )

        try:
            service = (
                await self.repository
                .set_specialist_service_item_status(
                    specialist_id=specialist_id,
                    user_id=user_id,
                    service_id=service_id,
                    status=after_status,
                )
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="service_status_changed",
                entity_type="specialist_service",
                entity_id=service.id,
                payload={
                    "before": before_status,
                    "after": after_status,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return service, before_status, after_status
    
    async def delete_service_item(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        service_id: UUID,
    ):
        specialist = await self.repository.get_by_user_id(
            user_id
        )

        if (
            not specialist
            or specialist.id != specialist_id
        ):
            raise SpecialistRegistrationError(
                "Specialist profile not found."
            )

        service = (
            await self.repository.get_owned_service_item(
                specialist_id=specialist_id,
                user_id=user_id,
                service_id=service_id,
            )
        )

        if not service:
            raise SpecialistRegistrationError(
                "Service not found."
            )

        before_status = service.status or "active"

        try:
            service = (
                await self.repository
                .set_specialist_service_item_status(
                    specialist_id=specialist_id,
                    user_id=user_id,
                    service_id=service_id,
                    status="deleted",
                )
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="service_deleted",
                entity_type="specialist_service",
                entity_id=service.id,
                payload={
                    "before": before_status,
                    "after": "deleted",
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return service, before_status
    
    async def update_availability(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        availability_status: str,
        available_from_text: str | None = None,
    ) -> None:
        if availability_status not in {
            "available_now",
            "partly_busy",
            "available_from",
        }:
            raise SpecialistRegistrationError(
                "Invalid availability status."
            )

        normalized_date = (
            available_from_text or ""
        ).strip() or None

        if (
            availability_status
            == "available_from"
            and not normalized_date
        ):
            raise SpecialistRegistrationError(
                "Availability date is required."
            )

        try:
            specialist = (
                await self.repository.get_by_user_id(
                    user_id
                )
            )

            if (
                not specialist
                or specialist.id != specialist_id
            ):
                raise SpecialistRegistrationError(
                    "Specialist profile not found."
                )

            before_metadata = dict(
                specialist.extra_metadata or {}
            )
            before_status = before_metadata.get(
                "availability_status"
            )
            before_date = before_metadata.get(
                "available_from_text"
            )
            before_is_available = bool(
                specialist.is_available
            )

            updated_specialist = await (
                self.repository
                .update_specialist_availability(
                    user_id=user_id,
                    specialist_id=specialist_id,
                    availability_status=(
                        availability_status
                    ),
                    available_from_text=(
                        normalized_date
                    ),
                )
            )

            after_metadata = dict(
                updated_specialist.extra_metadata
                or {}
            )

            changed = (
                before_status
                != availability_status
                or before_date
                != normalized_date
                or before_is_available
                != bool(
                    updated_specialist.is_available
                )
            )

            if changed:
                await EventRepository(
                    self.repository.session
                ).create_event(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    event_type="change_submitted",
                    entity_type="specialist",
                    entity_id=specialist_id,
                    payload={
                        "field": "availability",
                        "before": before_metadata,
                        "after": after_metadata,
                    },
                    platform="telegram",
                )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise


    async def get_profile_visibility(
        self,
        *,
        user_id: UUID,
    ) -> str | None:
        return await self.repository.get_specialist_profile_visibility(
            user_id=user_id,
        )

    async def update_profile_visibility(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        visibility: str,
    ):
        if visibility not in {
            "platform_only",
            "public_limited",
            "private",
        }:
            raise SpecialistRegistrationError(
                "Invalid visibility."
            )

        try:
            specialist = (
                await self.repository.get_by_user_id(
                    user_id
                )
            )

            if (
                not specialist
                or specialist.id != specialist_id
            ):
                raise SpecialistRegistrationError(
                    "Specialist profile not found."
                )

            (
                updated_specialist,
                before_visibility,
            ) = await (
                self.repository
                .update_specialist_profile_visibility(
                    user_id=user_id,
                    specialist_id=specialist_id,
                    visibility=visibility,
                )
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="profile_action",
                entity_type="specialist",
                entity_id=updated_specialist.id,
                payload={
                    "action": (
                        "visibility_changed"
                    ),
                    "before_visibility": (
                        before_visibility
                    ),
                    "after_visibility": (
                        visibility
                    ),
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return updated_specialist
    
    async def record_blocked_profile_change(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        field: str,
        source: str | None = None,
    ) -> None:
        normalized_field = (
            field or "unknown"
        ).strip()[:100]

        payload = {
            "field": normalized_field,
            "reason": (
                "critical_profile_change_"
                "requires_pending_schema"
            ),
        }

        if source:
            payload["source"] = (
                source.strip()[:100]
            )

        try:
            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="change_blocked",
                entity_type="specialist",
                entity_id=specialist_id,
                payload=payload,
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

    async def update_work_format(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        work_format: str,
    ):
        if work_format not in {"at_client", "at_specialist", "remote", "mixed"}:
            raise SpecialistRegistrationError("Invalid work format.")

        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        before_work_format = specialist.work_format

        if before_work_format == work_format:
            return specialist, before_work_format, work_format, False

        try:
            updated_specialist = (
                await self.repository
                .update_specialist_work_format(
                    user_id=user_id,
                    specialist_id=specialist_id,
                    work_format=work_format,
                )
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="change_submitted",
                entity_type="specialist",
                entity_id=specialist_id,
                payload={
                    "field": "work_format",
                    "before": before_work_format,
                    "after": work_format,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return (
            updated_specialist,
            before_work_format,
            work_format,
            True,
        )

    @staticmethod
    def toggle_language_selection(
        *,
        selected_codes: list[str],
        language_code: str,
    ) -> list[str]:
        allowed_codes = {
            "ru",
            "en",
            "pt",
        }

        normalized_code = (
            language_code or ""
        ).strip().lower()

        if normalized_code not in allowed_codes:
            raise ValueError(
                "Invalid language code."
            )

        selected = list(
            dict.fromkeys(
                item.strip().lower()
                for item in (
                    selected_codes or ["ru"]
                )
                if (
                    item
                    and item.strip().lower()
                    in allowed_codes
                )
            )
        )

        if normalized_code in selected:
            selected = [
                item
                for item in selected
                if item != normalized_code
            ]
        else:
            selected.append(
                normalized_code
            )

        if not selected:
            raise SpecialistRegistrationError(
                "At least one language is required."
            )

        return selected

    async def get_languages_for_editing(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
    ) -> list[str]:
        specialist = (
            await self.repository.get_by_user_id(
                user_id
            )
        )

        if (
            not specialist
            or specialist.id != specialist_id
        ):
            raise SpecialistRegistrationError(
                "Specialist profile not found."
            )

        language_codes = await (
            self.repository
            .list_specialist_language_codes(
                specialist_id=specialist_id,
            )
        )

        return language_codes or ["ru"]

    async def update_languages(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        language_codes: list[str],
    ):
        selected = [
            item.strip().lower()
            for item in language_codes
            if item and item.strip().lower() in {"ru", "en", "pt"}
        ]

        selected = list(dict.fromkeys(selected))

        if not selected:
            raise SpecialistRegistrationError("At least one language is required.")

        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        before_languages = await self.repository.list_specialist_language_codes(
            specialist_id=specialist_id,
        )

        if sorted(before_languages) == sorted(selected):
            return before_languages, selected, False

        try:
            await self.repository.replace_specialist_languages(
                user_id=user_id,
                specialist_id=specialist_id,
                language_codes=selected,
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="change_submitted",
                entity_type="specialist",
                entity_id=specialist_id,
                payload={
                    "field": "languages",
                    "before": before_languages,
                    "after": selected,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return before_languages, selected, True

    async def get_skills_for_editing(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        language: str,
        limit: int = 30,
    ) -> SpecialistSkillsEditData:
        specialist = (
            await self.repository.get_by_user_id(
                user_id
            )
        )

        if (
            not specialist
            or specialist.id != specialist_id
        ):
            raise SpecialistRegistrationError(
                "Specialist profile not found."
            )

        skills = await (
            self.repository
            .list_skills_for_specialist_professions(
                specialist_id=specialist_id,
                language=language,
                limit=limit,
            )
        )

        selected_ids = await (
            self.repository.list_user_skill_ids(
                user_id
            )
        )

        return SpecialistSkillsEditData(
            skills=tuple(
                SpecialistSkillOption(
                    id=skill.id,
                    name=_localized_model_name(
                        skill,
                        language,
                    ),
                )
                for skill in skills
            ),
            selected_ids=tuple(
                selected_ids
            ),
        )

    async def update_skills(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
        skill_ids: list[UUID],
    ):
        selected = list(dict.fromkeys(skill_ids))

        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        allowed_skills = await self.repository.list_skills_for_specialist_professions(
            specialist_id=specialist_id,
            limit=100,
        )
        allowed_ids = {item.id for item in allowed_skills}

        selected = [
            skill_id
            for skill_id in selected
            if skill_id in allowed_ids
        ]

        before_skills = await self.repository.list_user_skill_ids(user_id)

        if sorted(before_skills) == sorted(selected):
            return before_skills, selected, False

        try:
            await self.repository.replace_user_skills(
                user_id=user_id,
                skill_ids=selected,
            )

            await EventRepository(
                self.repository.session
            ).create_event(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="change_submitted",
                entity_type="specialist",
                entity_id=specialist_id,
                payload={
                    "field": "skills",
                    "before": [
                        str(item)
                        for item in before_skills
                    ],
                    "after": [
                        str(item)
                        for item in selected
                    ],
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return before_skills, selected, True