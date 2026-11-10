"""Draft allocation under the organization template root lock."""
from sqlalchemy import func, select

from database.repositories.construction.construction_template_copy import (
    ConstructionTemplateCopyRepository,
)


class ConstructionTemplateDraftRepository(ConstructionTemplateCopyRepository):
    async def get_template_for_update(self, *, tenant_id, template_id):
        t = self.templates
        row = (await self.session.execute(
            select(t).where(
                t.c.tenant_id == tenant_id,
                t.c.id == template_id,
            ).with_for_update()
        )).mappings().one_or_none()
        return None if row is None else dict(row)

    async def latest_version_number(self, *, tenant_id, template_id):
        v = self.versions
        return (await self.session.execute(
            select(func.max(v.c.version)).where(
                v.c.tenant_id == tenant_id,
                v.c.template_id == template_id,
            )
        )).scalar_one()
