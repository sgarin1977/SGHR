from typing import Optional
from uuid import UUID

from sqlalchemy import Integer, and_, delete, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from database.models import (
    City,
    Country,
    EventLog,
    Profession,
    Specialist,
    SpecialistCategory,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistProfession,
    SpecialistService,
    User,
    UserRoleMapping,
    ProfileVisibilitySetting,
    ProfessionAlias,
    ProfessionSkill,
    Skill,
    UserSkill,
)
MAX_SPECIALIST_CATEGORIES = 3
MAX_PROFESSIONS_PER_CATEGORY = 3

class SpecialistRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _release_category_conditions():
        return (
            SpecialistCategory.is_active.is_(True),
            SpecialistCategory.extra_metadata["release"].astext
            == "specialists_directory_v1",
        )

    def _validate_profession_limits(self, profession_selections: list[dict]) -> None:
        categories: dict[str, int] = {}

        for item in profession_selections:
            category_id = str(item["category_id"])
            categories[category_id] = categories.get(category_id, 0) + 1

        if len(categories) > MAX_SPECIALIST_CATEGORIES:
            raise ValueError("You can select no more than 3 sections.")

        if any(count > MAX_PROFESSIONS_PER_CATEGORY for count in categories.values()):
            raise ValueError("You can select no more than 3 professions in one section.")
        
    async def list_active_categories(self, limit: int = 50) -> list[SpecialistCategory]:
        result = await self.session.execute(
            select(SpecialistCategory)
            .where(
                *self._release_category_conditions(),
            )
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
            .join(SpecialistCategory, SpecialistCategory.id == Profession.category_id)
            .where(
                Profession.category_id == category_id,
                Profession.is_active.is_(True),
                *self._release_category_conditions(),
            )
            .order_by(Profession.sort_order, Profession.name)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_active_professions(self, limit: int = 50) -> list[Profession]:
        result = await self.session.execute(
            select(Profession)
            .join(SpecialistCategory, SpecialistCategory.id == Profession.category_id)
            .where(
                Profession.is_active.is_(True),
                *self._release_category_conditions(),
            )
            .order_by(Profession.sort_order, Profession.name)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_skills_for_specialist_professions(
        self,
        *,
        specialist_id: UUID,
        language: str = "ru",
        limit: int = 30,
    ) -> list[Skill]:
        result = await self.session.execute(
            select(Skill)
            .join(ProfessionSkill, ProfessionSkill.skill_id == Skill.id)
            .join(
                SpecialistProfession,
                SpecialistProfession.profession_id == ProfessionSkill.profession_id,
            )
            .where(
                SpecialistProfession.specialist_id == specialist_id,
                SpecialistProfession.status == "active",
                Skill.is_active.is_(True),
            )
            .group_by(Skill.id)
            .order_by(
                func.max(ProfessionSkill.is_primary.cast(Integer)).desc(),
                func.min(ProfessionSkill.sort_order),
                Skill.name,
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_user_skill_ids(self, user_id: UUID) -> list[UUID]:
        result = await self.session.execute(
            select(UserSkill.skill_id)
            .where(UserSkill.user_id == user_id)
            .order_by(UserSkill.created_at.asc())
        )
        return list(result.scalars().all())

    async def replace_user_skills(
        self,
        *,
        user_id: UUID,
        skill_ids: list[UUID],
    ) -> None:
        await self.session.execute(
            delete(UserSkill).where(UserSkill.user_id == user_id)
        )

        for skill_id in skill_ids:
            self.session.add(
                UserSkill(
                    user_id=user_id,
                    skill_id=skill_id,
                )
            )

        await self.session.flush()

    async def search_professions_by_text(
        self,
        query: str,
        limit: int = 10,
    ) -> list[Profession]:
        normalized_query = (query or "").strip().lower()

        if not normalized_query:
            return []

        exact_result = await self.session.execute(
            select(Profession)
            .join(SpecialistCategory, SpecialistCategory.id == Profession.category_id)
            .outerjoin(
                ProfessionAlias,
                and_(
                    ProfessionAlias.profession_id == Profession.id,
                    ProfessionAlias.is_active.is_(True),
                ),
            )
            .where(
                Profession.is_active.is_(True),
                *self._release_category_conditions(),
                or_(
                    func.lower(func.trim(Profession.name)) == normalized_query,
                    func.lower(func.trim(Profession.name_ru)) == normalized_query,
                    func.lower(func.trim(Profession.name_en)) == normalized_query,
                    func.lower(func.trim(Profession.name_pt)) == normalized_query,
                    func.lower(func.trim(Profession.normalized_name)) == normalized_query,
                    ProfessionAlias.normalized_alias == normalized_query,
                ),
            )
            .order_by(Profession.sort_order, Profession.name)
            .limit(limit)
        )

        exact_matches = list(exact_result.scalars().unique().all())

        if exact_matches:
            return exact_matches

        like_query = f"%{normalized_query}%"

        fallback_result = await self.session.execute(
            select(Profession)
            .join(SpecialistCategory, SpecialistCategory.id == Profession.category_id)
            .outerjoin(
                ProfessionAlias,
                and_(
                    ProfessionAlias.profession_id == Profession.id,
                    ProfessionAlias.is_active.is_(True),
                ),
            )
            .where(
                Profession.is_active.is_(True),
                *self._release_category_conditions(),
                or_(
                    func.lower(Profession.name).ilike(like_query),
                    func.lower(Profession.name_ru).ilike(like_query),
                    func.lower(Profession.name_en).ilike(like_query),
                    func.lower(Profession.name_pt).ilike(like_query),
                    func.lower(Profession.normalized_name).ilike(like_query),
                    ProfessionAlias.normalized_alias.ilike(like_query),
                    ProfessionAlias.alias.ilike(like_query),
                ),
            )
            .order_by(Profession.sort_order, Profession.name)
            .limit(limit)
        )

        return list(fallback_result.scalars().unique().all())

    async def list_active_cities(self, limit: int = 50) -> list[City]:
        result = await self.session.execute(
            select(City)
            .where(City.is_active.is_(True))
            .order_by(City.name)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def find_active_city_in_text(self, query: str) -> City | None:
        normalized_query = f" {(query or '').strip().lower()} "

        if len(normalized_query.strip()) < 2:
            return None

        result = await self.session.execute(
            select(City)
            .where(
                City.is_active.is_(True),
                or_(
                    literal(normalized_query).ilike(
                        func.concat("% ", func.lower(City.name), "%")
                    ),
                    literal(normalized_query).ilike(
                        func.concat("% ", func.lower(City.name_ru), "%")
                    ),
                    literal(normalized_query).ilike(
                        func.concat("% ", func.lower(City.name_en), "%")
                    ),
                    literal(normalized_query).ilike(
                        func.concat("% ", func.lower(City.name_pt), "%")
                    ),
                ),
            )
            .order_by(
                func.length(City.name).desc(),
                City.name.asc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_category(self, category_id: UUID) -> Optional[SpecialistCategory]:
        result = await self.session.execute(
            select(SpecialistCategory).where(
                SpecialistCategory.id == category_id,
                *self._release_category_conditions(),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_profession(self, profession_id: UUID) -> Optional[Profession]:
        result = await self.session.execute(
            select(Profession)
            .join(SpecialistCategory, SpecialistCategory.id == Profession.category_id)
            .where(
                Profession.id == profession_id,
                Profession.is_active.is_(True),
                *self._release_category_conditions(),
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

    async def get_owned_specialist(
        self,
        *,
        specialist_id: UUID,
        user_id: UUID,
    ) -> Specialist | None:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            return None
        return specialist

    async def get_specialist_location_parts(
        self,
        *,
        specialist: Specialist,
    ) -> tuple[City | None, Country | None]:
        city = await self.session.get(City, specialist.city_id) if specialist.city_id else None
        country_id = city.country_id if city else specialist.country_id
        country = await self.session.get(Country, country_id) if country_id else None
        return city, country

    async def list_specialist_services_page(
        self,
        *,
        specialist_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[int, list[SpecialistService]]:
        total_result = await self.session.execute(
            select(func.count())
            .select_from(SpecialistService)
            .where(
                SpecialistService.specialist_id == specialist_id,
                SpecialistService.status != "deleted",
            )
        )
        total = int(total_result.scalar_one() or 0)

        result = await self.session.execute(
            select(SpecialistService)
            .where(
                SpecialistService.specialist_id == specialist_id,
                SpecialistService.status != "deleted",
            )
            .order_by(SpecialistService.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        return total, list(result.scalars().all())

    async def get_specialist_profile_visibility(
        self,
        *,
        user_id: UUID,
    ) -> str | None:
        result = await self.session.execute(
            select(ProfileVisibilitySetting.visibility_level).where(
                ProfileVisibilitySetting.user_id == user_id,
                ProfileVisibilitySetting.profile_type == "specialist",
            )
        )
        return result.scalar_one_or_none()

    async def set_specialist_profile_status(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        status: str,
    ) -> Specialist:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            raise ValueError("Specialist profile not found.")

        specialist.status = status
        await self.session.flush()
        return specialist

    async def update_specialist_availability(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        availability_status: str,
        available_from_text: str | None = None,
    ) -> Specialist:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            raise ValueError("Specialist profile not found.")

        metadata = dict(specialist.extra_metadata or {})
        metadata["availability_status"] = availability_status

        if available_from_text:
            metadata["available_from_text"] = available_from_text
        else:
            metadata.pop("available_from_text", None)

        specialist.is_available = availability_status == "available_now"
        specialist.extra_metadata = metadata
        specialist.updated_at = datetime.utcnow()

        await self.session.flush()
        return specialist

    async def update_specialist_profile_visibility(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        visibility: str,
    ) -> tuple[Specialist, str | None]:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            raise ValueError("Specialist profile not found.")

        result = await self.session.execute(
            select(ProfileVisibilitySetting).where(
                ProfileVisibilitySetting.user_id == user_id,
                ProfileVisibilitySetting.profile_type == "specialist",
            )
        )
        settings = result.scalar_one_or_none()

        before_visibility = settings.visibility_level if settings else None

        if settings:
            settings.visibility_level = visibility
            settings.visible_to_clients = True
            settings.visible_to_employers = False
            settings.visible_to_agencies = False
            settings.allow_direct_messages = True
            settings.allow_profile_export = False
            settings.updated_at = datetime.utcnow()
        else:
            settings = ProfileVisibilitySetting(
                user_id=user_id,
                profile_type="specialist",
                visibility_level=visibility,
                visible_to_clients=True,
                visible_to_employers=False,
                visible_to_agencies=False,
                allow_direct_messages=True,
                allow_profile_export=False,
            )
            self.session.add(settings)

        metadata = dict(specialist.extra_metadata or {})
        metadata["contact_visibility"] = visibility
        specialist.extra_metadata = metadata

        await self.session.flush()
        return specialist, before_visibility

    async def update_specialist_work_format(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        work_format: str,
    ) -> Specialist:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            raise ValueError("Specialist profile not found.")

        specialist.work_format = work_format
        await self.session.flush()
        return specialist

    async def list_specialist_language_codes(
        self,
        *,
        specialist_id: UUID,
    ) -> list[str]:
        result = await self.session.execute(
            select(SpecialistLanguage.language_code).where(
                SpecialistLanguage.specialist_id == specialist_id,
            )
        )
        return [row[0] for row in result.all()]

    async def replace_specialist_languages(
        self,
        *,
        user_id: UUID,
        specialist_id: UUID,
        language_codes: list[str],
    ) -> list[str]:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            raise ValueError("Specialist profile not found.")

        await self.session.execute(
            delete(SpecialistLanguage).where(
                SpecialistLanguage.specialist_id == specialist_id,
            )
        )

        for code in language_codes:
            self.session.add(
                SpecialistLanguage(
                    specialist_id=specialist_id,
                    language_code=code,
                    level="basic",
                )
            )

        await self.session.flush()
        return language_codes

    async def get_owned_service_item(
        self,
        *,
        specialist_id: UUID,
        user_id: UUID,
        service_id: UUID,
    ) -> SpecialistService | None:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            return None

        service = await self.session.get(SpecialistService, service_id)
        if (
            not service
            or service.specialist_id != specialist_id
            or service.status == "deleted"
        ):
            return None

        return service

    async def create_specialist_service_item(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        category_id: UUID | None,
        profession_id: UUID | None,
        title: str,
        description: str,
        price_from: float | None,
        price_to: float | None,
        currency: str,
    ) -> SpecialistService:
        service = SpecialistService(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            category_id=category_id,
            profession_id=profession_id,
            title=title,
            description=description,
            price_from=price_from,
            price_to=price_to,
            currency=currency,
            price_unit="service",
            status="active",
        )
        self.session.add(service)
        await self.session.flush()
        return service

    async def update_specialist_service_item(
        self,
        *,
        specialist_id: UUID,
        user_id: UUID,
        service_id: UUID,
        title: str,
        description: str,
        price_from: float | None,
        price_to: float | None,
        currency: str,
        category_id: UUID | None,
        profession_id: UUID | None,
    ) -> SpecialistService:
        service = await self.get_owned_service_item(
            specialist_id=specialist_id,
            user_id=user_id,
            service_id=service_id,
        )
        if not service:
            raise ValueError("Specialist service not found.")

        service.title = title
        service.description = description
        service.price_from = price_from
        service.price_to = price_to
        service.currency = currency
        service.category_id = category_id
        service.profession_id = profession_id

        await self.session.flush()
        return service

    async def set_specialist_service_item_status(
        self,
        *,
        specialist_id: UUID,
        user_id: UUID,
        service_id: UUID,
        status: str,
    ) -> SpecialistService:
        service = await self.get_owned_service_item(
            specialist_id=specialist_id,
            user_id=user_id,
            service_id=service_id,
        )
        if not service:
            raise ValueError("Specialist service not found.")

        service.status = status
        await self.session.flush()
        return service

    async def list_active_specialist_professions(
        self,
        specialist_id: UUID,
    ):
        result = await self.session.execute(
            select(SpecialistProfession, SpecialistCategory, Profession)
            .join(
                SpecialistCategory,
                SpecialistCategory.id == SpecialistProfession.category_id,
            )
            .join(
                Profession,
                Profession.id == SpecialistProfession.profession_id,
            )
            .where(
                SpecialistProfession.specialist_id == specialist_id,
                SpecialistProfession.status == "active",
            )
            .order_by(
                SpecialistProfession.is_primary.desc(),
                SpecialistProfession.created_at,
            )
        )
        return result.all()

    async def replace_specialist_professions(
        self,
        *,
        specialist_id: UUID,
        user_id: UUID,
        profession_selections: list[dict],
    ) -> Specialist:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            raise ValueError("Specialist profile not found.")


        normalized_selections = []
        seen_profession_ids = set()

        for item in profession_selections:
            category_id = UUID(str(item["category_id"]))
            profession_id = UUID(str(item["profession_id"]))

            if profession_id in seen_profession_ids:
                continue

            seen_profession_ids.add(profession_id)
            normalized_selections.append(
                {
                    "category_id": category_id,
                    "profession_id": profession_id,
                }
            )

        if not normalized_selections:
            raise ValueError("At least one profession is required.")
        self._validate_profession_limits(normalized_selections)
        result = await self.session.execute(
            select(SpecialistProfession).where(
                SpecialistProfession.specialist_id == specialist.id,
                SpecialistProfession.status == "active",
            )
        )
        existing_rows = list(result.scalars().all())

        for row in existing_rows:
            row.status = "deleted"
            row.is_primary = False

        primary = normalized_selections[0]
        specialist.category_id = primary["category_id"]
        specialist.profession_id = primary["profession_id"]

        for index, item in enumerate(normalized_selections):
            self.session.add(
                SpecialistProfession(
                    specialist_id=specialist.id,
                    category_id=item["category_id"],
                    profession_id=item["profession_id"],
                    is_primary=index == 0,
                    status="active",
                )
            )

        specialist.updated_at = datetime.utcnow()
        await self.session.commit()
        return specialist

    async def create_specialist_profile(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        category_id: UUID,
        profession_id: UUID,
        profession_selections: list[dict] | None = None,
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
        contact_visibility: str | None,
        allow_requests: bool,
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
            extra_metadata={
                "contact_text": contact_text,
                "contact_visibility": contact_visibility or "platform_only",
                "allow_requests": bool(allow_requests),
            },
        )
        self.session.add(specialist)
        await self.session.flush()

        seen_profession_ids = set()
        normalized_selections = []

        for item in profession_selections or []:
            item_category_id = UUID(str(item["category_id"]))
            item_profession_id = UUID(str(item["profession_id"]))

            if item_profession_id in seen_profession_ids:
                continue

            seen_profession_ids.add(item_profession_id)
            normalized_selections.append(
                {
                    "category_id": item_category_id,
                    "profession_id": item_profession_id,
                }
            )

        if not normalized_selections:
            normalized_selections.append(
                {
                    "category_id": category_id,
                    "profession_id": profession_id,
                }
            )
        self._validate_profession_limits(normalized_selections)
        for index, item in enumerate(normalized_selections):
            self.session.add(
                SpecialistProfession(
                    specialist_id=specialist.id,
                    category_id=item["category_id"],
                    profession_id=item["profession_id"],
                    is_primary=index == 0,
                    status="active",
                )
            )

        await self.ensure_specialist_role(tenant_id=tenant_id, user_id=user_id)
        visibility_level = contact_visibility or "platform_only"

        visibility_result = await self.session.execute(
            select(ProfileVisibilitySetting).where(
                ProfileVisibilitySetting.user_id == user_id,
                ProfileVisibilitySetting.profile_type == "specialist",
            )
        )
        visibility_settings = visibility_result.scalar_one_or_none()

        if visibility_settings:
            visibility_settings.visibility_level = visibility_level
            visibility_settings.visible_to_clients = True
            visibility_settings.visible_to_employers = False
            visibility_settings.visible_to_agencies = False
            visibility_settings.allow_direct_messages = bool(allow_requests)
            visibility_settings.allow_profile_export = False
            visibility_settings.updated_at = datetime.utcnow()
        else:
            self.session.add(
                ProfileVisibilitySetting(
                    user_id=user_id,
                    profile_type="specialist",
                    visibility_level=visibility_level,
                    visible_to_clients=True,
                    visible_to_employers=False,
                    visible_to_agencies=False,
                    allow_direct_messages=bool(allow_requests),
                    allow_profile_export=False,
                )
            )
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
        clear_city: bool = False,
        clear_coordinates: bool = False,
        service_radius_km: int | None = None,
    ) -> Specialist:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist or specialist.user_id != user_id:
            raise ValueError("Specialist profile not found.")

        location_changed = any(
            (
                country_id is not None,
                city_id is not None,
                latitude is not None,
                longitude is not None,
                clear_city,
                clear_coordinates,
            )
        )

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

        if profession_id is not None:
            effective_category_id = category_id or specialist.category_id

            result = await self.session.execute(
                select(SpecialistProfession).where(
                    SpecialistProfession.specialist_id == specialist.id,
                    SpecialistProfession.status == "active",
                )
            )
            active_professions = list(result.scalars().all())

            matching_profession = None
            for item in active_professions:
                if item.profession_id == profession_id:
                    matching_profession = item
                    break

            for item in active_professions:
                item.is_primary = False

            if matching_profession:
                matching_profession.category_id = effective_category_id
                matching_profession.is_primary = True
                matching_profession.status = "active"
            else:
                self.session.add(
                    SpecialistProfession(
                        specialist_id=specialist.id,
                        category_id=effective_category_id,
                        profession_id=profession_id,
                        is_primary=True,
                        status="active",
                    )
                )

        if country_id is not None:
            specialist.country_id = country_id

        if clear_city:
            specialist.city_id = None
        elif city_id is not None:
            specialist.city_id = city_id

        if clear_coordinates:
            specialist.latitude = None
            specialist.longitude = None
        else:
            if latitude is not None:
                specialist.latitude = latitude

            if longitude is not None:
                specialist.longitude = longitude

        if service_radius_km is not None:
            specialist.service_radius_km = service_radius_km

        if location_changed:
            result = await self.session.execute(
                select(SpecialistLocation).where(
                    SpecialistLocation.specialist_id == specialist.id,
                    SpecialistLocation.is_current.is_(True),
                )
            )

            for current_location in result.scalars().all():
                current_location.is_current = False

            self.session.add(
                SpecialistLocation(
                    tenant_id=specialist.tenant_id,
                    specialist_id=specialist.id,
                    country_id=specialist.country_id,
                    city_id=specialist.city_id,
                    latitude=specialist.latitude,
                    longitude=specialist.longitude,
                    location_source="profile_edit",
                    visibility_level="city",
                    is_current=True,
                )
            )

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