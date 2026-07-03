from dataclasses import dataclass
from uuid import UUID

from database.repositories.legal import LegalRepository
from database.repositories.rate_limit import RateLimitRepository
from database.repositories.specialist import SpecialistRepository
from services.legal import LegalService, MissingLegalDocumentError
from services.rate_limit import RateLimitError, RateLimitService


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

def _localized_model_name(item, language: str) -> str:
    if not item:
        return "-"

    return (
        getattr(item, f"name_{language}", None)
        or getattr(item, "name", None)
        or "-"
    )

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

        if data.service_id:
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

        return service, mode

    async def toggle_service_item_status(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        service_id: UUID,
    ):
        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        service = await self.repository.get_owned_service_item(
            specialist_id=specialist_id,
            user_id=user_id,
            service_id=service_id,
        )
        if not service:
            raise SpecialistRegistrationError("Service not found.")

        before_status = service.status or "active"
        after_status = "paused" if before_status == "active" else "active"

        service = await self.repository.set_specialist_service_item_status(
            specialist_id=specialist_id,
            user_id=user_id,
            service_id=service_id,
            status=after_status,
        )

        return service, before_status, after_status

    async def delete_service_item(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        service_id: UUID,
    ):
        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        service = await self.repository.get_owned_service_item(
            specialist_id=specialist_id,
            user_id=user_id,
            service_id=service_id,
        )
        if not service:
            raise SpecialistRegistrationError("Service not found.")

        before_status = service.status or "active"

        service = await self.repository.set_specialist_service_item_status(
            specialist_id=specialist_id,
            user_id=user_id,
            service_id=service_id,
            status="deleted",
        )

        return service, before_status
    async def toggle_profile_status(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
    ):
        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        before_status = specialist.status
        after_status = "active" if before_status == "paused" else "paused"
        action = "resume" if after_status == "active" else "pause"

        specialist = await self.repository.set_specialist_profile_status(
            user_id=user_id,
            specialist_id=specialist_id,
            status=after_status,
        )

        return specialist, before_status, after_status, action

    async def set_profile_status(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        status: str,
    ):
        if status not in {"active", "paused", "draft"}:
            raise SpecialistRegistrationError("Invalid profile status.")

        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        before_status = specialist.status
        updated_specialist = await self.repository.set_specialist_profile_status(
            user_id=user_id,
            specialist_id=specialist_id,
            status=status,
        )

        return updated_specialist, before_status, status

    async def update_availability(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        availability_status: str,
        available_from_text: str | None = None,
    ):
        if availability_status not in {"available_now", "partly_busy", "available_from"}:
            raise SpecialistRegistrationError("Invalid availability status.")

        normalized_date = (available_from_text or "").strip() or None

        if availability_status == "available_from" and not normalized_date:
            raise SpecialistRegistrationError("Availability date is required.")

        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        before_metadata = dict(specialist.extra_metadata or {})
        before_status = before_metadata.get("availability_status")
        before_date = before_metadata.get("available_from_text")
        before_is_available = bool(specialist.is_available)

        updated_specialist = await self.repository.update_specialist_availability(
            user_id=user_id,
            specialist_id=specialist_id,
            availability_status=availability_status,
            available_from_text=normalized_date,
        )

        changed = (
            before_status != availability_status
            or before_date != normalized_date
            or before_is_available != bool(updated_specialist.is_available)
        )

        return updated_specialist, before_metadata, dict(updated_specialist.extra_metadata or {}), changed

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
        user_id: UUID,
        specialist_id: UUID,
        visibility: str,
    ):
        if visibility not in {"platform_only", "public_limited", "private"}:
            raise SpecialistRegistrationError("Invalid visibility.")

        specialist = await self.repository.get_by_user_id(user_id)
        if not specialist or specialist.id != specialist_id:
            raise SpecialistRegistrationError("Specialist profile not found.")

        updated_specialist, before_visibility = (
            await self.repository.update_specialist_profile_visibility(
                user_id=user_id,
                specialist_id=specialist_id,
                visibility=visibility,
            )
        )

        return updated_specialist, before_visibility, visibility
    
    async def update_work_format(
        self,
        *,
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

        updated_specialist = await self.repository.update_specialist_work_format(
            user_id=user_id,
            specialist_id=specialist_id,
            work_format=work_format,
        )

        return updated_specialist, before_work_format, work_format, True
    
    async def update_languages(
        self,
        *,
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

        await self.repository.replace_specialist_languages(
            user_id=user_id,
            specialist_id=specialist_id,
            language_codes=selected,
        )

        return before_languages, selected, True
    
    async def update_skills(
        self,
        *,
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

        await self.repository.replace_user_skills(
            user_id=user_id,
            skill_ids=selected,
        )

        return before_skills, selected, True