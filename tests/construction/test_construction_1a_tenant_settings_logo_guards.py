from services.construction.construction_permissions import CONSTRUCTION_ROLE_PERMISSIONS
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction.construction_permissions import (
    ConstructionAccessContext,
    ConstructionGrantContext,
)
from services.construction.construction_tenant_settings import (
    ConstructionTenantSettingsService,
    resolve_construction_logo_file_id,
)


def make_case(existing_logo=None):
    tenant_id, user_id = uuid4(), uuid4()
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
    current = SimpleNamespace(
        tenant_id=tenant_id, row_version=5,
        logo_file_id_override=existing_logo,
    )
    repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=current),
        update_settings=AsyncMock(),
    )
    files = SimpleNamespace(
        get_organization_downloadable_file=AsyncMock()
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    events = SimpleNamespace(create_event=AsyncMock())
    service = ConstructionTenantSettingsService(
        settings_reader=repository, file_repository=files,
        session=session, event_repository=events,
    )
    return SimpleNamespace(
        actor=actor, access=access, current=current,
        repository=repository, files=files,
        session=session, events=events, service=service,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_kind", ["foreign", "public"])
async def test_c002_rejects_unavailable_logo_before_update(bad_kind):
    case = make_case()
    logo_id = uuid4()
    logo = SimpleNamespace(
        id=logo_id, tenant_id=case.actor.tenant_id,
        entity_type="organization", entity_id=case.actor.tenant_id,
        status="ready", antivirus_status="clean",
        visibility_scope="private", public_url=None,
        storage_path=f"{case.actor.tenant_id}/logo.png",
        mime_type="image/png", file_type="image", size_bytes=1024,
    )
    if bad_kind == "foreign":
        logo.tenant_id = uuid4()
    else:
        logo.visibility_scope = "public"
    case.files.get_organization_downloadable_file.return_value = logo

    with pytest.raises(ValueError):
        await case.service.update_for_actor(
            actor=case.actor, access_context=case.access,
            expected_row_version=5,
            changes={"logo_file_id_override": logo_id},
        )

    case.repository.update_settings.assert_not_awaited()
    case.events.create_event.assert_not_awaited()
    case.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_c002_null_logo_override_restores_inheritance():
    previous = uuid4()
    case = make_case(existing_logo=previous)
    updated = SimpleNamespace(
        tenant_id=case.actor.tenant_id,
        logo_file_id_override=None,
    )
    case.repository.update_settings.return_value = updated

    result = await case.service.update_for_actor(
        actor=case.actor, access_context=case.access,
        expected_row_version=5,
        changes={"logo_file_id_override": None},
    )

    case.files.get_organization_downloadable_file.assert_not_awaited()
    case.repository.update_settings.assert_awaited_once_with(
        settings=case.current,
        values={"logo_file_id_override": None},
    )
    changes = case.events.create_event.await_args.kwargs["payload"]["changes"]
    assert changes == [{
        "field": "logo_file_id_override",
        "old_value": str(previous),
        "new_value": None,
    }]
    inherited = uuid4()
    assert resolve_construction_logo_file_id(
        profile=SimpleNamespace(logo_file_id=inherited),
        settings=result,
    ) == inherited
    case.session.commit.assert_awaited_once()
