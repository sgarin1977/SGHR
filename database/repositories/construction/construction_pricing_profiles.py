"""Tenant-filtered pricing storage. The service owns authorization and audit."""
from sqlalchemy import case, insert, select, update
from database.models import ConstructionPricingProfile


class ConstructionPricingProfileRepository:
    def __init__(self, session):
        self.session = session
        self.table = ConstructionPricingProfile.__table__

    async def get_profile(self, *, tenant_id, profile_id, for_update=False):
        query = select(self.table).where(
            self.table.c.tenant_id == tenant_id, self.table.c.id == profile_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def get_profile_by_code(self, *, tenant_id, code):
        row = (await self.session.execute(select(self.table).where(
            self.table.c.tenant_id == tenant_id, self.table.c.code == code
        ))).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def list_profiles(self, *, tenant_id):
        order = case(
            {"ECONOMY": 0, "STANDARD": 1, "PREMIUM": 2},
            value=self.table.c.code, else_=3)
        rows = (await self.session.execute(
            select(self.table).where(self.table.c.tenant_id == tenant_id)
            .order_by(order, self.table.c.id).limit(4)
        )).mappings().all()
        return tuple(dict(row) for row in rows)

    async def create_profile(self, *, tenant_id, profile_id, created_by, fields, as_of):
        if set(fields) != {"code", "work_coefficient", "material_markup_percent"}:
            raise ValueError("Explicit pricing level and both commercial values are required.")
        row = (await self.session.execute(insert(self.table).values(
            id=profile_id, tenant_id=tenant_id, created_by=created_by, **fields,
            row_version=1, created_at=as_of, updated_at=as_of
        ).returning(*self.table.c))).mappings().one()
        return dict(row)

    async def update_profile(self, *, tenant_id, profile_id, expected_row_version, fields, as_of):
        if not fields or not set(fields).issubset({"work_coefficient", "material_markup_percent"}):
            raise ValueError("Only commercial values can be updated.")
        row = (await self.session.execute(update(self.table).where(
            self.table.c.tenant_id == tenant_id, self.table.c.id == profile_id,
            self.table.c.row_version == expected_row_version
        ).values(**fields, row_version=self.table.c.row_version + 1, updated_at=as_of)
            .returning(*self.table.c))).mappings().one_or_none()
        return dict(row) if row is not None else None
