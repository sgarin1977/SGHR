from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_con_c001_inherits_only_own_platform_identifiers(foreign):
    from services.construction.construction_company_profile import (
        ConstructionCompanyProfileScopeError,
        ConstructionCompanyProfileService,
    )

    tenant_id = uuid4()
    profile_id = uuid4()
    actor = SimpleNamespace(tenant_id=tenant_id, status="active")
    profile_reader = SimpleNamespace(
        get_for_tenant=AsyncMock(
            return_value=SimpleNamespace(
                id=profile_id,
                tenant_id=tenant_id,
            )
        )
    )
    identifier = SimpleNamespace(
        tenant_company_profile_id=(
            uuid4() if foreign else profile_id
        )
    )
    identifier_reader = SimpleNamespace(
        list_for_tenant=AsyncMock(return_value=[identifier])
    )
    service = ConstructionCompanyProfileService(
        profile_reader=profile_reader,
        identifier_reader=identifier_reader,
    )

    if foreign:
        with pytest.raises(ConstructionCompanyProfileScopeError):
            await service.list_inherited_identifiers(actor=actor)
    else:
        assert await service.list_inherited_identifiers(
            actor=actor
        ) == [identifier]

    profile_reader.get_for_tenant.assert_awaited_once_with(
        tenant_id=tenant_id
    )
    identifier_reader.list_for_tenant.assert_awaited_once_with(
        tenant_id=tenant_id
    )
