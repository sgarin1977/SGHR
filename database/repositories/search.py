from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ReputationScore,
    EventLog,
    Specialist,
    SpecialistCategory,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistProfession,
    SpecialistService,
    Profession,
    ProfessionSkill,
    Skill,
    ProfessionalCabinet,
    ProfessionalCabinetSkill,
    User,
    UserSkill,
    City,
    Country,
    SpecialistPromotion,
)
PUBLIC_SPECIALIST_STATUSES = (
    "approved",
    "pending_moderation",
)

PUBLIC_CABINET_MODERATION_STATUSES = (
    "approved",
    "pending_moderation",
)

@dataclass
class SpecialistSearchFilters:
    category_id: UUID | None = None
    profession_id: UUID | None = None
    profession_ids: list[UUID] | None = None
    city_id: UUID | None = None
    country_id: UUID | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = 25
    language_code: str | None = None
    verified_only: bool = False
    premium_only: bool = False
    available_only: bool = False
    rating_min: float | None = None
    work_format: str | None = None
    status: str | None = None
    page: int = 1
    page_size: int = 5
    limit: int | None = None
    offset: int | None = None
    sort_by: str = "distance"

    @property
    def normalized_radius_km(self) -> float:
        radius = 25 if self.radius_km is None else float(self.radius_km)
        return max(0, min(radius, 100))

    @property
    def normalized_page_size(self) -> int:
        if self.limit is not None:
            return max(1, min(int(self.limit), 10))
        return max(1, min(int(self.page_size), 10))
    
    @property
    def query_limit(self) -> int:
        if self.limit is not None:
            return max(1, int(self.limit))
        return self.normalized_page_size

    @property
    def normalized_offset(self) -> int:
        if self.offset is not None:
            return max(0, int(self.offset))
        return (max(1, int(self.page)) - 1) * self.normalized_page_size


class SpecialistSearchRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _active_cabinet_promotion_conditions(
        self,
    ) -> tuple:
        return (
            SpecialistPromotion.tenant_id
            == ProfessionalCabinet.tenant_id,
            SpecialistPromotion.professional_cabinet_id
            == ProfessionalCabinet.id,
            SpecialistPromotion.status == "active",
            or_(
                SpecialistPromotion.starts_at.is_(
                    None
                ),
                SpecialistPromotion.starts_at
                <= func.now(),
            ),
            or_(
                SpecialistPromotion.ends_at.is_(
                    None
                ),
                SpecialistPromotion.ends_at
                > func.now(),
            ),
        )

    def _active_cabinet_promotion_exists(
        self,
        promotion_type: str,
    ):
        return (
            select(SpecialistPromotion.id)
            .where(
                *self._active_cabinet_promotion_conditions(),
                SpecialistPromotion.promotion_type
                == promotion_type,
            )
            .correlate(ProfessionalCabinet)
            .exists()
        )

    def _cabinet_promotion_priority_expression(
        self,
    ):
        priority = case(
            (
                SpecialistPromotion.promotion_type
                == "top_category",
                100.0,
            ),
            (
                SpecialistPromotion.promotion_type
                == "premium",
                50.0,
            ),
            (
                SpecialistPromotion.promotion_type
                == "featured_service",
                25.0,
            ),
            (
                SpecialistPromotion.promotion_type
                == "boost",
                15.0,
            ),
            else_=0.0,
        )

        return func.coalesce(
            (
                select(func.max(priority))
                .where(
                    *self._active_cabinet_promotion_conditions()
                )
                .correlate(ProfessionalCabinet)
                .scalar_subquery()
            ),
            0.0,
        )

    async def list_active_professional_cabinet_promotion_types(
        self,
        *,
        tenant_id: UUID,
        professional_cabinet_ids: list[UUID],
    ) -> dict[UUID, set[str]]:
        cabinet_ids = list(
            set(professional_cabinet_ids)
        )
        if not cabinet_ids:
            return {}

        now = datetime.utcnow()

        result = await self.session.execute(
            select(
                SpecialistPromotion.professional_cabinet_id,
                SpecialistPromotion.promotion_type,
            ).where(
                SpecialistPromotion.tenant_id
                == tenant_id,
                SpecialistPromotion.professional_cabinet_id.in_(
                    cabinet_ids
                ),
                SpecialistPromotion.status == "active",
                or_(
                    SpecialistPromotion.starts_at.is_(
                        None
                    ),
                    SpecialistPromotion.starts_at
                    <= now,
                ),
                or_(
                    SpecialistPromotion.ends_at.is_(
                        None
                    ),
                    SpecialistPromotion.ends_at
                    > now,
                ),
            )
        )

        promotion_types: dict[
            UUID,
            set[str],
        ] = {}

        for (
            professional_cabinet_id,
            promotion_type,
        ) in result.tuples().all():
            if not professional_cabinet_id:
                continue

            promotion_types.setdefault(
                professional_cabinet_id,
                set(),
            ).add(promotion_type)

        return promotion_types
    def _distance_km_expression(
        self,
        *,
        latitude: float,
        longitude: float,
    ):
        earth_radius_km = 6371.0

        specialist_latitude = func.coalesce(
            SpecialistLocation.latitude,
            Specialist.latitude,
        )
        specialist_longitude = func.coalesce(
            SpecialistLocation.longitude,
            Specialist.longitude,
        )

        lat1 = func.radians(literal(latitude))
        lon1 = func.radians(literal(longitude))
        lat2 = func.radians(specialist_latitude)
        lon2 = func.radians(specialist_longitude)

        delta_lat = lat2 - lat1
        delta_lon = lon2 - lon1

        haversine = (
            func.pow(func.sin(delta_lat / 2), 2)
            + func.cos(lat1)
            * func.cos(lat2)
            * func.pow(func.sin(delta_lon / 2), 2)
        )

        return earth_radius_km * 2 * func.asin(func.sqrt(haversine))

    async def get_approved_specialist_for_card(
        self,
        specialist_id: UUID,
    ) -> Specialist | None:
        result = await self.session.execute(
            select(Specialist)
            .join(User, User.id == Specialist.user_id)
            .where(
                Specialist.id == specialist_id,
                Specialist.status.in_(
    PUBLIC_SPECIALIST_STATUSES
),
                User.status.notin_(["blocked", "deleted"]),
            )
        )
        return result.scalar_one_or_none()

    async def get_approved_professional_cabinet_for_card(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
    ) -> tuple[
        Specialist,
        ProfessionalCabinet,
    ] | None:
        query = (
            select(
                Specialist,
                ProfessionalCabinet,
            )
            .join(
                ProfessionalCabinet,
                ProfessionalCabinet.specialist_id
                == Specialist.id,
            )
            .join(
                User,
                User.id == Specialist.user_id,
            )
            .where(
                Specialist.id == specialist_id,
                ProfessionalCabinet.specialist_id
                == specialist_id,
                ProfessionalCabinet.is_active.is_(
                    True
                ),
                ProfessionalCabinet.moderation_status.in_(
                    PUBLIC_CABINET_MODERATION_STATUSES
                ),
                Specialist.status.in_(
                    PUBLIC_SPECIALIST_STATUSES
                ),
                User.status.notin_(
                    ["blocked", "deleted"]
                ),
            )
        )

        query = query.where(
            Specialist.tenant_id == tenant_id,
            ProfessionalCabinet.tenant_id
            == tenant_id,
        )

        if professional_cabinet_id is not None:
            query = query.where(
                ProfessionalCabinet.id
                == professional_cabinet_id
            )
        else:
            query = query.where(
                ProfessionalCabinet.id
                == Specialist.active_professional_cabinet_id
            )

        result = await self.session.execute(
            query.limit(1)
        )

        row = result.one_or_none()
        if not row:
            return None

        specialist, cabinet = row
        return specialist, cabinet

    async def get_city_name(
        self,
        city_id: UUID | None,
        language: str = "ru",
    ) -> str | None:
        if not city_id:
            return None

        city = await self.session.get(City, city_id)
        if not city:
            return None

        localized = getattr(city, f"name_{language}", None)
        return localized or city.name_ru or city.name

    async def get_country_name(
        self,
        country_id: UUID | None,
        language: str = "ru",
    ) -> str | None:
        if not country_id:
            return None

        country = await self.session.get(
            Country,
            country_id,
        )
        if not country:
            return None

        return self._localized_name(
            country,
            language,
        )


    def _localized_name(self, item, language: str = "ru") -> str | None:
        if not item:
            return None

        localized = getattr(item, f"name_{language}", None)
        return localized or getattr(item, "name_ru", None) or getattr(item, "name", None)

    async def get_category_name(
        self,
        category_id: UUID | None,
        language: str = "ru",
    ) -> str | None:
        if not category_id:
            return None

        category = await self.session.get(SpecialistCategory, category_id)
        return self._localized_name(category, language)

    async def get_profession_name(
        self,
        profession_id: UUID | None,
        language: str = "ru",
    ) -> str | None:
        if not profession_id:
            return None

        profession = await self.session.get(Profession, profession_id)
        return self._localized_name(profession, language)

    async def get_public_service_titles(
        self,
        specialist_id: UUID,
        professional_cabinet_id: UUID | None = None,
        limit: int = 5,
    ) -> list[str]:
        filters = [
            SpecialistService.specialist_id
            == specialist_id,
            SpecialistService.status == "active",
        ]

        if professional_cabinet_id is not None:
            filters.append(
                SpecialistService.professional_cabinet_id
                == professional_cabinet_id
            )

        result = await self.session.execute(
            select(SpecialistService.title)
            .where(*filters)
            .order_by(
                SpecialistService.created_at.desc()
            )
            .limit(max(1, int(limit)))
        )
        return [
            title
            for title in result.scalars().all()
            if title
        ]

    async def get_public_skill_names_for_cabinet(
        self,
        *,
        professional_cabinet_id: UUID,
        language: str = "ru",
        limit: int = 8,
    ) -> list[str]:
        name_field = {
            "ru": Skill.name_ru,
            "en": Skill.name_en,
            "pt": Skill.name_pt,
            "es": Skill.name_es,
        }.get(language, Skill.name_ru)

        result = await self.session.execute(
            select(
                func.coalesce(
                    name_field,
                    Skill.name_ru,
                    Skill.name,
                )
            )
            .join(
                ProfessionalCabinetSkill,
                ProfessionalCabinetSkill.skill_id
                == Skill.id,
            )
            .where(
                ProfessionalCabinetSkill.professional_cabinet_id
                == professional_cabinet_id,
                Skill.is_active.is_(True),
            )
            .order_by(Skill.name.asc())
            .limit(max(1, int(limit)))
        )

        return [
            name
            for name in result.scalars().all()
            if name
        ]

    async def get_public_skill_names_for_user(
        self,
        user_id: UUID,
        language: str = "ru",
        limit: int = 8,
    ) -> list[str]:
        name_field = {
            "ru": Skill.name_ru,
            "en": Skill.name_en,
            "pt": Skill.name_pt,
            "es": Skill.name_es,
        }.get(language, Skill.name_ru)

        result = await self.session.execute(
            select(func.coalesce(name_field, Skill.name_ru, Skill.name))
            .join(UserSkill, UserSkill.skill_id == Skill.id)
            .where(
                UserSkill.user_id == user_id,
                Skill.is_active.is_(True),
            )
            .order_by(Skill.name.asc())
            .limit(max(1, int(limit)))
        )
        return [name for name in result.scalars().all() if name]

    async def get_language_codes_for_specialist(
        self,
        specialist_id: UUID,
    ) -> list[str]:
        result = await self.session.execute(
            select(SpecialistLanguage.language_code)
            .where(SpecialistLanguage.specialist_id == specialist_id)
            .order_by(SpecialistLanguage.language_code.asc())
        )
        return list(result.scalars().all())

    async def log_specialist_viewed(
        self,
        *,
        tenant_id: UUID | None,
        user_id: UUID | None,
        specialist_id: UUID,
        platform: str = "telegram",
    ) -> None:
        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="specialist_viewed",
                entity_type="specialist",
                entity_id=specialist_id,
                platform=platform,
                payload={},
            )
        )
        await self.session.commit()

    async def log_search_performed(
        self,
        *,
        tenant_id: UUID | None,
        user_id: UUID | None,
        filters: SpecialistSearchFilters,
        results_count: int,
        platform: str = "telegram",
    ) -> None:
        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="search_performed",
                entity_type="specialist_search",
                entity_id=None,
                platform=platform,
                payload={
                    "category_id": str(filters.category_id) if filters.category_id else None,
                    "profession_id": str(filters.profession_id) if filters.profession_id else None,
                    "city_id": str(filters.city_id) if filters.city_id else None,
                    "has_geo": filters.latitude is not None and filters.longitude is not None,
                    "radius_km": filters.normalized_radius_km,
                    "language_code": filters.language_code,
                    "verified_only": filters.verified_only,
                    "premium_only": filters.premium_only,
                    "rating_min": filters.rating_min,
                    "work_format": filters.work_format,
                    "page": filters.page,
                    "page_size": filters.normalized_page_size,
                    "offset": filters.normalized_offset,
                    "results_count": results_count,
                },
            )
        )
        await self.session.commit()

    async def list_recent_search_events(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        limit: int = 5,
    ) -> list[EventLog]:
        normalized_limit = max(1, min(int(limit), 10))

        result = await self.session.execute(
            select(EventLog)
            .where(
                EventLog.tenant_id == tenant_id,
                EventLog.user_id == user_id,
                EventLog.event_type.in_(
                    ["search_performed", "results_viewed", "empty_search"]
                ),
                EventLog.entity_type.in_(["search", "specialist_search"]),
            )
            .order_by(EventLog.created_at.desc())
            .limit(normalized_limit)
        )

        return list(result.scalars().all())

    async def search_specialists(
        self,
        filters: SpecialistSearchFilters,
    ) -> list[Specialist]:
        stmt = (
            select(Specialist)
            .join(User, User.id == Specialist.user_id)
            .where(
                Specialist.status == filters.status,
                User.status.notin_(["blocked", "deleted"]),
            )
        )

        if filters.city_id:
            current_location_in_city = exists(
                select(SpecialistLocation.id).where(
                    SpecialistLocation.specialist_id == Specialist.id,
                    SpecialistLocation.is_current.is_(True),
                    SpecialistLocation.city_id == filters.city_id,
                )
            )

            location_conditions = [
                Specialist.city_id == filters.city_id,
                current_location_in_city,
            ]

            if filters.country_id:
                specialist_serves_whole_country = and_(
                    Specialist.country_id == filters.country_id,
                    Specialist.city_id.is_(None),
                )

                current_location_serves_whole_country = exists(
                    select(SpecialistLocation.id).where(
                        SpecialistLocation.specialist_id == Specialist.id,
                        SpecialistLocation.is_current.is_(True),
                        SpecialistLocation.country_id == filters.country_id,
                        SpecialistLocation.city_id.is_(None),
                    )
                )

                location_conditions.extend(
                    [
                        specialist_serves_whole_country,
                        current_location_serves_whole_country,
                    ]
                )

            stmt = stmt.where(or_(*location_conditions))

        if filters.country_id:
            current_location_in_country = exists(
                select(SpecialistLocation.id).where(
                    SpecialistLocation.specialist_id == Specialist.id,
                    SpecialistLocation.is_current.is_(True),
                    SpecialistLocation.country_id == filters.country_id,
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.country_id == filters.country_id,
                    current_location_in_country,
                )
            )

        if filters.category_id:
            category_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.category_id == filters.category_id,
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.category_id == filters.category_id,
                    category_exists,
                )
            )

        profession_ids = list(filters.profession_ids or [])
        if filters.profession_id and filters.profession_id not in profession_ids:
            profession_ids.append(filters.profession_id)

        if profession_ids:
            profession_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.profession_id.in_(profession_ids),
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.profession_id.in_(profession_ids),
                    profession_exists,
                )
            )


        if filters.language_code:
            language_exists = exists(
                select(SpecialistLanguage.id).where(
                    SpecialistLanguage.specialist_id == Specialist.id,
                    SpecialistLanguage.language_code == filters.language_code.lower(),
                )
            )
            stmt = stmt.where(language_exists)

        if filters.premium_only:
            stmt = stmt.where(Specialist.is_premium.is_(True))

        if filters.verified_only:
            stmt = stmt.where(Specialist.is_verified.is_(True))

        if filters.available_only:
            stmt = stmt.where(Specialist.is_available.is_(True))

        if filters.rating_min is not None:
            stmt = stmt.where(Specialist.rating >= filters.rating_min)

        if filters.work_format:
            stmt = stmt.where(Specialist.work_format == filters.work_format)

        stmt = (
            stmt.order_by(
                Specialist.is_premium.desc(),
                Specialist.priority_score.desc(),
                Specialist.rating.desc(),
                Specialist.is_verified.desc(),
                Specialist.reviews_count.desc(),
                Specialist.updated_at.desc(),
                Specialist.id.asc(),
            )
            .offset(filters.normalized_offset)
            .limit(filters.query_limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search_professional_cabinets(
        self,
        filters: SpecialistSearchFilters,
        *,
        tenant_id: UUID,
    ) -> list[
        tuple[
            Specialist,
            ProfessionalCabinet,
        ]
    ]:
        premium_promotion_exists = (
            self._active_cabinet_promotion_exists(
                "premium"
            )
        )
        promotion_priority = (
            self._cabinet_promotion_priority_expression()
        )

        stmt = (
            select(
                Specialist,
                ProfessionalCabinet,
            )
            .join(
                ProfessionalCabinet,
                ProfessionalCabinet.specialist_id
                == Specialist.id,
            )
            .join(
                User,
                User.id == Specialist.user_id,
            )
            .outerjoin(
                ReputationScore,
                and_(
                    ReputationScore.tenant_id
                    == ProfessionalCabinet.tenant_id,
                    ReputationScore.target_type
                    == "professional_cabinet",
                    ReputationScore.target_id
                    == ProfessionalCabinet.id,
                ),
            )
            .where(
                Specialist.tenant_id == tenant_id,
                ProfessionalCabinet.tenant_id
                == tenant_id,
                Specialist.status.in_(
                    PUBLIC_SPECIALIST_STATUSES
                ),
                User.status.notin_(
                    ["blocked", "deleted"]
                ),
                ProfessionalCabinet.is_active.is_(
                    True
                ),
                ProfessionalCabinet.moderation_status.in_(
                    PUBLIC_CABINET_MODERATION_STATUSES
                ),
            )
        )
        if filters.status:
            stmt = stmt.where(
                Specialist.status
                == filters.status
            )
        if filters.city_id:
            stmt = stmt.where(
                ProfessionalCabinet.city_id
                == filters.city_id
            )

        if filters.country_id:
            stmt = stmt.where(
                ProfessionalCabinet.country_id
                == filters.country_id
            )

        if filters.category_id:
            stmt = stmt.where(
                ProfessionalCabinet.category_id
                == filters.category_id
            )

        profession_ids = list(
            filters.profession_ids or []
        )
        if (
            filters.profession_id
            and filters.profession_id
            not in profession_ids
        ):
            profession_ids.append(
                filters.profession_id
            )

        if profession_ids:
            stmt = stmt.where(
                ProfessionalCabinet.profession_id.in_(
                    profession_ids
                )
            )

        if filters.language_code:
            language_exists = exists(
                select(
                    SpecialistLanguage.id
                ).where(
                    SpecialistLanguage.specialist_id
                    == Specialist.id,
                    SpecialistLanguage.language_code
                    == filters.language_code.lower(),
                )
            )
            stmt = stmt.where(language_exists)

        if filters.premium_only:
            stmt = stmt.where(
                premium_promotion_exists
            )

        if filters.verified_only:
            stmt = stmt.where(
                Specialist.is_verified.is_(True)
            )

        if filters.available_only:
            stmt = stmt.where(
                ProfessionalCabinet.availability_status
                == "available"
            )

        if filters.rating_min is not None:
            stmt = stmt.where(
                func.coalesce(
                    ReputationScore.score,
                    0,
                )
                >= filters.rating_min
            )

        if filters.work_format:
            stmt = stmt.where(
                ProfessionalCabinet.work_format
                == filters.work_format
            )

        stmt = (
            stmt.order_by(
                premium_promotion_exists.desc(),
                promotion_priority.desc(),
                func.coalesce(
                    ReputationScore.score,
                    0,
                ).desc(),
                Specialist.is_verified.desc(),
                func.coalesce(
                    ReputationScore.review_count,
                    0,
                ).desc(),
                ProfessionalCabinet.updated_at.desc(),
                ProfessionalCabinet.id.asc(),
            )
            .offset(filters.normalized_offset)
            .limit(filters.query_limit)
        )

        result = await self.session.execute(
            stmt
        )
        return list(result.tuples().all())

    async def list_active_with_coordinates(
        self,
        *,
        category_id: UUID | None = None,
        country_id: UUID | None = None,
        profession_id: UUID | None = None,
        profession_ids: list[UUID] | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        available_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 200,
    ) -> list[Specialist]:
        stmt = (
            select(Specialist)
            .join(User, User.id == Specialist.user_id)
            .where(
                Specialist.status.in_(
    PUBLIC_SPECIALIST_STATUSES
),
                User.status.notin_(["blocked", "deleted"]),
                or_(
                    Specialist.latitude.isnot(None),
                    exists(
                        select(SpecialistLocation.id).where(
                            SpecialistLocation.specialist_id == Specialist.id,
                            SpecialistLocation.is_current.is_(True),
                            SpecialistLocation.latitude.isnot(None),
                            SpecialistLocation.longitude.isnot(None),
                        )
                    ),
                ),
            )
        )

        if country_id:
            current_location_in_country = exists(
                select(SpecialistLocation.id).where(
                    SpecialistLocation.specialist_id == Specialist.id,
                    SpecialistLocation.is_current.is_(True),
                    SpecialistLocation.country_id == country_id,
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.country_id == country_id,
                    current_location_in_country,
                )
            )

        if category_id:
            category_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.category_id == category_id,
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.category_id == category_id,
                    category_exists,
                )
            )

        profession_ids = list(profession_ids or [])
        if profession_id and profession_id not in profession_ids:
            profession_ids.append(profession_id)

        if profession_ids:
            profession_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.profession_id.in_(profession_ids),
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.profession_id.in_(profession_ids),
                    profession_exists,
                )
            )


        if language_code:
            language_exists = exists(
                select(SpecialistLanguage.id).where(
                    SpecialistLanguage.specialist_id == Specialist.id,
                    SpecialistLanguage.language_code == language_code.lower(),
                )
            )
            stmt = stmt.where(language_exists)


        if premium_only:
            stmt = stmt.where(Specialist.is_premium.is_(True))

        if verified_only:
            stmt = stmt.where(Specialist.is_verified.is_(True))

        if available_only:
            stmt = stmt.where(Specialist.is_available.is_(True))

        if rating_min is not None:
            stmt = stmt.where(Specialist.rating >= rating_min)

        if work_format:
            stmt = stmt.where(Specialist.work_format == work_format)

        stmt = stmt.order_by(
            Specialist.is_premium.desc(),
            Specialist.priority_score.desc(),
            Specialist.rating.desc(),
            Specialist.is_verified.desc(),
            Specialist.reviews_count.desc(),
            Specialist.updated_at.desc(),
            Specialist.id.asc(),
        ).limit(max(1, int(limit)))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def search_professional_cabinets_within_radius(
        self,
        *,
        tenant_id: UUID,
        latitude: float,
        longitude: float,
        radius_km: float,
        country_wide: bool = False,
        country_id: UUID | None = None,
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        profession_ids: list[UUID] | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        available_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 200,
    ) -> list[
        tuple[
            Specialist,
            ProfessionalCabinet,
            float | None,
        ]
    ]:
        premium_promotion_exists = (
            self._active_cabinet_promotion_exists(
                "premium"
            )
        )
        promotion_priority = (
            self._cabinet_promotion_priority_expression()
        )

        cabinet_latitude = City.latitude
        cabinet_longitude = City.longitude

        earth_radius_km = 6371.0
        lat1 = func.radians(
            literal(latitude)
        )
        lon1 = func.radians(
            literal(longitude)
        )
        lat2 = func.radians(cabinet_latitude)
        lon2 = func.radians(cabinet_longitude)

        delta_lat = lat2 - lat1
        delta_lon = lon2 - lon1

        haversine = (
            func.pow(
                func.sin(delta_lat / 2),
                2,
            )
            + func.cos(lat1)
            * func.cos(lat2)
            * func.pow(
                func.sin(delta_lon / 2),
                2,
            )
        )

        distance_expr = (
            earth_radius_km
            * 2
            * func.asin(func.sqrt(haversine))
        ).label("distance_km")

        stmt = (
            select(
                Specialist,
                ProfessionalCabinet,
                distance_expr,
            )
            .join(
                ProfessionalCabinet,
                ProfessionalCabinet.specialist_id
                == Specialist.id,
            )
            .join(
                User,
                User.id == Specialist.user_id,
            )
            .outerjoin(
                ReputationScore,
                and_(
                    ReputationScore.tenant_id
                    == ProfessionalCabinet.tenant_id,
                    ReputationScore.target_type
                    == "professional_cabinet",
                    ReputationScore.target_id
                    == ProfessionalCabinet.id,
                ),
            )
            .outerjoin(
                City,
                City.id
                == ProfessionalCabinet.city_id,
            )
            .where(
                Specialist.tenant_id == tenant_id,
                ProfessionalCabinet.tenant_id
                == tenant_id,
                Specialist.status.in_(
                    PUBLIC_SPECIALIST_STATUSES
                ),
                User.status.notin_(
                    ["blocked", "deleted"]
                ),
                ProfessionalCabinet.is_active.is_(
                    True
                ),
                ProfessionalCabinet.moderation_status.in_(
                    PUBLIC_CABINET_MODERATION_STATUSES
                ),
            )
        )

        if country_id:
            stmt = stmt.where(
                ProfessionalCabinet.country_id
                == country_id
            )

        if category_id:
            stmt = stmt.where(
                ProfessionalCabinet.category_id
                == category_id
            )

        selected_profession_ids = list(
            profession_ids or []
        )
        if (
            profession_id
            and profession_id
            not in selected_profession_ids
        ):
            selected_profession_ids.append(
                profession_id
            )

        if selected_profession_ids:
            stmt = stmt.where(
                ProfessionalCabinet.profession_id.in_(
                    selected_profession_ids
                )
            )

        if language_code:
            language_exists = exists(
                select(
                    SpecialistLanguage.id
                ).where(
                    SpecialistLanguage.specialist_id
                    == Specialist.id,
                    SpecialistLanguage.language_code
                    == language_code.lower(),
                )
            )
            stmt = stmt.where(language_exists)

        if premium_only:
            stmt = stmt.where(
                premium_promotion_exists
            )

        if verified_only:
            stmt = stmt.where(
                Specialist.is_verified.is_(True)
            )

        if available_only:
            stmt = stmt.where(
                ProfessionalCabinet.availability_status
                == "available"
            )

        if rating_min is not None:
            stmt = stmt.where(
                func.coalesce(
                    ReputationScore.score,
                    0,
                )
                >= rating_min
            )

        if work_format:
            stmt = stmt.where(
                ProfessionalCabinet.work_format
                == work_format
            )

        if not country_wide:
            stmt = stmt.where(
                cabinet_latitude.isnot(None),
                cabinet_longitude.isnot(None),
                distance_expr <= radius_km,
            )

        stmt = (
            stmt.order_by(
                premium_promotion_exists.desc(),
                promotion_priority.desc(),
                distance_expr.asc().nulls_last(),
                func.coalesce(
                    ReputationScore.score,
                    0,
                ).desc(),
                Specialist.is_verified.desc(),
                func.coalesce(
                    ReputationScore.review_count,
                    0,
                ).desc(),
                ProfessionalCabinet.updated_at.desc(),
                ProfessionalCabinet.id.asc(),
            )
            .limit(max(1, int(limit)))
        )

        result = await self.session.execute(
            stmt
        )

        return [
            (
                specialist,
                cabinet,
                (
                    float(distance)
                    if distance is not None
                    else None
                ),
            )
            for specialist, cabinet, distance
            in result.all()
        ]

    async def search_within_radius(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float,
        country_wide: bool = False,
        country_id: UUID | None = None,
        category_id: UUID | None = None,
        profession_id: UUID | None = None,
        profession_ids: list[UUID] | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        available_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 200,
    ) -> list[tuple[Specialist, float | None]]:
        distance_expr = self._distance_km_expression(
            latitude=latitude,
            longitude=longitude,
        ).label("distance_km")

        stmt = (
            select(Specialist, distance_expr)
            .join(User, User.id == Specialist.user_id)
            .outerjoin(
                SpecialistLocation,
                (SpecialistLocation.specialist_id == Specialist.id)
                & (SpecialistLocation.is_current.is_(True)),
            )
            .where(
                Specialist.status.in_(
    PUBLIC_SPECIALIST_STATUSES
),
                User.status.notin_(["blocked", "deleted"]),
)
        )

        if country_id:
            stmt = stmt.where(
                or_(
                    Specialist.country_id == country_id,
                    SpecialistLocation.country_id == country_id,
                )
            )

        if category_id:
            category_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.category_id == category_id,
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.category_id == category_id,
                    category_exists,
                )
            )

        profession_ids = list(profession_ids or [])
        if profession_id and profession_id not in profession_ids:
            profession_ids.append(profession_id)

        if profession_ids:
            profession_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.profession_id.in_(profession_ids),
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.profession_id.in_(profession_ids),
                    profession_exists,
                )
            )

        if language_code:
            language_exists = exists(
                select(SpecialistLanguage.id).where(
                    SpecialistLanguage.specialist_id == Specialist.id,
                    SpecialistLanguage.language_code == language_code.lower(),
                )
            )
            stmt = stmt.where(language_exists)

        if premium_only:
            stmt = stmt.where(Specialist.is_premium.is_(True))

        if verified_only:
            stmt = stmt.where(Specialist.is_verified.is_(True))

        if available_only:
            stmt = stmt.where(Specialist.is_available.is_(True))

        if rating_min is not None:
            stmt = stmt.where(Specialist.rating >= rating_min)

        if work_format:
            stmt = stmt.where(Specialist.work_format == work_format)

        if not country_wide:
            stmt = stmt.where(
        or_(
            Specialist.latitude.isnot(None),
            SpecialistLocation.latitude.isnot(None),
        ),
        or_(
            Specialist.longitude.isnot(None),
            SpecialistLocation.longitude.isnot(None),
        ),
        distance_expr <= radius_km,
    )

        stmt = stmt.order_by(
            Specialist.is_premium.desc(),
            Specialist.priority_score.desc(),
            distance_expr.asc().nulls_last(),
            Specialist.rating.desc(),
            Specialist.is_verified.desc(),
            Specialist.reviews_count.desc(),
            Specialist.updated_at.desc(),
            Specialist.id.asc(),
        ).limit(max(1, int(limit)))

        result = await self.session.execute(stmt)

        return [
    (
        specialist,
        float(distance) if distance is not None else None,
    )
    for specialist, distance in result.all()
]

    async def get_current_locations_by_specialist_ids(
        self,
        specialist_ids: list[UUID],
    ) -> dict[UUID, SpecialistLocation]:
        if not specialist_ids:
            return {}

        result = await self.session.execute(
            select(SpecialistLocation).where(
                SpecialistLocation.specialist_id.in_(specialist_ids),
                SpecialistLocation.is_current.is_(True),
                SpecialistLocation.latitude.isnot(None),
                SpecialistLocation.longitude.isnot(None),
            )
        )

        locations = result.scalars().all()
        return {
            location.specialist_id: location
            for location in locations
        }

    async def get_user_metrics_by_specialist_ids(
        self,
        specialist_ids: list[UUID],
    ) -> dict[UUID, dict]:
        if not specialist_ids:
            return {}

        result = await self.session.execute(
            select(
                Specialist.id,
                User.profile_completion_score,
                User.risk_score,
            )
            .join(User, User.id == Specialist.user_id)
            .where(Specialist.id.in_(specialist_ids))
        )

        return {
            specialist_id: {
                "profile_completion_score": profile_completion_score or 0,
                "risk_score": risk_score or 0,
            }
            for specialist_id, profile_completion_score, risk_score in result.all()
        }
