"""Project-bound projections and conditional SQL; caller owns authorization/audit."""
from sqlalchemy import insert, select, update
from database.models import ConstructionProject, ConstructionProjectPricingAdjustment


class ConstructionProjectPricingAdjustmentRepository:
    def __init__(self, session):
        self.session = session
        self.table = ConstructionProjectPricingAdjustment.__table__
        self.projects = ConstructionProject.__table__

    async def get_project(self, *, tenant_id, project_id, for_update=False):
        query = select(
            self.projects.c.id, self.projects.c.tenant_id,
            self.projects.c.status, self.projects.c.deleted_at,
        ).where(self.projects.c.tenant_id == tenant_id, self.projects.c.id == project_id,
                self.projects.c.deleted_at.is_(None))
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def get_adjustment(self, *, tenant_id, project_id, adjustment_id, for_update=False):
        query = select(self.table).where(
            self.table.c.tenant_id == tenant_id, self.table.c.project_id == project_id,
            self.table.c.id == adjustment_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def list_adjustments(self, *, tenant_id, project_id, limit, after_id=None):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Adjustment query must be bounded.")
        query = select(self.table).where(
            self.table.c.tenant_id == tenant_id, self.table.c.project_id == project_id)
        if after_id is not None:
            query = query.where(self.table.c.id > after_id)
        rows = (await self.session.execute(
            query.order_by(self.table.c.id.asc()).limit(limit))).mappings().all()
        return tuple(dict(row) for row in rows)

    async def create_adjustment(self, *, tenant_id, project_id, adjustment_id, created_by, fields, as_of):
        if set(fields) != {"name", "coefficient", "enabled"}:
            raise ValueError("Name, coefficient and enabled must be explicit.")
        row = (await self.session.execute(insert(self.table).values(
            id=adjustment_id, tenant_id=tenant_id, project_id=project_id, created_by=created_by,
            **fields, row_version=1, created_at=as_of, updated_at=as_of
        ).returning(*self.table.c))).mappings().one()
        return dict(row)

    async def update_adjustment(self, *, tenant_id, project_id, adjustment_id, expected_row_version, fields, as_of):
        if not fields or not set(fields).issubset({"name", "coefficient", "enabled"}):
            raise ValueError("Only explicit adjustment fields can change.")
        row = (await self.session.execute(update(self.table).where(
            self.table.c.tenant_id == tenant_id, self.table.c.project_id == project_id,
            self.table.c.id == adjustment_id, self.table.c.row_version == expected_row_version
        ).values(**fields, row_version=self.table.c.row_version + 1, updated_at=as_of)
            .returning(*self.table.c))).mappings().one_or_none()
        return dict(row) if row is not None else None
