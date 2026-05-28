from dataclasses import dataclass
from uuid import UUID

from database.repositories.legal import LegalRepository
from database.repositories.rate_limit import RateLimitRepository
from database.repositories.specialist import SpecialistRepository
from services.legal import LegalService, MissingLegalDocumentError
from services.rate_limit import RateLimitError, RateLimitService


class SpecialistRegistrationError(Exception):
    pass


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
        )