"""Publish an exact current draft; caller holds root and version locks."""
from sqlalchemy import update

from database.repositories.construction.construction_template_draft_content import (
    ConstructionTemplateDraftContentRepository,
)


class ConstructionTemplatePublicationRepository(ConstructionTemplateDraftContentRepository):
    async def publish_draft(
        self, *, tenant_id, template_id, version_id,
        expected_row_version, content_hash, as_of,
    ):
        v = self.versions
        row = (await self.session.execute(
            update(v).where(
                v.c.tenant_id == tenant_id,
                v.c.template_id == template_id,
                v.c.id == version_id,
                v.c.status == "DRAFT",
                v.c.row_version == expected_row_version,
            ).values(
                status="PUBLISHED",
                content_hash=content_hash,
                row_version=v.c.row_version + 1,
                published_at=as_of,
                updated_at=as_of,
            ).returning(v)
        )).mappings().one_or_none()
        return None if row is None else dict(row)
