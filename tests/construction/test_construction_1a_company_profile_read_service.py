from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.platform_company_profile_access import (
    CompanyProfilePermissionDeniedError,
)
from services.platform_company_profiles import (
    TenantCompanyProfileService,
)


@pytest.mark.asyncio
async def test_con_c001_profile_read_uses_verified_actor_tenant():
    tenant_id = uuid4()
    profile = SimpleNamespace(tenant_id=tenant_id)
    calls = []

    class Repository:
        async def get_for_tenant(self, *, tenant_id):
            calls.append(tenant_id)
            return profile

    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
    )
    service = TenantCompanyProfileService(
        session=object(),
        repository=Repository(),
        event_repository=object(),
    )

    assert await service.get_profile(actor=actor) is profile
    assert calls == [tenant_id]


@pytest.mark.asyncio
async def test_con_c001_profile_read_rejects_foreign_tenant_result():
    class Repository:
        async def get_for_tenant(self, *, tenant_id):
            return SimpleNamespace(tenant_id=uuid4())

    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
    )
    service = TenantCompanyProfileService(
        session=object(),
        repository=Repository(),
        event_repository=object(),
    )

    with pytest.raises(CompanyProfilePermissionDeniedError):
        await service.get_profile(actor=actor)
