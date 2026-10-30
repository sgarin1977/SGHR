from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ConstructionTenantSetting


class ConstructionTenantSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_tenant(
        self,
        *,
        tenant_id: UUID,
    ) -> ConstructionTenantSetting | None:
        statement = (
            select(ConstructionTenantSetting)
            .where(ConstructionTenantSetting.tenant_id == tenant_id)
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_settings(
        self,
        *,
        tenant_id: UUID,
        values: dict,
    ) -> ConstructionTenantSetting:
        allowed = {
            "display_name_override",
            "phone_override",
            "email_override",
            "website_override",
            "logo_file_id_override",
            "document_header_text",
            "document_footer_text",
            "accent_color",
        }
        if not isinstance(values, dict) or not set(values).issubset(allowed):
            raise ValueError("Invalid Construction settings fields.")

        settings = ConstructionTenantSetting(
            tenant_id=tenant_id,
            **values,
        )
        self.session.add(settings)
        await self.session.flush()
        return settings

    async def get_for_tenant_for_update(
        self,
        *,
        tenant_id: UUID,
    ) -> ConstructionTenantSetting | None:
        statement = (
            select(ConstructionTenantSetting)
            .where(ConstructionTenantSetting.tenant_id == tenant_id)
            .limit(1)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def update_settings(
        self,
        *,
        settings: ConstructionTenantSetting,
        values: dict,
    ) -> ConstructionTenantSetting:
        allowed = {
            "display_name_override",
            "phone_override",
            "email_override",
            "website_override",
            "logo_file_id_override",
            "document_header_text",
            "document_footer_text",
            "accent_color",
        }
        if (
            not isinstance(values, dict)
            or not values
            or not set(values).issubset(allowed)
        ):
            raise ValueError("Invalid Construction settings update.")

        for name, value in values.items():
            setattr(settings, name, value)

        await self.session.flush()
        return settings
