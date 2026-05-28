from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    City,
    Country,
    EventLog,
    Profession,
    Specialist,
    SpecialistCategory,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistService,
    User,
    UserRoleMapping,
)


class SpecialistRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_active_categories(self, limit: int = 50) -> list[SpecialistCategory]:
        result = await self.session.execute(
            select(SpecialistCategory)
            .where(SpecialistCategory.is_active.is_(True))
            .order_by(SpecialistCategory.sort_order, SpecialistCategory.name)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_active_professions_by_category(
        self,
        category_id: UUID,
        limit: int = 50,
    ) -> list[Profession]:
        result = await self.session.execute(
            select(Profession)
            .where(
                Profession.category_id == category_id,
                Profession.is_active.is_(True),
            )
            .order_by(Profession.name)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_active_professions(self, limit: int = 50) -> list[Profession]:
        result = await self.session.execute(
            select(Profession)
            .where(Profession.is_active.is_(True))
            .order_by(Profession.name)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_active_cities(self, limit: int = 50) -> list[City]:
        result = await self.session.execute(
            select(City)
            .where(City.is_active.is_(True))
            .order_by(City.name)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_active_category(self, category_id: UUID) -> Optional[SpecialistCategory]:
        result = await self.session.execute(
            select(SpecialistCategory).where(
                SpecialistCategory.id == category_id,
                SpecialistCategory.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_profession(self, profession_id: UUID) -> Optional[Profession]:
        result = await self.session.execute(
            select(Profession).where(
                Profession.id == profession_id,
                Profession.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_country(self, country_id: UUID) -> Optional[Country]:
        result = await self.session.execute(
            select(Country).where(
                Country.id == country_id,
                Country.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_city(self, city_id: UUID) -> Optional[City]:
        result = await self.session.execute(
            select(City).where(
                City.id == city_id,
                City.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: UUID) -> Optional[Specialist]:
        result = await self.session.execute(
            select(Specialist).where(Specialist.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create_specialist_profile(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        category_id: UUID,
        profession_id: UUID,
        country_id: UUID | None,
        city_id: UUID | None,
        display_name: str,
        short_description: str,
        full_description: str | None,
        price_from,
        price_to,
        currency: str,
        price_unit: str | None,
        latitude,
        longitude,
        service_radius_km: int | None,
        languages: list[str],
        service_title: str | None,
        service_description: str | None,
        contact_text: str | None,
        work_format: str | None,
    ) -> Specialist:
        price_unit = price_unit or "service"
        currency = currency or "EUR"
        service_radius_km = service_radius_km or 0
        work_format = work_format or "mixed"

        specialist = Specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            category_id=category_id,
            profession_id=profession_id,
            country_id=country_id,
            city_id=city_id,
            display_name=display_name,
            short_description=short_description,
            full_description=full_description,
            price_from=price_from,
            price_to=price_to,
            currency=currency,
            price_unit=price_unit,
            work_format=work_format,
            latitude=latitude,
            longitude=longitude,
            service_radius_km=service_radius_km,
            status="pending_moderation",
            is_verified=False,
            is_premium=False,
            is_available=True,
            extra_metadata={"contact_text": contact_text} if contact_text else {},
        )
        self.session.add(specialist)
        await self.session.flush()
        await self.ensure_specialist_role(tenant_id=tenant_id, user_id=user_id)
        if country_id or city_id or latitude or longitude:
            self.session.add(
                SpecialistLocation(
                    tenant_id=tenant_id,
                    specialist_id=specialist.id,
                    country_id=country_id,
                    city_id=city_id,
                    latitude=latitude,
                    longitude=longitude,
                    location_source="registration",
                    visibility_level="city",
                    is_current=True,
                )
            )

        for language_code in languages:
            self.session.add(
                SpecialistLanguage(
                    specialist_id=specialist.id,
                    language_code=language_code,
                    level="basic",
                )
            )

        if service_title:
            self.session.add(
                SpecialistService(
                    tenant_id=tenant_id,
                    specialist_id=specialist.id,
                    title=service_title,
                    description=service_description,
                    price_from=price_from,
                    price_to=price_to,
                    currency=currency,
                    price_unit=price_unit,
                    status="active",
                )
            )

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="specialist_profile_created",
                entity_type="specialist",
                entity_id=specialist.id,
                payload={
                    "status": "pending_moderation",
                    "category_id": str(category_id),
                    "profession_id": str(profession_id),
                    "city_id": str(city_id) if city_id else None,
                },
                platform="telegram",
            )
        )
        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="specialist_submitted",
                entity_type="specialist",
                entity_id=specialist.id,
                payload={
                    "status": "pending_moderation",
                },
                platform="telegram",
            )
        )

        await self.session.commit()
        return specialist

    async def ensure_specialist_role(self, tenant_id: UUID, user_id: UUID) -> None:
        role_result = await self.session.execute(
            select(UserRoleMapping).where(
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.role == "specialist",
            )
        )
        role = role_result.scalar_one_or_none()

        if role:
            if role.status != "active":
                role.status = "active"
        else:
            self.session.add(
                UserRoleMapping(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role="specialist",
                    status="active",
                )
            )

        user = await self.session.get(User, user_id)
        if user:
            user.active_role = "specialist"

    async def update_specialist_profile_fields(
        self,
        *,
        specialist_id: UUID,
        user_id: UUID,
        display_name: str | None = None,
        short_description: str | None = None,
        contact_text: str | None = None,
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        country_id: UUID | None = None,
        city_id: UUID | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        service_radius_km: int | None = None,
    ) -> Specialist:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            raise ValueError("Specialist profile not found.")

        before_state = {
            "display_name": specialist.display_name,
            "short_description": specialist.short_description,
            "contact_text": (specialist.extra_metadata or {}).get("contact_text"),
            "category_id": str(specialist.category_id) if specialist.category_id else None,
            "profession_id": str(specialist.profession_id) if specialist.profession_id else None,
            "country_id": str(specialist.country_id) if specialist.country_id else None,
            "city_id": str(specialist.city_id) if specialist.city_id else None,
        }

        if display_name is not None:
            specialist.display_name = display_name

        if short_description is not None:
            specialist.short_description = short_description
            specialist.full_description = short_description

        if category_id is not None:
            specialist.category_id = category_id

        if profession_id is not None:
            specialist.profession_id = profession_id

        if country_id is not None:
            specialist.country_id = country_id

        if city_id is not None:
            specialist.city_id = city_id

        if latitude is not None:
            specialist.latitude = latitude

        if longitude is not None:
            specialist.longitude = longitude

        if service_radius_km is not None:
            specialist.service_radius_km = service_radius_km

        if contact_text is not None:
            metadata = dict(specialist.extra_metadata or {})
            metadata["contact_text"] = contact_text
            specialist.extra_metadata = metadata

        after_state = {
            "display_name": specialist.display_name,
            "short_description": specialist.short_description,
            "contact_text": (specialist.extra_metadata or {}).get("contact_text"),
            "category_id": str(specialist.category_id) if specialist.category_id else None,
            "profession_id": str(specialist.profession_id) if specialist.profession_id else None,
            "country_id": str(specialist.country_id) if specialist.country_id else None,
            "city_id": str(specialist.city_id) if specialist.city_id else None,
        }

        self.session.add(
            EventLog(
                tenant_id=specialist.tenant_id,
                user_id=user_id,
                event_type="profile_edit",
                entity_type="specialist",
                entity_id=specialist.id,
                payload={
                    "before": before_state,
                    "after": after_state,
                },
                platform="telegram",
            )
        )

        await self.session.commit()
        return specialist