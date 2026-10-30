import pytest


@pytest.mark.asyncio
async def test_c003_document_data_consumes_effective_presentation():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from services.construction.construction_company_profile import (
        ConstructionCompanyProfileService,
    )

    actor = SimpleNamespace(tenant_id=uuid4(), status="active")
    legal = dict(
        legal_name="Legal Company", commercial_name="Brand",
        registered_address_raw="Registered address",
        operational_address_raw=None,
        signatory_name="Jane Doe", signatory_position="Director",
    )
    profile = SimpleNamespace(id=uuid4(), tenant_id=actor.tenant_id, **legal)
    effective = {
        "display_name": "Construction Brand",
        "email": "construction@example.pt",
    }
    presentation = SimpleNamespace(
        get_effective_for_actor=AsyncMock(return_value=effective),
    )
    service = ConstructionCompanyProfileService(
        profile_reader=SimpleNamespace(
            get_for_tenant=AsyncMock(return_value=profile),
        ),
        identifier_reader=SimpleNamespace(
            list_for_tenant=AsyncMock(return_value=[]),
        ),
        bank_account_reader=SimpleNamespace(
            list_document_visible_for_tenant=AsyncMock(return_value=[]),
        ),
        presentation_reader=presentation,
    )
    arguments = dict(
        actor=actor, platform_display_name="Brand",
        tenant_ui_name="Tenant", platform_ui_accent_color=None,
    )
    result = await service.get_document_company_data(**arguments)

    assert result["legal_identity"] == legal
    assert result["presentation"] == effective
    assert result["identifiers"] == []
    assert result["bank_accounts"] == []
    assert result["legal_identity"]["legal_name"] != effective["display_name"]
    presentation.get_effective_for_actor.assert_awaited_once_with(**arguments)
