from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.platform_company_identifiers import (
    CompanyIdentifierOperationError,
    TenantCompanyIdentifierService,
)


@pytest.mark.parametrize("operation", ("replace", "delete"))
async def test_identifier_change_rolls_back_on_audit_failure(operation):
    tenant_id, user_id = uuid4(), uuid4()
    profile = NS(id=uuid4(), tenant_id=tenant_id)
    old = NS(
        id=uuid4(), tenant_company_profile_id=profile.id,
        country_code="PT", identifier_type="vat",
        identifier_value="PT OLD", is_primary=True,
    )
    new = NS(id=uuid4(), tenant_company_profile_id=profile.id)
    actor = NS(
        tenant_id=tenant_id, user_id=user_id, status="active",
        permissions=("platform.organization.profile.manage",),
    )
    session = NS(commit=AsyncMock(), rollback=AsyncMock())
    profiles = NS(get_for_tenant_for_update=AsyncMock(
        return_value=profile
    ))
    identifiers = NS(
        get_for_tenant_for_update=AsyncMock(return_value=old),
        delete_locked_identifier=AsyncMock(),
        create_identifier=AsyncMock(return_value=new),
    )
    events = NS(create_event=AsyncMock(
        side_effect=RuntimeError("private audit failure")
    ))
    service = TenantCompanyIdentifierService(
        session=session, profile_repository=profiles,
        identifier_repository=identifiers, event_repository=events,
    )

    with pytest.raises(
        CompanyIdentifierOperationError,
        match="Identifier operation failed",
    ) as exc_info:
        if operation == "replace":
            await service.replace_identifier(
                actor=actor, identifier_id=old.id,
                country_code="PT", identifier_type="vat",
                identifier_value="PT NEW", is_primary=True,
            )
        else:
            await service.delete_identifier(
                actor=actor, identifier_id=old.id,
            )

    identifiers.delete_locked_identifier.assert_awaited_once()
    events.create_event.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    assert "private audit failure" not in str(exc_info.value)
