"""Draft header reads and conditional updates under root/version locks."""
from sqlalchemy import select, update

from database.repositories.construction.construction_template_drafts import (
    ConstructionTemplateDraftRepository,
)

HEADER_FIELDS = frozenset({
    "source_language", "title_key", "description_key", "labels",
})


class ConstructionTemplateDraftContentRepository(ConstructionTemplateDraftRepository):
    async def lock_template(self, *, tenant_id, template_id, write):
        if type(write) is not bool:
            raise ValueError("An explicit lock mode is required.")
        t = self.templates
        row = (await self.session.execute(
            select(t).where(
                t.c.tenant_id == tenant_id,
                t.c.id == template_id,
            ).with_for_update(read=not write)
        )).mappings().one_or_none()
        return None if row is None else dict(row)

    async def lock_draft(self, *, tenant_id, template_id, version_id, write):
        if type(write) is not bool:
            raise ValueError("An explicit lock mode is required.")
        v = self.versions
        row = (await self.session.execute(
            select(v).where(
                v.c.tenant_id == tenant_id,
                v.c.template_id == template_id,
                v.c.id == version_id,
                v.c.status == "DRAFT",
            ).with_for_update(read=not write)
        )).mappings().one_or_none()
        return None if row is None else dict(row)

    async def update_header(
        self, *, tenant_id, template_id, version_id,
        expected_row_version, fields, as_of,
    ):
        if (
            not isinstance(fields, dict) or not fields
            or not set(fields).issubset(HEADER_FIELDS)
        ):
            raise ValueError("Only draft header fields may be updated.")
        v = self.versions
        row = (await self.session.execute(
            update(v).where(
                v.c.tenant_id == tenant_id,
                v.c.template_id == template_id,
                v.c.id == version_id,
                v.c.status == "DRAFT",
                v.c.row_version == expected_row_version,
            ).values(
                **fields,
                row_version=v.c.row_version + 1,
                updated_at=as_of,
            ).returning(v)
        )).mappings().one_or_none()
        return None if row is None else dict(row)
