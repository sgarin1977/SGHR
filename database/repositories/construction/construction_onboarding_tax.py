"""Explicit onboarding tax-profile selection in canonical tenant settings."""
from sqlalchemy import select, update
from database.models import ConstructionTenantSetting


class ConstructionOnboardingTaxRepository:
    def __init__(self, session):
        self.session = session
        self.table = ConstructionTenantSetting.__table__

    def _columns(self):
        return tuple(self.table.c[name] for name in (
            "tenant_id", "row_version", "onboarding_tax_profile_id",
            "created_at", "updated_at",
        ))

    async def get_selection(self, *, tenant_id, for_update=False, for_share=False):
        if for_update and for_share:
            raise ValueError("Only one settings lock mode is allowed.")
        query = select(*self._columns()).where(self.table.c.tenant_id == tenant_id)
        if for_update or for_share:
            query = query.with_for_update(read=for_share)
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else dict(row)

    async def set_profile(
        self, *, tenant_id, expected_row_version,
        previous_profile_id, tax_profile_id, as_of,
    ):
        previous = (
            self.table.c.onboarding_tax_profile_id.is_(None)
            if previous_profile_id is None
            else self.table.c.onboarding_tax_profile_id == previous_profile_id
        )
        row = (await self.session.execute(
            update(self.table).where(
                self.table.c.tenant_id == tenant_id,
                self.table.c.row_version == expected_row_version,
                previous,
            ).values(
                onboarding_tax_profile_id=tax_profile_id,
                row_version=self.table.c.row_version + 1,
                updated_at=as_of,
            ).returning(*self._columns())
        )).mappings().one_or_none()
        return None if row is None else dict(row)

# onboarding_explicit_tax_profile_v1
