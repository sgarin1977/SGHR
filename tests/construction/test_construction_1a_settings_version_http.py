from construction_rate_limit_fixture import with_local_construction_rate_limit
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest


@pytest.mark.asyncio
async def test_c002_stale_version_returns_confirmed_http_error():
    from api.app import create_app
    from api.auth import get_current_actor
    import api.routes.construction_settings as routes
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsRowVersionConflictError,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    from services.construction.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    )
    context = ConstructionAccessContext(
        actor.user_id, actor.tenant_id, ("OWNER",),
        tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"])),
        (ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    service = SimpleNamespace(update_for_actor=AsyncMock(
        side_effect=ConstructionTenantSettingsRowVersionConflictError(
            "private-version-details"
        )
    ))
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[routes.get_construction_scoped_session] = lambda: object()
    app.dependency_overrides[routes.get_construction_access_context] = lambda: context
    app.dependency_overrides[routes.get_settings_service] = lambda: service
    for dependency in routes.router.dependencies[2:]:
        app.dependency_overrides[dependency.dependency] = lambda: actor

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            "/api/v1/construction/settings/company",
            json={"expected_row_version": 1, "display_name_override": "Crew"},
            headers={"X-Request-ID": "settings-version-test"},
        )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert body["error"]["request_id"] == "settings-version-test"
    assert "private-version-details" not in response.text
    service.update_for_actor.assert_awaited_once()
    assert service.update_for_actor.await_args.kwargs["expected_row_version"] == 1
