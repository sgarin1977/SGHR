"""Tenant/profile projections and versioned SQL; caller owns authorization/audit."""
from sqlalchemy import insert, select, update

from database.models import ConstructionTaxProfile, ConstructionTaxRule


class ConstructionTaxRuleRepository:
    def __init__(self, session):
        self.session = session
        self.table = ConstructionTaxRule.__table__
        self.profiles = ConstructionTaxProfile.__table__

    async def get_profile_id(self, *, tenant_id, profile_id, for_update=False):
        statement = select(self.profiles.c.id).where(
            self.profiles.c.tenant_id == tenant_id,
            self.profiles.c.id == profile_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_rule(self, *, tenant_id, profile_id, rule_id, for_update=False):
        statement = select(self.table).where(
            self.table.c.tenant_id == tenant_id,
            self.table.c.tax_profile_id == profile_id,
            self.table.c.id == rule_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self.session.execute(statement)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def get_rule_id_by_category(self, *, tenant_id, profile_id, category):
        statement = select(self.table.c.id).where(
            self.table.c.tenant_id == tenant_id,
            self.table.c.tax_profile_id == profile_id,
            self.table.c.category == category,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def create_rule(
        self, *, tenant_id, profile_id, rule_id, created_by, fields, as_of,
    ):
        if set(fields) != {"category", "rate_percent"}:
            raise ValueError("Category and rate are required.")
        row = (await self.session.execute(insert(self.table).values(
            id=rule_id, tenant_id=tenant_id, tax_profile_id=profile_id,
            created_by=created_by, category=fields["category"],
            rate_percent=fields["rate_percent"],
            row_version=1, created_at=as_of, updated_at=as_of,
        ).returning(*self.table.c))).mappings().one()
        return dict(row)

    async def update_rule(
        self, *, tenant_id, profile_id, rule_id,
        expected_row_version, fields, as_of,
    ):
        if not fields or not set(fields).issubset({"category", "rate_percent"}):
            raise ValueError("Only explicit category and rate changes are allowed.")
        row = (await self.session.execute(update(self.table).where(
            self.table.c.tenant_id == tenant_id,
            self.table.c.tax_profile_id == profile_id,
            self.table.c.id == rule_id,
            self.table.c.row_version == expected_row_version,
        ).values(
            **fields,
            row_version=self.table.c.row_version + 1,
            updated_at=as_of,
        ).returning(*self.table.c))).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def list_rules(self, *, tenant_id, profile_id, limit, after_id=None):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Tax rule query must be bounded.")
        statement = select(self.table).where(
            self.table.c.tenant_id == tenant_id,
            self.table.c.tax_profile_id == profile_id,
        )
        if after_id is not None:
            statement = statement.where(self.table.c.id > after_id)
        rows = (await self.session.execute(
            statement.order_by(self.table.c.id.asc()).limit(limit)
        )).mappings().all()
        return tuple(dict(row) for row in rows)

# con016_canonical_categories_unique_v1
