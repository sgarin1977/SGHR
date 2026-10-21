def test_c003_readers_use_the_same_construction_session():
    import pytest
    from database.repositories.company_profiles import (
        TenantCompanyProfileRepository,
        TenantCompanyIdentifierRepository,
        TenantCompanyBankAccountRepository,
    )
    from services.construction_company_profile import (
        build_construction_company_profile_service,
    )

    session = object()
    service = build_construction_company_profile_service(
        construction_session=session,
    )
    assert isinstance(service.profile_reader, TenantCompanyProfileRepository)
    assert isinstance(service.identifier_reader, TenantCompanyIdentifierRepository)
    assert isinstance(service.bank_account_reader, TenantCompanyBankAccountRepository)
    assert service.presentation_reader.settings_reader.session is session
    assert service.presentation_reader.profile_reader.session is session
    for reader in (
        service.profile_reader, service.identifier_reader,
        service.bank_account_reader,
    ):
        assert reader.session is session
    with pytest.raises(ValueError):
        build_construction_company_profile_service(construction_session=None)
