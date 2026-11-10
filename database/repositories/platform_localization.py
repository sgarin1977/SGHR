"""Read-only localization projection of canonical Platform tables."""
from uuid import UUID
from sqlalchemy import and_, select
from database.models import Language, TenantLanguage
from database.repositories.platform_organization_version import TENANT_SETTINGS


class PlatformLocalizationRepository:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def _tenant(tenant_id):
        if not isinstance(tenant_id, UUID):
            raise ValueError("Canonical tenant UUID is required.")

    async def get_tenant_language(self, *, tenant_id):
        self._tenant(tenant_id)
        result = await self.session.execute(
            select(TENANT_SETTINGS.c.id.label("tenant_id"), TENANT_SETTINGS.c.default_language, TENANT_SETTINGS.c.updated_at, TENANT_SETTINGS.c.row_version)
            .where(TENANT_SETTINGS.c.id == tenant_id, TENANT_SETTINGS.c.status == "active")
            .execution_options(autoflush=False)
        )
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)

    async def list_language_catalog(self, *, tenant_id):
        self._tenant(tenant_id)
        result = await self.session.execute(
            select(
                Language.code, Language.name, Language.native_name,
                TenantLanguage.is_active.is_(True).label("enabled_for_organization"),
            )
            .outerjoin(TenantLanguage, and_(
                TenantLanguage.language_code == Language.code,
                TenantLanguage.tenant_id == tenant_id,
            ))
            .where(Language.is_active.is_(True))
            .order_by(Language.code)
            .execution_options(autoflush=False)
        )
        return [dict(row) for row in result.mappings().all()]

# con_011_organization_language_http_v1

# con_011_row_version_v1
