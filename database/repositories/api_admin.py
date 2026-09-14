from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Review
from database.models import (
    ProfessionalCabinet,
    Specialist,
    User,
)
from database.repositories.admin_scope import (
    AdminScopeContext,
)


class AdminApiRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def list_users(
        self,
        *,
        scope_context: AdminScopeContext,
        limit: int,
        offset: int,
    ) -> list:
        statement = (
            select(User)
            .where(
                User.tenant_id
                == scope_context.tenant_id,
                scope_context.sql_predicate(
                    country_column=User.country_id,
                    language_column=(
                        User.language_code
                    ),
                ),
            )
            .order_by(
                User.created_at.desc(),
                User.id.desc(),
            )
            .limit(
                max(1, min(int(limit), 101))
            )
            .offset(max(0, int(offset)))
        )

        result = await self.session.execute(
            statement
        )
        return list(result.all())


    async def get_user(
        self,
        *,
        scope_context: AdminScopeContext,
        user_id,
    ):
        statement = (
            select(User)
            .where(
                User.id == user_id,
                User.tenant_id
                == scope_context.tenant_id,
                scope_context.sql_predicate(
                    country_column=User.country_id,
                    language_column=(
                        User.language_code
                    ),
                ),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()


    async def list_specialists(
        self,
        *,
        scope_context: AdminScopeContext,
        limit: int,
        offset: int,
    ) -> list:
        effective_country_id = (
            func.coalesce(
                Specialist.country_id,
                ProfessionalCabinet.country_id,
                User.country_id,
            )
        )

        statement = (
            select(
                Specialist,
                ProfessionalCabinet,
            )
            .join(
                User,
                and_(
                    User.id
                    == Specialist.user_id,
                    User.tenant_id
                    == scope_context.tenant_id,
                ),
            )
            .outerjoin(
                ProfessionalCabinet,
                and_(
                    ProfessionalCabinet.id
                    == Specialist
                    .active_professional_cabinet_id,
                    ProfessionalCabinet.tenant_id
                    == scope_context.tenant_id,
                ),
            )
            .where(
                Specialist.tenant_id
                == scope_context.tenant_id,
                scope_context.sql_predicate(
                    country_column=(
                        effective_country_id
                    ),
                    language_column=(
                        User.language_code
                    ),
                ),
            )
            .order_by(
                Specialist.created_at.desc(),
                Specialist.id.desc(),
            )
            .limit(
                max(1, min(int(limit), 101))
            )
            .offset(max(0, int(offset)))
        )

        result = await self.session.execute(
            statement
        )
        return list(result.all())


    async def get_specialist(
        self,
        *,
        scope_context: AdminScopeContext,
        specialist_id,
    ):
        effective_country_id = (
            func.coalesce(
                Specialist.country_id,
                ProfessionalCabinet.country_id,
                User.country_id,
            )
        )

        statement = (
            select(
                Specialist,
                ProfessionalCabinet,
            )
            .join(
                User,
                and_(
                    User.id
                    == Specialist.user_id,
                    User.tenant_id
                    == scope_context.tenant_id,
                ),
            )
            .outerjoin(
                ProfessionalCabinet,
                and_(
                    ProfessionalCabinet.id
                    == Specialist
                    .active_professional_cabinet_id,
                    ProfessionalCabinet.tenant_id
                    == scope_context.tenant_id,
                ),
            )
            .where(
                Specialist.id
                == specialist_id,
                Specialist.tenant_id
                == scope_context.tenant_id,
                scope_context.sql_predicate(
                    country_column=(
                        effective_country_id
                    ),
                    language_column=(
                        User.language_code
                    ),
                ),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.one_or_none()


    async def list_reviews(
        self,
        *,
        scope_context: AdminScopeContext,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list:
        effective_country_id = (
            func.coalesce(
                Specialist.country_id,
                ProfessionalCabinet.country_id,
                User.country_id,
            )
        )

        conditions = [
            Review.tenant_id
            == scope_context.tenant_id,
            ProfessionalCabinet.tenant_id
            == scope_context.tenant_id,
            Specialist.tenant_id
            == scope_context.tenant_id,
            User.tenant_id
            == scope_context.tenant_id,
            scope_context.sql_predicate(
                country_column=(
                    effective_country_id
                ),
                language_column=(
                    User.language_code
                ),
            ),
        ]

        if status is not None:
            conditions.append(
                Review.status == status
            )

        statement = (
            select(
                Review,
                ProfessionalCabinet,
                Specialist,
            )
            .select_from(Review)
            .join(
                ProfessionalCabinet,
                ProfessionalCabinet.id
                == Review
                .professional_cabinet_id,
            )
            .join(
                Specialist,
                Specialist.id
                == ProfessionalCabinet
                .specialist_id,
            )
            .join(
                User,
                User.id == Specialist.user_id,
            )
            .where(*conditions)
            .order_by(
                Review.created_at.desc(),
                Review.id.desc(),
            )
            .limit(
                max(1, min(int(limit), 101))
            )
            .offset(max(0, int(offset)))
        )

        result = await self.session.execute(
            statement
        )
        return list(result.all())
