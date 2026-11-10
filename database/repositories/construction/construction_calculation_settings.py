"""Tenant-filtered CON-019 storage; authorization belongs to the service."""
from sqlalchemy import insert, select, update
from database.models import ConstructionCalculationSetting

FIELDS = (
    "money_input_precision", "quantity_precision",
    "minimum_job_amount", "minimum_job_currency",
    "supplier_stale_price_days",
    "default_technological_waste_percent", "default_local_repair_percent",
)


class ConstructionCalculationSettingsRepository:
    def __init__(self, session):
        self.session = session
        self.table = ConstructionCalculationSetting.__table__

    async def get_settings(self, *, tenant_id, for_update=False):
        query = select(self.table).where(self.table.c.tenant_id == tenant_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def create_settings(self, *, tenant_id, created_by, fields, as_of):
        if set(fields) != set(FIELDS):
            raise ValueError("Explicit normalized calculation fields are required.")
        row = (await self.session.execute(
            insert(self.table).values(
                tenant_id=tenant_id, created_by=created_by, **fields,
                row_version=1, created_at=as_of, updated_at=as_of,
            ).returning(*self.table.c)
        )).mappings().one()
        return dict(row)

    async def update_settings(
        self, *, tenant_id, expected_row_version, fields, as_of,
    ):
        if not fields or not set(fields).issubset(FIELDS):
            raise ValueError("Only calculation configuration can be updated.")
        row = (await self.session.execute(
            update(self.table).where(
                self.table.c.tenant_id == tenant_id,
                self.table.c.row_version == expected_row_version,
            ).values(
                **fields, row_version=self.table.c.row_version + 1,
                updated_at=as_of,
            ).returning(*self.table.c)
        )).mappings().one_or_none()
        return dict(row) if row is not None else None
