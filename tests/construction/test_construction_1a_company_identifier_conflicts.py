from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from services.platform_company_identifiers import (
    CompanyIdentifierOperationError,
    TenantCompanyIdentifierService,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "constraint_name",
    (
        "uq_tenant_company_identifiers_normalized",
        "uq_tenant_company_identifiers_primary_scope",
    ),
)
async def test_identifier_unique_conflict_rolls_back_without_audit(
    constraint_name,
):
    tenant_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    original = RuntimeError("private identifier value")
    original.sqlstate = "23505"
    original.constraint_name = constraint_name
    database_error = IntegrityError("INSERT", {}, original)

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    event_repository = SimpleNamespace(create_event=AsyncMock())
    identifier_repository = SimpleNamespace(
        create_identifier=AsyncMock(side_effect=database_error),
    )
    service = TenantCompanyIdentifierService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(return_value=profile),
        ),
        identifier_repository=identifier_repository,
        event_repository=event_repository,
    )

    with pytest.raises(CompanyIdentifierOperationError) as caught:
        await service.create_identifier(
            actor=actor,
            country_code="PT",
            identifier_type="tax_id",
            identifier_value="123456789",
            is_primary=True,
        )

    identifier_repository.create_identifier.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    event_repository.create_event.assert_not_awaited()
    assert "private identifier value" not in str(caught.value)
