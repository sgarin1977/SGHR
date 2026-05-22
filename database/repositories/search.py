from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    EventLog,
    Specialist,
    SpecialistLanguage,
    SpecialistLocation,
    User,
    City,
)


@dataclass
class SpecialistSearchFilters:
    category_id: UUID | None = None
    profession_id: UUID | None = None
    city_id: UUID | None = None
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
    page_size: int = 10
    limit: int | None = None
    offset: int | None = None

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
            stmt = stmt.where(
                or_(
                    Specialist.city_id == filters.city_id,
                    current_location_in_city,
                )
            )

        if filters.category_id:
            stmt = stmt.where(Specialist.category_id == filters.category_id)

        if filters.profession_id:
            stmt = stmt.where(Specialist.profession_id == filters.profession_id)

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

        if filters.verified_only:
            stmt = stmt.where(Specialist.is_verified.is_(True))

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
                Specialist.created_at.desc(),
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

        if category_id:
            stmt = stmt.where(Specialist.category_id == category_id)

        if profession_id:
            stmt = stmt.where(Specialist.profession_id == profession_id)

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

        if verified_only:
            stmt = stmt.where(Specialist.is_verified.is_(True))

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
            Specialist.created_at.desc(),
        ).limit(max(1, int(limit)))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
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