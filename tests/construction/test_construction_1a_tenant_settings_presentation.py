import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["own", "missing", "foreign"])
async def test_c002_effective_profile_is_loaded_in_actor_tenant(case):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsScopeError,
        ConstructionTenantSettingsService,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    profile = None if case == "missing" else SimpleNamespace(
        tenant_id=actor.tenant_id if case == "own" else uuid4(),
        phone="+351912345678",
        email="office@example.pt",
        website="https://example.pt",
        logo_file_id=uuid4(),
    )
    profile_reader = SimpleNamespace(
        get_for_tenant=AsyncMock(return_value=profile),
    )
    settings_reader = SimpleNamespace(
        get_for_tenant=AsyncMock(return_value=None),
    )
    service = ConstructionTenantSettingsService(
        settings_reader=settings_reader,
        profile_reader=profile_reader,
    )

    arguments = dict(
        actor=actor,
        platform_display_name=None,
        tenant_ui_name="Tenant UI name",
        platform_ui_accent_color="#123456",
    )
    if case == "foreign":
        with pytest.raises(ConstructionTenantSettingsScopeError):
            await service.get_effective_for_actor(**arguments)
    else:
        result = await service.get_effective_for_actor(**arguments)
        assert result["display_name"] == "Tenant UI name"
        assert result["accent_color"] == "#123456"
        for field in ("phone", "email", "website", "logo_file_id"):
            assert result[field] == (
                getattr(profile, field) if profile is not None else None
            )
        assert "legal_name" not in result
        assert "tenant_id" not in result

    profile_reader.get_for_tenant.assert_awaited_once_with(
        tenant_id=actor.tenant_id,
    )

@pytest.mark.asyncio
async def test_c002_profile_changes_refresh_inherited_values():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, call
    from uuid import uuid4
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsService,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    old_logo, new_logo = uuid4(), uuid4()
    profiles = [
        SimpleNamespace(
            tenant_id=actor.tenant_id, phone="+351111111111",
            email="old@example.pt", logo_file_id=old_logo,
        ),
        SimpleNamespace(
            tenant_id=actor.tenant_id, phone="+351222222222",
            email="new@example.pt", logo_file_id=new_logo,
        ),
    ]
    settings = SimpleNamespace(
        tenant_id=actor.tenant_id,
        email_override="construction@example.pt",
    )
    profile_reader = SimpleNamespace(
        get_for_tenant=AsyncMock(side_effect=profiles),
    )
    service = ConstructionTenantSettingsService(
        profile_reader=profile_reader,
        settings_reader=SimpleNamespace(
            get_for_tenant=AsyncMock(return_value=settings),
        ),
    )
    arguments = dict(
        actor=actor, platform_display_name=None,
        tenant_ui_name="Company", platform_ui_accent_color=None,
    )
    before = await service.get_effective_for_actor(**arguments)
    after = await service.get_effective_for_actor(**arguments)

    assert before["phone"] == "+351111111111"
    assert after["phone"] == "+351222222222"
    assert before["logo_file_id"] == old_logo
    assert after["logo_file_id"] == new_logo
    assert before["email"] == after["email"] == "construction@example.pt"
    assert settings.email_override == "construction@example.pt"
    assert profile_reader.get_for_tenant.await_args_list == [
        call(tenant_id=actor.tenant_id),
        call(tenant_id=actor.tenant_id),
    ]
