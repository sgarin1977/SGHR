from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.platform_company_identifiers import (
    CompanyIdentifierOperationError,
    TenantCompanyIdentifierService,
)


async def test_identifier_create_rolls_back_when_audit_fails():
    tenant_id, user_id = uuid4(), uuid4()
    profile = NS(id=uuid4(), tenant_id=tenant_id)
    identifier = NS(id=uuid4(), tenant_company_profile_id=profile.id)
    actor = NS(
        tenant_id=tenant_id,
        user_id=user_id,
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    session = NS(commit=AsyncMock(), rollback=AsyncMock())
    profiles = NS(get_for_tenant_for_update=AsyncMock(
        return_value=profile
    ))
    identifiers = NS(create_identifier=AsyncMock(
        return_value=identifier
    ))
    events = NS(create_event=AsyncMock(
        side_effect=RuntimeError("private audit failure")
    ))
    service = TenantCompanyIdentifierService(
        session=session,
        profile_repository=profiles,
        identifier_repository=identifiers,
        event_repository=events,
    )

    with pytest.raises(
        CompanyIdentifierOperationError,
        match="Identifier operation failed",
    ) as exc_info:
        await service.create_identifier(
            actor=actor,
            country_code="PT",
            identifier_type="vat",
            identifier_value="PT123",
        )

    identifiers.create_identifier.assert_awaited_once()
    events.create_event.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    assert "private audit failure" not in str(exc_info.value)
