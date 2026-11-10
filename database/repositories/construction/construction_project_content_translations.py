"""Current universal-area name lookup in the existing tenant translation cache."""
import re
from uuid import UUID

from sqlalchemy import select

from database.repositories.construction.construction_project_translation import (
    ConstructionProjectTranslationRepository,
)
from services.construction.construction_content_translation import _language


class ConstructionProjectContentTranslationRepository(ConstructionProjectTranslationRepository):
    async def find_area_name(
        self, *, tenant_id, area_id, target_language,
        source_version, source_text_hash, for_update=False,
    ):
        if (
            not isinstance(tenant_id, UUID) or not isinstance(area_id, UUID)
            or type(source_version) is not int or not 1 <= source_version <= 2**63 - 1
            or not isinstance(source_text_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_text_hash) is None
            or type(for_update) is not bool
        ):
            raise ValueError("An exact current area name is required.")
        _language(target_language)
        t = self.table
        statement = select(t).where(
            t.c.tenant_id == tenant_id,
            t.c.entity_type == "construction.project_area",
            t.c.entity_id == area_id,
            t.c.field_name == "name",
            t.c.target_language == target_language,
            t.c.source_version == source_version,
            t.c.source_text_hash == source_text_hash,
        ).order_by(t.c.source_language).limit(2)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(
            statement.execution_options(autoflush=False))
        return tuple(dict(row) for row in result.mappings())
