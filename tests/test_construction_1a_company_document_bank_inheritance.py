import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["own", "foreign", "missing"])
async def test_c003_inherits_document_accounts_from_own_profile(case):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from services.construction_company_profile import (
        ConstructionCompanyProfileScopeError,
        ConstructionCompanyProfileService,
    )

    tenant_id, profile_id = uuid4(), uuid4()
    actor = SimpleNamespace(tenant_id=tenant_id, status="active")
    profile = None if case == "missing" else SimpleNamespace(
        id=profile_id, tenant_id=tenant_id,
    )
    account = SimpleNamespace(
        tenant_company_profile_id=(
            uuid4() if case == "foreign" else profile_id
        ),
        is_active=True, show_in_documents=True,
        account_holder_name="Company", bank_name="Bank",
        iban=None, account_number="123456",
        bic_swift=None, currency="XTS",
    )
    reader = SimpleNamespace(
        list_document_visible_for_tenant=AsyncMock(
            return_value=[account],
        ),
    )
    service = ConstructionCompanyProfileService(
        profile_reader=SimpleNamespace(
            get_for_tenant=AsyncMock(return_value=profile),
        ),
        bank_account_reader=reader,
    )

    if case == "foreign":
        with pytest.raises(ConstructionCompanyProfileScopeError):
            await service.list_inherited_document_bank_accounts(actor=actor)
    else:
        result = await service.list_inherited_document_bank_accounts(
            actor=actor,
        )
        assert result == ([] if case == "missing" else [{
            "account_holder_name": "Company",
            "bank_name": "Bank",
            "iban": None,
            "account_number": "123456",
            "bic_swift": None,
            "currency": "XTS",
        }])

    if case == "missing":
        reader.list_document_visible_for_tenant.assert_not_awaited()
    else:
        reader.list_document_visible_for_tenant.assert_awaited_once_with(
            tenant_id=tenant_id,
        )

@pytest.mark.asyncio
@pytest.mark.parametrize("active,visible", [(False, True), (True, False)])
async def test_c003_rejects_accounts_not_allowed_in_documents(active, visible):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from services.construction_company_profile import (
        ConstructionCompanyProfileService,
    )
    from services.platform_company_bank_accounts import (
        BankAccountNotDocumentVisibleError,
    )

    tenant_id, profile_id = uuid4(), uuid4()
    actor = SimpleNamespace(tenant_id=tenant_id, status="active")
    account = SimpleNamespace(
        tenant_company_profile_id=profile_id,
        is_active=active, show_in_documents=visible,
        account_holder_name="Company", bank_name="Bank",
        iban=None, account_number="123456",
        bic_swift=None, currency="XTS",
    )
    service = ConstructionCompanyProfileService(
        profile_reader=SimpleNamespace(
            get_for_tenant=AsyncMock(return_value=SimpleNamespace(
                id=profile_id, tenant_id=tenant_id,
            )),
        ),
        bank_account_reader=SimpleNamespace(
            list_document_visible_for_tenant=AsyncMock(
                return_value=[account],
            ),
        ),
    )
    with pytest.raises(BankAccountNotDocumentVisibleError):
        await service.list_inherited_document_bank_accounts(actor=actor)
