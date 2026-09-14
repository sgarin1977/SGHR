from datetime import datetime, timezone

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Language,
    LegalDocument,
    Module,
    PaidFeature,
    Suite,
    SuiteModule,
    Tenant,
    TenantDomain,
    TenantLanguage,
    TenantModule,
    TenantSuite,
    TenantWhiteLabelSetting,
)


class WhiteLabelRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def get_active_tenant_for_domain(
        self,
        *,
        domain: str,
    ) -> Tenant | None:
        statement = (
            select(Tenant)
            .join(
                TenantDomain,
                TenantDomain.tenant_id
                == Tenant.id,
            )
            .where(
                TenantDomain.domain == domain,
                TenantDomain.status == "active",
                (
                    TenantDomain.verified_at
                    .is_not(None)
                ),
                Tenant.status == "active",
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def get_settings(
        self,
        *,
        tenant_id,
    ) -> TenantWhiteLabelSetting | None:
        statement = (
            select(TenantWhiteLabelSetting)
            .where(
                TenantWhiteLabelSetting.tenant_id
                == tenant_id,
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def list_active_languages(
        self,
        *,
        tenant_id,
    ) -> list[Language]:
        statement = (
            select(Language)
            .join(
                TenantLanguage,
                TenantLanguage.language_code
                == Language.code,
            )
            .where(
                TenantLanguage.tenant_id
                == tenant_id,
                TenantLanguage.is_active
                .is_(True),
                Language.is_active.is_(True),
            )
            .order_by(Language.code)
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def list_active_suites(
        self,
        *,
        tenant_id,
        now,
    ) -> list[Suite]:
        statement = (
            select(Suite)
            .join(
                TenantSuite,
                TenantSuite.suite_id
                == Suite.id,
            )
            .where(
                TenantSuite.tenant_id
                == tenant_id,
                TenantSuite.status
                == "active",
                Suite.status == "active",
                or_(
                    TenantSuite.expires_at
                    .is_(None),
                    TenantSuite.expires_at
                    > now,
                ),
            )
            .order_by(Suite.code)
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def list_enabled_modules(
        self,
        *,
        tenant_id,
    ) -> list[Module]:
        current_time = datetime.now(
            timezone.utc
        )

        active_suite = exists(
            select(SuiteModule.id)
            .join(
                TenantSuite,
                and_(
                    TenantSuite.suite_id
                    == SuiteModule.suite_id,
                    TenantSuite.tenant_id
                    == tenant_id,
                ),
            )
            .join(
                Suite,
                Suite.id
                == TenantSuite.suite_id,
            )
            .where(
                SuiteModule.module_id
                == Module.id,
                TenantSuite.status
                == "active",
                Suite.status == "active",
                or_(
                    TenantSuite.expires_at
                    .is_(None),
                    TenantSuite.expires_at
                    > current_time,
                ),
            )
        )

        statement = (
            select(Module)
            .join(
                TenantModule,
                TenantModule.module_id
                == Module.id,
            )
            .where(
                TenantModule.tenant_id
                == tenant_id,
                TenantModule.is_enabled
                .is_(True),
                Module.status == "active",
                or_(
                    TenantModule.source
                    == "manual",
                    and_(
                        TenantModule.source
                        == "suite",
                        active_suite,
                    ),
                ),
            )
            .order_by(Module.code)
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())


    async def list_published_legal_documents(
        self,
        *,
        tenant_id,
    ) -> list[LegalDocument]:
        statement = (
            select(LegalDocument)
            .where(
                LegalDocument.tenant_id
                == tenant_id,
                LegalDocument.status
                == "published",
            )
            .order_by(
                LegalDocument.doc_type,
                LegalDocument.language,
                LegalDocument.version.desc(),
            )
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def list_active_pricing(
        self,
        *,
        tenant_id,
    ) -> list[PaidFeature]:
        statement = (
            select(PaidFeature)
            .where(
                PaidFeature.tenant_id
                == tenant_id,
                PaidFeature.status
                == "active",
            )
            .order_by(PaidFeature.code)
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def is_module_enabled(
        self,
        *,
        tenant_id,
        module_code: str,
        now: datetime | None = None,
    ) -> bool:
        current_time = (
            now
            if now is not None
            else datetime.now(timezone.utc)
        )

        active_suite = exists(
            select(SuiteModule.id)
            .join(
                TenantSuite,
                and_(
                    TenantSuite.suite_id
                    == SuiteModule.suite_id,
                    TenantSuite.tenant_id
                    == tenant_id,
                ),
            )
            .join(
                Suite,
                Suite.id
                == TenantSuite.suite_id,
            )
            .where(
                SuiteModule.module_id
                == Module.id,
                TenantSuite.status
                == "active",
                Suite.status == "active",
                or_(
                    TenantSuite.expires_at
                    .is_(None),
                    TenantSuite.expires_at
                    > current_time,
                ),
            )
        )

        statement = (
            select(Module.id)
            .join(
                TenantModule,
                TenantModule.module_id
                == Module.id,
            )
            .where(
                TenantModule.tenant_id
                == tenant_id,
                TenantModule.is_enabled
                .is_(True),
                Module.code == module_code,
                Module.status == "active",
                or_(
                    TenantModule.source
                    == "manual",
                    and_(
                        TenantModule.source
                        == "suite",
                        active_suite,
                    ),
                ),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return (
            result.scalar_one_or_none()
            is not None
        )

    async def is_suite_enabled(
        self,
        *,
        tenant_id,
        suite_code: str,
        now: datetime | None = None,
    ) -> bool:
        current_time = (
            now
            if now is not None
            else datetime.now(timezone.utc)
        )

        statement = (
            select(Suite.id)
            .join(
                TenantSuite,
                TenantSuite.suite_id
                == Suite.id,
            )
            .where(
                TenantSuite.tenant_id
                == tenant_id,
                Suite.code == suite_code,
                TenantSuite.status
                == "active",
                Suite.status == "active",
                or_(
                    TenantSuite.activated_at
                    .is_(None),
                    TenantSuite.activated_at
                    <= current_time,
                ),
                or_(
                    TenantSuite.expires_at
                    .is_(None),
                    TenantSuite.expires_at
                    > current_time,
                ),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return (
            result.scalar_one_or_none()
            is not None
        )

