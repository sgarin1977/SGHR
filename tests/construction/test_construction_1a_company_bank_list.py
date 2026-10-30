from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql


@pytest.mark.asyncio
async def test_bank_repository_lists_tenant_accounts_including_inactive():
    from database.repositories.company_profiles import (
        TenantCompanyBankAccountRepository,
    )

    tenant_id = uuid4()
    active = SimpleNamespace(id=uuid4(), is_active=True)
    inactive = SimpleNamespace(id=uuid4(), is_active=False)

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [active, inactive]

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    result = await TenantCompanyBankAccountRepository(
        session
    ).list_for_tenant(tenant_id=tenant_id)

    assert result == [active, inactive]
    assert len(session.statements) == 1
    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    where_sql = sql.partition("WHERE ")[2]
    assert where_sql, sql
    assert f"tenant_company_profiles.tenant_id = '{tenant_id}'" in where_sql
    assert "tenant_company_bank_accounts.is_active" not in where_sql
    assert "tenant_company_bank_accounts.show_in_documents" not in where_sql


@pytest.mark.asyncio
async def test_bank_service_lists_accounts_for_active_member():
    from services.platform_company_bank_accounts import (
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(),
    )
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    accounts = [
        SimpleNamespace(
            id=uuid4(),
            tenant_company_profile_id=profile.id,
            is_active=True,
        ),
        SimpleNamespace(
            id=uuid4(),
            tenant_company_profile_id=profile.id,
            is_active=False,
        ),
    ]
    profiles = SimpleNamespace(
        get_for_tenant=AsyncMock(return_value=profile),
    )
    account_repository = SimpleNamespace(
        list_for_tenant=AsyncMock(return_value=accounts),
    )
    service = TenantCompanyBankAccountService(
        session=SimpleNamespace(),
        profile_repository=profiles,
        account_repository=account_repository,
        event_repository=SimpleNamespace(),
    )

    assert await service.list_accounts(actor=actor) == accounts
    profiles.get_for_tenant.assert_awaited_once_with(
        tenant_id=tenant_id
    )
    account_repository.list_for_tenant.assert_awaited_once_with(
        tenant_id=tenant_id
    )
