"""Project catalog query; caller supplies the authorized scope."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select

from database.models import ConstructionProject


@dataclass(frozen=True)
class ConstructionProjectCatalogCursor:
    tenant_id: UUID
    updated_at: datetime
    project_id: UUID

    def __post_init__(self):
        if (
            not isinstance(self.tenant_id, UUID)
            or not isinstance(self.project_id, UUID)
            or not isinstance(self.updated_at, datetime)
            or self.updated_at.tzinfo is None
            or self.updated_at.utcoffset() is None
        ):
            raise ValueError("Project catalog cursor requires UUIDs and an aware timestamp.")
        object.__setattr__(
            self, "updated_at", self.updated_at.astimezone(timezone.utc),
        )


class ConstructionProjectCatalogRepository:
    def __init__(self, session):
        if session is None or not callable(getattr(session, "execute", None)):
            raise ValueError("Project catalog requires a database session.")
        self.session = session

    async def list_projects(
        self, *, tenant_id, allowed_project_ids, limit=20, after=None,
    ):
        if not isinstance(tenant_id, UUID) or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid project catalog query.")
        if after is not None:
            if not isinstance(after, ConstructionProjectCatalogCursor):
                raise ValueError("Invalid project catalog cursor.")
            after = ConstructionProjectCatalogCursor(
                after.tenant_id, after.updated_at, after.project_id,
            )
            if after.tenant_id != tenant_id:
                raise ValueError("Project catalog cursor belongs to another tenant.")
        if allowed_project_ids is not None:
            if (
                not isinstance(allowed_project_ids, tuple)
                or any(not isinstance(value, UUID) for value in allowed_project_ids)
            ):
                raise ValueError("Authorized project identifiers must be a UUID tuple.")
            if not allowed_project_ids:
                return []

        model = ConstructionProject
        conditions = [
            model.tenant_id == tenant_id,
            model.deleted_at.is_(None),
        ]
        if allowed_project_ids is not None:
            conditions.append(model.id.in_(allowed_project_ids))
        if after is not None:
            conditions.append(or_(
                model.updated_at < after.updated_at,
                and_(
                    model.updated_at == after.updated_at,
                    model.id < after.project_id,
                ),
            ))
        statement = (
            select(model).where(*conditions)
            .order_by(model.updated_at.desc(), model.id.desc())
            .limit(limit + 1)
            .execution_options(populate_existing=True, autoflush=False)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
