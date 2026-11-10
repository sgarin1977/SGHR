"""Current project-field cache lookup; authority and transaction belong to workflow."""
import re
from uuid import UUID

from sqlalchemy import select

from database.repositories.construction.construction_content_translations import (
    ConstructionContentTranslationRepository,
)
from services.construction.construction_content_translation import _language


class ConstructionProjectTranslationRepository(ConstructionContentTranslationRepository):
    async def find_project_text(
        self, *, tenant_id, project_id, field_name, target_language,
        source_version, source_text_hash, for_update=False,
    ):
        if (
            not isinstance(tenant_id, UUID)
            or not isinstance(project_id, UUID)
            or field_name not in ("name", "comment")
            or type(source_version) is not int
            or not 1 <= source_version <= 2**63 - 1
            or not isinstance(source_text_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_text_hash) is None
            or type(for_update) is not bool
        ):
            raise ValueError("An exact current project field is required.")
        _language(target_language)
        t = self.table
        query = select(t).where(
            t.c.tenant_id == tenant_id,
            t.c.entity_type == "construction.project",
            t.c.entity_id == project_id,
            t.c.field_name == field_name,
            t.c.target_language == target_language,
            t.c.source_version == source_version,
            t.c.source_text_hash == source_text_hash,
        ).order_by(t.c.source_language).limit(2)
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(
            query.execution_options(autoflush=False))
        return tuple(dict(row) for row in result.mappings())
