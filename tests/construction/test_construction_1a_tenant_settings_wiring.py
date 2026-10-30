def test_c002_logo_reader_uses_platform_session():
    from services.construction.construction_tenant_settings import (
        build_construction_tenant_settings_service,
    )

    construction_session = object()
    platform_session = object()

    service = build_construction_tenant_settings_service(
        construction_session=construction_session,
        platform_session=platform_session,
    )

    assert service.session is construction_session
    assert service.settings_reader.session is construction_session
    assert service.event_repository.session is construction_session
    assert service.file_repository.session is platform_session
