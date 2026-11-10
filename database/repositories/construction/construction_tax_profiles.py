"""Exact tenant projections and conditional updates; caller owns authorization/audit."""
from sqlalchemy import insert, select, update

from database.models import ConstructionTaxProfile


class ConstructionTaxProfileRepository:
    def __init__(self, session):
        self.session = session
        self.table = ConstructionTaxProfile.__table__

    async def get_profile(self, *, tenant_id, profile_id, for_update=False, for_share=False):
        if for_update and for_share:
            raise ValueError("Only one profile lock mode is allowed.")
        statement = select(self.table).where(
            self.table.c.tenant_id == tenant_id, self.table.c.id == profile_id,
        )
        if for_update or for_share:
            statement = statement.with_for_update(read=for_share)
        row = (await self.session.execute(statement)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def create_profile(self, *, tenant_id, profile_id, created_by, fields, as_of):
        row = (await self.session.execute(insert(self.table).values(
            id=profile_id, tenant_id=tenant_id, created_by=created_by,
            name=fields["name"], client_price_tax_mode=fields["client_price_tax_mode"],
            material_cost_basis=fields["material_cost_basis"],
            row_version=1, created_at=as_of, updated_at=as_of,
        ).returning(*self.table.c))).mappings().one()
        return dict(row)

    async def update_profile(self, *, tenant_id, profile_id, expected_row_version, fields, as_of):
        if not fields or not set(fields).issubset({"name", "client_price_tax_mode", "material_cost_basis"}):
            raise ValueError("Only explicit tax profile settings can be updated.")
        row = (await self.session.execute(update(self.table).where(
            self.table.c.tenant_id == tenant_id, self.table.c.id == profile_id,
            self.table.c.row_version == expected_row_version,
        ).values(**fields, row_version=self.table.c.row_version + 1, updated_at=as_of)
            .returning(*self.table.c))).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def list_profiles(self, *, tenant_id, limit, after_id=None):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Tax profile query must be bounded.")
        query = select(self.table).where(self.table.c.tenant_id == tenant_id)
        if after_id is not None:
            query = query.where(self.table.c.id > after_id)
        rows = (await self.session.execute(
            query.order_by(self.table.c.id.asc()).limit(limit)
        )).mappings().all()
        return tuple(dict(row) for row in rows)
