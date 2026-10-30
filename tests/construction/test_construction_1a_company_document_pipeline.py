import pytest


@pytest.mark.asyncio
async def test_c003_factory_reads_complete_company_data_without_writes():
    from types import SimpleNamespace as NS
    from uuid import uuid4
    from database.models import (
        ConstructionTenantSetting, TenantCompanyProfile,
        TenantCompanyIdentifier, TenantCompanyBankAccount,
    )
    from services.construction.construction_company_profile import (
        build_construction_company_profile_service,
    )

    actor = NS(user_id=uuid4(), tenant_id=uuid4(), status="active")
    profile_id, logo_id = uuid4(), uuid4()
    profile = NS(
        id=profile_id, tenant_id=actor.tenant_id,
        legal_name="Legal Company", commercial_name="Brand",
        registered_address_raw="Registered address",
        operational_address_raw=None,
        signatory_name="Jane Doe", signatory_position="Director",
        phone="+351912345678", email="office@example.pt",
        website=None, logo_file_id=logo_id,
    )
    identifier_data = dict(
        country_code="PT", identifier_type="vat",
        identifier_value="PT 123456789", is_primary=True,
    )
    bank_data = dict(
        account_holder_name="Legal Company", bank_name="Bank",
        iban=None, account_number="123456", bic_swift=None, currency="XTS",
    )
    rows = {
        TenantCompanyProfile: [profile],
        ConstructionTenantSetting: [NS(
            tenant_id=actor.tenant_id,
            display_name_override="Construction Brand",
            email_override="construction@example.pt",
        )],
        TenantCompanyIdentifier: [NS(
            tenant_company_profile_id=profile_id, **identifier_data,
        )],
        TenantCompanyBankAccount: [NS(
            tenant_company_profile_id=profile_id,
            is_active=True, show_in_documents=True, **bank_data,
        )],
    }
    seen = set()

    class Session:
        async def execute(self, statement):
            assert actor.tenant_id in statement.compile().params.values()
            entity = statement.column_descriptions[0]["entity"]
            seen.add(entity)
            values = rows[entity]
            return NS(
                scalar_one_or_none=lambda: values[0] if values else None,
                scalars=lambda: NS(all=lambda: values),
            )

    service = build_construction_company_profile_service(
        construction_session=Session(),
    )
    result = await service.get_document_company_data(
        actor=actor, platform_display_name="Brand",
        tenant_ui_name="Tenant", platform_ui_accent_color="#123456",
    )
    assert seen == set(rows)
    assert result["legal_identity"]["legal_name"] == "Legal Company"
    assert result["legal_identity"]["signatory_name"] == "Jane Doe"
    assert result["presentation"]["display_name"] == "Construction Brand"
    assert result["presentation"]["email"] == "construction@example.pt"
    assert result["presentation"]["phone"] == profile.phone
    assert result["presentation"]["logo_file_id"] == logo_id
    assert result["identifiers"] == [identifier_data]
    assert result["bank_accounts"] == [bank_data]
