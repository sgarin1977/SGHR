from services.construction_permissions import CONSTRUCTION_ROLE_PERMISSIONS
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction_permissions import (
    ConstructionAccessContext,
    ConstructionGrantContext,
)
from services.construction_tenant_settings import ConstructionTenantSettingsService


@pytest.mark.asyncio
async def test_c002_update_replaces_verified_logo_and_audits():
    tenant_id, user_id, logo_id = uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access = ConstructionAccessContext(
        user_id=user_id, tenant_id=tenant_id, roles=("OWNER",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id
        ),),
    )
    logo = SimpleNamespace(
        id=logo_id, tenant_id=tenant_id,
        entity_type="organization", entity_id=tenant_id,
        status="ready", antivirus_status="clean",
        visibility_scope="private", public_url=None,
        storage_path=f"{tenant_id}/{user_id}/logo.png",
        mime_type="image/png", file_type="image", size_bytes=1024,
    )
    current = SimpleNamespace(
        tenant_id=tenant_id, row_version=7,
        logo_file_id_override=None,
    )
    updated = SimpleNamespace(
        tenant_id=tenant_id, row_version=8,
        logo_file_id_override=logo_id,
    )
    repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=current),
        update_settings=AsyncMock(return_value=updated),
    )
    files = SimpleNamespace(
        get_organization_downloadable_file=AsyncMock(return_value=logo)
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    events = SimpleNamespace(create_event=AsyncMock())
    service = ConstructionTenantSettingsService(
        settings_reader=repository, file_repository=files,
        session=session, event_repository=events,
    )

    result = await service.update_for_actor(
        actor=actor, access_context=access,
        expected_row_version=7,
        changes={"logo_file_id_override": logo_id},
    )

    assert result.logo_file_id_override == logo_id
    files.get_organization_downloadable_file.assert_awaited_once_with(
        tenant_id=tenant_id, file_id=logo_id
    )
    repository.update_settings.assert_awaited_once_with(
        settings=current, values={"logo_file_id_override": logo_id}
    )
    audit = events.create_event.await_args.kwargs
    assert audit["payload"]["changes"] == [{
        "field": "logo_file_id_override",
        "old_value": None,
        "new_value": str(logo_id),
    }]
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
