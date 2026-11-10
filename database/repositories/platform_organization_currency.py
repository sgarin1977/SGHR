"""Narrow projection of existing organization currency; no schema or commit."""
from uuid import UUID
from sqlalchemy import select, update
from database.repositories.platform_organization_version import TENANT_SETTINGS


class PlatformOrganizationCurrencyRepository:
    def __init__(self, session):
        self.session = session

    async def get_active(self, *, tenant_id, for_update=False):
        if not isinstance(tenant_id, UUID):
            raise ValueError("Canonical organization UUID is required.")

        table = TENANT_SETTINGS.c
        statement = select(
            table.id.label("tenant_id"),
            table.default_currency,
            table.updated_at,
            table.row_version,
        ).where(
            table.id == tenant_id,
            table.status == "active",
        )
        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement.execution_options(autoflush=False)
        )
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)

    async def replace_default(
        self, *, tenant_id, previous_currency,
        expected_row_version, currency_code, as_of,
    ):
        table = TENANT_SETTINGS.c
        result = await self.session.execute(
            update(TENANT_SETTINGS).where(
                table.id == tenant_id,
                table.status == "active",
                table.row_version == expected_row_version,
                table.default_currency == previous_currency,
            ).values(
                default_currency=currency_code,
                updated_at=as_of,
                row_version=expected_row_version + 1,
            ).returning(
                table.id.label("tenant_id"),
                table.default_currency,
                table.updated_at,
                table.row_version,
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)

# con_012_currency_settings_v1
