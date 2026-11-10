"""CON-O001 progress in canonical Construction settings; no branding updates."""
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from database.models import ConstructionTenantSetting


class ConstructionOnboardingRepository:
    def __init__(self, session):
        self.session = session
        self.table = ConstructionTenantSetting.__table__

    def _insert(self):
        return insert(self.table)

    def _columns(self):
        return tuple(self.table.c[name] for name in (
            "tenant_id", "row_version", "onboarding_current_step",
            "onboarding_step_states", "created_at", "updated_at"))

    async def get_settings(self, *, tenant_id, for_update=False):
        statement = select(*self._columns()).where(self.table.c.tenant_id == tenant_id)
        if for_update:
            statement = statement.with_for_update()
        row = (await self.session.execute(statement)).mappings().one_or_none()
        return None if row is None else dict(row)

    async def create_progress(self, *, tenant_id, current_step, step_states, as_of):
        statement = self._insert().values(
            tenant_id=tenant_id, onboarding_current_step=current_step,
            onboarding_step_states=dict(step_states), row_version=1,
            created_at=as_of, updated_at=as_of
        ).on_conflict_do_nothing(index_elements=[self.table.c.tenant_id]).returning(*self._columns())
        row = (await self.session.execute(statement)).mappings().one_or_none()
        return None if row is None else dict(row)

    async def update_progress(self, *, tenant_id, expected_row_version, current_step, step_states, as_of):
        statement = update(self.table).where(
            self.table.c.tenant_id == tenant_id,
            self.table.c.row_version == expected_row_version,
        ).values(
            onboarding_current_step=current_step, onboarding_step_states=dict(step_states),
            row_version=self.table.c.row_version + 1, updated_at=as_of,
        ).returning(*self._columns())
        row = (await self.session.execute(statement)).mappings().one_or_none()
        return None if row is None else dict(row)
