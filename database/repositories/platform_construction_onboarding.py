"""Initial Construction assignments using the existing Platform registry.

No catalogue changes, commits, external operations or tariff allocation.
This repository is an internal collaborator of organization bootstrap.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from database.models import (
    Language, Module, Suite, SuiteModule, Tenant,
    TenantLanguage, TenantModule, TenantSuite,
)
from database.repositories.construction import ConstructionAccessRepository


class PlatformConstructionOnboardingRepository:
    def __init__(self, session):
        self.session = session

    async def provision_new_tenant(self, *, tenant_id, user_id, as_of):
        if (
            not isinstance(tenant_id, UUID)
            or not isinstance(user_id, UUID)
            or not isinstance(as_of, datetime)
            or as_of.tzinfo is None or as_of.utcoffset() is None
        ):
            raise ValueError("Canonical onboarding context is required.")
        as_of = as_of.astimezone(timezone.utc)

        tenant = (await self.session.execute(
            select(Tenant.id, Tenant.default_language).where(
                Tenant.id == tenant_id, Tenant.status == "active",
            ).with_for_update()
        )).one_or_none()
        if tenant is None or tenant[0] != tenant_id:
            raise RuntimeError("Bootstrap organization is unavailable.")
        language = tenant[1]

        access = ConstructionAccessRepository(self.session)
        if await access.has_active_platform_membership(
            tenant_id=tenant_id, user_id=user_id,
        ) is not True:
            raise RuntimeError("Creator membership must precede provisioning.")

        # Read and hold the existing catalogue configuration.
        # Do not create or reactivate global Suite/Module entries here.
        catalogue = (await self.session.execute(
            select(Suite.id, Module.id, SuiteModule.id)
            .select_from(Suite)
            .join(SuiteModule, SuiteModule.suite_id == Suite.id)
            .join(Module, Module.id == SuiteModule.module_id)
            .where(
                Suite.code == "construction", Suite.status == "active",
                Module.code == "construction.core", Module.status == "active",
            )
            .with_for_update(read=True, of=(Suite, Module, SuiteModule))
        )).one_or_none()
        if catalogue is None or any(not isinstance(value, UUID) for value in catalogue):
            raise RuntimeError("Construction Suite/Core catalogue is unavailable.")
        suite_id, module_id, _ = catalogue

        # This is initial provisioning, not a general access/reactivation path.
        # Existing assignments are never silently replaced or extended.
        for model in (TenantSuite, TenantModule):
            existing = (await self.session.execute(
                select(model.id).where(model.tenant_id == tenant_id).limit(1)
            )).scalar_one_or_none()
            if existing is not None:
                raise RuntimeError("Organization already has module assignments.")

        configured_language = (await self.session.execute(
            select(Language.code).where(
                Language.code == language, Language.is_active.is_(True),
            )
        )).scalar_one_or_none()
        if configured_language != language:
            raise RuntimeError("Organization language is unavailable.")

        language_row = (await self.session.execute(
            select(TenantLanguage).where(
                TenantLanguage.tenant_id == tenant_id,
                TenantLanguage.language_code == language,
            ).with_for_update()
        )).scalar_one_or_none()
        if language_row is None:
            self.session.add(TenantLanguage(
                tenant_id=tenant_id, language_code=language, is_active=True,
                created_at=as_of, updated_at=as_of,
            ))
        elif language_row.is_active is not True:
            raise RuntimeError("Initial organization language is inactive.")

        self.session.add(TenantSuite(
            tenant_id=tenant_id, suite_id=suite_id,
            status="active", activated_at=as_of, expires_at=None,
            created_at=as_of, updated_at=as_of,
        ))
        self.session.add(TenantModule(
            tenant_id=tenant_id, module_id=module_id,
            is_enabled=True, source="suite",
            created_at=as_of, updated_at=as_of,
        ))
        await self.session.flush()
