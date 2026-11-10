"""Canonical organization-language writes; caller owns authorization and commit."""
from uuid import UUID, uuid4
from sqlalchemy import select, update
from database.models import Language, TenantLanguage
from database.repositories.platform_organization_version import TENANT_SETTINGS


class PlatformOrganizationLanguageRepository:
    def __init__(self, session):
        self.session = session

    async def lock_active_language(self, *, language_code):
        result = await self.session.execute(
            select(Language.code).where(
                Language.code == language_code,
                Language.is_active.is_(True),
            ).with_for_update(read=True).execution_options(autoflush=False)
        )
        return result.scalar_one_or_none() == language_code

    async def lock_active_organization(self, *, tenant_id):
        if not isinstance(tenant_id, UUID):
            raise ValueError("Canonical organization UUID is required.")
        result = await self.session.execute(
            select(
                TENANT_SETTINGS.c.id.label("tenant_id"),
                TENANT_SETTINGS.c.default_language,
                TENANT_SETTINGS.c.updated_at,
                TENANT_SETTINGS.c.row_version,
            )
            .where(TENANT_SETTINGS.c.id == tenant_id, TENANT_SETTINGS.c.status == "active")
            .with_for_update()
            .execution_options(autoflush=False)
        )
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)

    async def enable_language(self, *, tenant_id, language_code, as_of):
        result = await self.session.execute(
            select(TenantLanguage.id, TenantLanguage.is_active).where(
                TenantLanguage.tenant_id == tenant_id,
                TenantLanguage.language_code == language_code,
            ).with_for_update().execution_options(autoflush=False)
        )
        row = result.mappings().one_or_none()
        if row is None:
            await self.session.execute(
                TenantLanguage.__table__.insert().values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    language_code=language_code,
                    is_active=True,
                    created_at=as_of,
                    updated_at=as_of,
                )
            )
            return None

        previous = row["is_active"]
        if type(previous) is not bool:
            raise ValueError("Organization language configuration is invalid.")
        if not previous:
            await self.session.execute(
                update(TenantLanguage.__table__).where(
                    TenantLanguage.id == row["id"],
                    TenantLanguage.tenant_id == tenant_id,
                    TenantLanguage.language_code == language_code,
                    TenantLanguage.is_active.is_(False),
                ).values(is_active=True, updated_at=as_of)
            )
        return previous

    async def replace_default(
        self, *, tenant_id, expected_language,
        expected_row_version, language_code, as_of,
    ):
        result = await self.session.execute(
            update(TENANT_SETTINGS).where(
                TENANT_SETTINGS.c.id == tenant_id,
                TENANT_SETTINGS.c.status == "active",
                TENANT_SETTINGS.c.default_language == expected_language,
                TENANT_SETTINGS.c.row_version == expected_row_version,
            ).values(
                default_language=language_code,
                updated_at=as_of,
                row_version=expected_row_version + 1,
            ).returning(
                TENANT_SETTINGS.c.id.label("tenant_id"),
                TENANT_SETTINGS.c.default_language,
                TENANT_SETTINGS.c.updated_at,
                TENANT_SETTINGS.c.row_version,
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)

# con_011_row_version_v1
