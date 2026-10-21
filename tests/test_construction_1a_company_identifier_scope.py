from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.platform_company_identifiers import (
    CompanyIdentifierOperationError,
    TenantCompanyIdentifierService,
)


@pytest.mark.parametrize("operation", ("replace", "delete"))
async def test_identifier_change_rejects_foreign_profile(operation):
    tenant_id, user_id = uuid4(), uuid4()
    profile = NS(id=uuid4(), tenant_id=tenant_id)
    foreign = NS(
        id=uuid4(), tenant_company_profile_id=uuid4(),
    )
    actor = NS(
        tenant_id=tenant_id, user_id=user_id, status="active",
        permissions=("platform.organization.profile.manage",),
    )
    session = NS(commit=AsyncMock(), rollback=AsyncMock())
    profiles = NS(get_for_tenant_for_update=AsyncMock(
        return_value=profile
    ))
    identifiers = NS(
        get_for_tenant_for_update=AsyncMock(return_value=foreign),
        delete_locked_identifier=AsyncMock(),
        create_identifier=AsyncMock(),
    )
    events = NS(create_event=AsyncMock())
    service = TenantCompanyIdentifierService(
        session=session, profile_repository=profiles,
        identifier_repository=identifiers, event_repository=events,
    )

    with pytest.raises(
        CompanyIdentifierOperationError,
        match="Identifier is not available",
    ):
        if operation == "replace":
            await service.replace_identifier(
                actor=actor, identifier_id=foreign.id,
                country_code="PT", identifier_type="vat",
                identifier_value="PT NEW", is_primary=True,
            )
        else:
            await service.delete_identifier(
                actor=actor, identifier_id=foreign.id,
            )

    identifiers.get_for_tenant_for_update.assert_awaited_once_with(
        tenant_id=tenant_id, identifier_id=foreign.id,
    )
    identifiers.delete_locked_identifier.assert_not_awaited()
    identifiers.create_identifier.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.parametrize("operation", ("replace", "delete"))
async def test_identifier_missing_uses_typed_not_found(operation):
    from services.platform_company_identifiers import (
        CompanyIdentifierNotFoundError,
    )

    tenant_id = uuid4()
    actor = NS(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    profile = NS(id=uuid4(), tenant_id=tenant_id)
    session = NS(commit=AsyncMock(), rollback=AsyncMock())
    profiles = NS(
        get_for_tenant_for_update=AsyncMock(return_value=profile)
    )
    identifiers = NS(
        get_for_tenant_for_update=AsyncMock(return_value=None),
        delete_locked_identifier=AsyncMock(),
        create_identifier=AsyncMock(),
    )
    events = NS(create_event=AsyncMock())
    service = TenantCompanyIdentifierService(
        session=session,
        profile_repository=profiles,
        identifier_repository=identifiers,
        event_repository=events,
    )

    with pytest.raises(CompanyIdentifierNotFoundError):
        if operation == "replace":
            await service.replace_identifier(
                actor=actor,
                identifier_id=uuid4(),
                country_code="PT",
                identifier_type="vat",
                identifier_value="PT NEW",
                is_primary=True,
            )
        else:
            await service.delete_identifier(
                actor=actor,
                identifier_id=uuid4(),
            )

    identifiers.delete_locked_identifier.assert_not_awaited()
    identifiers.create_identifier.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
