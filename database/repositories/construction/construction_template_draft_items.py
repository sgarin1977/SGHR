"""Conditional draft item writes; caller holds root and version locks."""
from sqlalchemy import select, update

from database.repositories.construction.construction_template_draft_content import (
    ConstructionTemplateDraftContentRepository,
)

IMMUTABLE = frozenset({
    "id", "tenant_id", "template_version_id", "template_section_id",
    "row_version", "created_at", "updated_at",
})


class ConstructionTemplateDraftItemRepository(ConstructionTemplateDraftContentRepository):
    async def advance_draft(
        self, *, tenant_id, template_id, version_id,
        expected_row_version, labels, as_of,
    ):
        if not isinstance(labels, dict) or set(labels) - {"labels"}:
            raise ValueError("Only an optional labels update is accepted.")
        v = self.versions
        row = (await self.session.execute(
            update(v).where(
                v.c.tenant_id == tenant_id,
                v.c.template_id == template_id,
                v.c.id == version_id,
                v.c.status == "DRAFT",
                v.c.row_version == expected_row_version,
            ).values(
                **labels,
                row_version=v.c.row_version + 1,
                updated_at=as_of,
            ).returning(v)
        )).mappings().one_or_none()
        return None if row is None else dict(row)

    async def update_item(
        self, *, tenant_id, template_id, version_id, kind, item_id,
        expected_item_row_version, expected_version_row_version,
        fields, as_of,
    ):
        if kind not in ("SECTION", "QUESTION"):
            raise ValueError("Unknown draft item kind.")
        table = self.sections if kind == "SECTION" else self.questions
        allowed = set(table.c.keys()) - IMMUTABLE
        if (
            not isinstance(fields, dict) or not fields
            or not set(fields).issubset(allowed)
        ):
            raise ValueError("Unsupported draft item fields.")
        v = self.versions
        draft_exists = select(1).where(
            v.c.tenant_id == tenant_id,
            v.c.template_id == template_id,
            v.c.id == version_id,
            v.c.status == "DRAFT",
            v.c.row_version == expected_version_row_version,
        ).exists()
        row = (await self.session.execute(
            update(table).where(
                table.c.tenant_id == tenant_id,
                table.c.template_version_id == version_id,
                table.c.id == item_id,
                table.c.row_version == expected_item_row_version,
                draft_exists,
            ).values(
                **fields,
                row_version=table.c.row_version + 1,
                updated_at=as_of,
            ).returning(table)
        )).mappings().one_or_none()
        return None if row is None else dict(row)
