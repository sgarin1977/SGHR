from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    EventLog,
    Specialist,
    SpecialistCategory,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistProfession,
    SpecialistService,
    Profession,
    User,
    City,
)


@dataclass
class SpecialistSearchFilters:
    category_id: UUID | None = None
    profession_id: UUID | None = None
    city_id: UUID | None = None
    country_id: UUID | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = 25
    price_min: float | None = None
    price_max: float | None = None
    language_code: str | None = None
    verified_only: bool = False
    premium_only: bool = False
    rating_min: float | None = None
    work_format: str | None = None
    status: str = "active"
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

    async def get_active_specialist_for_card(
        self,
        specialist_id: UUID,
    ) -> Specialist | None:
        result = await self.session.execute(
            select(Specialist)
            .join(User, User.id == Specialist.user_id)
            .where(
                Specialist.id == specialist_id,
                Specialist.status == "active",
                User.status.notin_(["blocked", "deleted"]),
            )
        )
        return result.scalar_one_or_none()
    
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
        limit: int = 5,
    ) -> list[str]:
        result = await self.session.execute(
            select(SpecialistService.title)
            .where(
                SpecialistService.specialist_id == specialist_id,
                SpecialistService.status == "active",
            )
            .order_by(SpecialistService.created_at.desc())
            .limit(max(1, int(limit)))
        )
        return [title for title in result.scalars().all() if title]

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
                    "price_min": filters.price_min,
                    "price_max": filters.price_max,
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

        if filters.profession_id:
            profession_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.profession_id == filters.profession_id,
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.profession_id == filters.profession_id,
                    profession_exists,
                )
            )

        if filters.price_min is not None:
            stmt = stmt.where(
                or_(
                    Specialist.price_to.is_(None),
                    Specialist.price_to >= filters.price_min,
                )
            )

        if filters.price_max is not None:
            stmt = stmt.where(
                or_(
                    Specialist.price_from.is_(None),
                    Specialist.price_from <= filters.price_max,
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

    async def list_active_with_coordinates(
        self,
        *,
        category_id: UUID | None = None,
        country_id: UUID | None = None,
        profession_id: UUID | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
        rating_min: float | None = None,
        work_format: str | None = None,
        limit: int = 200,
    ) -> list[Specialist]:
        stmt = (
            select(Specialist)
            .join(User, User.id == Specialist.user_id)
            .where(
                Specialist.status == "active",
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

        if profession_id:
            profession_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.profession_id == profession_id,
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.profession_id == profession_id,
                    profession_exists,
                )
            )

        if price_min is not None:
            stmt = stmt.where(
                or_(
                    Specialist.price_to.is_(None),
                    Specialist.price_to >= price_min,
                )
            )

        if price_max is not None:
            stmt = stmt.where(
                or_(
                    Specialist.price_from.is_(None),
                    Specialist.price_from <= price_max,
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
        price_min: float | None = None,
        price_max: float | None = None,
        language_code: str | None = None,
        verified_only: bool = False,
        premium_only: bool = False,
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
                Specialist.status == "active",
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

        if profession_id:
            profession_exists = exists(
                select(SpecialistProfession.id).where(
                    SpecialistProfession.specialist_id == Specialist.id,
                    SpecialistProfession.profession_id == profession_id,
                    SpecialistProfession.status == "active",
                )
            )
            stmt = stmt.where(
                or_(
                    Specialist.profession_id == profession_id,
                    profession_exists,
                )
            )

        if price_min is not None:
            stmt = stmt.where(
                or_(
                    Specialist.price_to.is_(None),
                    Specialist.price_to >= price_min,
                )
            )

        if price_max is not None:
            stmt = stmt.where(
                or_(
                    Specialist.price_from.is_(None),
                    Specialist.price_from <= price_max,
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
