"""Read the current organization currency using the operation's RLS session."""
from uuid import UUID

from sqlalchemy import select

from database.models import Tenant


class ConstructionProjectCurrencyRepository:
    def __init__(self, session):
        self.session = session

    async def get_for_tenant(self, tenant_id: UUID) -> str | None:
        if not isinstance(tenant_id, UUID):
            raise ValueError("Verified project tenant is required.")
        # Only granted tenant columns; no Platform row lock or settings write.
        statement = (
            select(Tenant.default_currency)
            .where(Tenant.id == tenant_id, Tenant.status == "active")
            .execution_options(autoflush=False)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
