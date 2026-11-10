"""Canonical Platform tenant metadata; authorization and commit belong to service."""
from uuid import UUID

from sqlalchemy import (
    BigInteger, Column, DateTime, MetaData, Table, Text, Uuid,
    bindparam, func, select, true, update,
)
from sqlalchemy.dialects.postgresql import JSONB, array
from database.models import Country

REGION_KEY = "regional_settings"

REGION_TENANTS = Table(
    "tenants", MetaData(),
    Column("id", Uuid, primary_key=True),
    Column("status", Text),
    Column("metadata", JSONB),
    Column("updated_at", DateTime(timezone=True)),
    Column("row_version", BigInteger),
)


class PlatformOrganizationRegionRepository:
    def __init__(self, session):
        self.session = session
        self.table = REGION_TENANTS

    def _columns(self):
        c = self.table.c
        return (c.id.label("tenant_id"), c.metadata, c.updated_at, c.row_version)

    async def get_active(self, *, tenant_id, for_update=False):
        if not isinstance(tenant_id, UUID):
            raise ValueError("Canonical tenant UUID is required.")
        query = select(*self._columns()).where(
            self.table.c.id == tenant_id, self.table.c.status == "active")
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(
            query.execution_options(autoflush=False))).mappings().one_or_none()
        return None if row is None else dict(row)

    async def country_is_active(self, *, country_code, for_share=False):
        query = select(Country.code).where(
            Country.code == country_code, Country.is_active.is_(True)).limit(2)
        if for_share:
            query = query.with_for_update(read=True)
        codes = (await self.session.execute(
            query.execution_options(autoflush=False))).scalars().all()
        if len(codes) > 1:
            raise RuntimeError("Platform country catalog is ambiguous.")
        return codes == [country_code]

    def _metadata_value(self, region):
        # Only the selected subtree is serialized. Unrelated JSONB, including
        # numeric precision, is preserved by PostgreSQL itself.
        return func.jsonb_set(
            self.table.c.metadata, array([REGION_KEY], type_=Text),
            bindparam("region_value", value=dict(region), type_=JSONB), true())

    async def update_region(self, *, tenant_id, expected_row_version, region, as_of):
        if set(region) != {"country_code", "timezone"}:
            raise ValueError("Explicit country and timezone are required.")
        query = update(self.table).where(
            self.table.c.id == tenant_id,
            self.table.c.status == "active",
            self.table.c.row_version == expected_row_version,
            func.jsonb_typeof(self.table.c.metadata) == "object",
        ).values(
            metadata=self._metadata_value(region),
            updated_at=as_of, row_version=expected_row_version + 1,
        ).returning(*self._columns())
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else dict(row)
