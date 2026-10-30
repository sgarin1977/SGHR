from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ConstructionAccessGrant


class ConstructionTeamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active_grants(
        self,
        *,
        tenant_id: UUID,
        as_of: datetime,
        project_id: UUID | None = None,
    ) -> list[ConstructionAccessGrant]:
        filters = [
            ConstructionAccessGrant.tenant_id == tenant_id,
            ConstructionAccessGrant.status == "active",
            or_(
                ConstructionAccessGrant.expires_at.is_(None),
                ConstructionAccessGrant.expires_at > as_of,
            ),
        ]
        if project_id is not None:
            filters.append(
                or_(
                    and_(
                        ConstructionAccessGrant.scope_type == "project",
                        ConstructionAccessGrant.scope_id == project_id,
                    ),
                    and_(
                        ConstructionAccessGrant.scope_type == "tenant",
                        ConstructionAccessGrant.scope_id == tenant_id,
                    ),
                )
            )

        statement = (
            select(ConstructionAccessGrant)
            .where(*filters)
            .order_by(
                ConstructionAccessGrant.user_id,
                ConstructionAccessGrant.scope_type,
                ConstructionAccessGrant.scope_id,
                ConstructionAccessGrant.role,
                ConstructionAccessGrant.id,
            )
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
