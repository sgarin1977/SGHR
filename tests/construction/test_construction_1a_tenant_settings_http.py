from construction_rate_limit_fixture import with_local_construction_rate_limit, construction_module_dependencies
from services.construction.construction_permissions import CONSTRUCTION_ROLE_PERMISSIONS
def test_c002_company_settings_routes_are_registered():
    from api.app import create_app
    from services.construction.construction_permissions import CONSTRUCTION_PERMISSION_CODES

    paths = with_local_construction_rate_limit(create_app()).openapi()["paths"]
    methods = set(paths["/api/v1/construction/settings/company"])

    assert {"get", "post", "patch"} <= methods
    assert "construction.settings.manage" in CONSTRUCTION_PERMISSION_CODES


import pytest


@pytest.mark.asyncio
async def test_c002_get_allows_member_without_manage_permission():
    from types import SimpleNamespace
    from uuid import uuid4
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.routes.construction_settings import (
        get_construction_scoped_session,
        get_settings_service,
        router,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(),
        status="active", permissions=(),
    )
    calls = []

    class Service:
        async def get_for_actor(self, *, actor):
            calls.append(actor.tenant_id)
            return None

    app = with_local_construction_rate_limit(create_app())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        anonymous = await client.get(
            "/api/v1/construction/settings/company"
        )
        app.dependency_overrides[get_current_actor] = lambda: actor
        app.dependency_overrides[
            get_construction_scoped_session
        ] = lambda: object()
        app.dependency_overrides[get_settings_service] = lambda: Service()
        for dependency in construction_module_dependencies(router):
            app.dependency_overrides[dependency.dependency] = lambda: actor
        member = await client.get(
            "/api/v1/construction/settings/company"
        )

    assert anonymous.status_code == 401
    assert member.status_code == 200, member.text
    assert member.json()["data"] is None
    assert calls == [actor.tenant_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,payload", [
    ("post", {"display_name_override": "Denied"}),
    ("patch", {
        "expected_row_version": 1,
        "display_name_override": "Denied",
    }),
])
async def test_c002_writes_deny_role_without_tenant_manage(method, payload):
    from types import SimpleNamespace
    from uuid import uuid4
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.routes.construction_settings import (
        get_construction_access_context,
        get_construction_scoped_session,
        get_settings_service,
        router,
    )
    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsService,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("SURVEYOR",), permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("SURVEYOR", frozenset()))),
        grants=(ConstructionGrantContext(
            role="SURVEYOR", scope_type="project", scope_id=uuid4(),
        ),),
    )
    service = ConstructionTenantSettingsService(
        settings_reader=object(), session=object(),
        event_repository=object(),
    )
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[
        get_construction_scoped_session
    ] = lambda: object()
    app.dependency_overrides[
        get_construction_access_context
    ] = lambda: context
    app.dependency_overrides[get_settings_service] = lambda: service
    for dependency in construction_module_dependencies(router):
        app.dependency_overrides[dependency.dependency] = lambda: actor

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await getattr(client, method)(
            "/api/v1/construction/settings/company",
            json=payload,
        )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "permission_required"


@pytest.mark.asyncio
async def test_c002_owner_http_passes_verified_write_context():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.routes.construction_settings import (
        FIELDS, get_construction_access_context,
        get_construction_scoped_session, get_settings_service, router,
    )
    from services.construction.construction_permissions import (
        ConstructionAccessContext, ConstructionGrantContext,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",), permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=actor.tenant_id,
        ),),
    )
    settings = SimpleNamespace(**{field: None for field in FIELDS})
    settings.tenant_id = actor.tenant_id
    settings.row_version = 5
    service = SimpleNamespace(
        create_for_actor=AsyncMock(return_value=settings),
        update_for_actor=AsyncMock(return_value=settings),
    )
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[get_construction_scoped_session] = lambda: object()
    app.dependency_overrides[get_construction_access_context] = lambda: context
    app.dependency_overrides[get_settings_service] = lambda: service
    for dependency in construction_module_dependencies(router):
        app.dependency_overrides[dependency.dependency] = lambda: actor

    url = "/api/v1/construction/settings/company"
    headers = {"X-Request-ID": "c002-write-test"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        created = await client.post(
            url, json={"display_name_override": "Crew"}, headers=headers,
        )
        updated = await client.patch(
            url, json={
                "expected_row_version": 5,
                "display_name_override": "New Crew",
            }, headers=headers,
        )

    assert created.status_code == 201, created.text
    assert updated.status_code == 200, updated.text
    assert service.create_for_actor.await_args.kwargs == {
        "actor": actor, "access_context": context,
        "values": {"display_name_override": "Crew"},
        "trace_id": "c002-write-test",
    }
    assert service.update_for_actor.await_args.kwargs == {
        "actor": actor, "access_context": context,
        "expected_row_version": 5,
        "changes": {"display_name_override": "New Crew"},
        "trace_id": "c002-write-test",
    }


@pytest.mark.asyncio
async def test_c002_patch_missing_settings_returns_404():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.routes.construction_settings import (
        get_construction_access_context,
        get_construction_scoped_session,
        get_settings_service,
        router,
    )
    from services.construction.construction_permissions import (
        ConstructionAccessContext, ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsNotFoundError,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",), permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=actor.tenant_id,
        ),),
    )
    service = SimpleNamespace(update_for_actor=AsyncMock(
        side_effect=ConstructionTenantSettingsNotFoundError()
    ))
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[get_construction_scoped_session] = lambda: object()
    app.dependency_overrides[get_construction_access_context] = lambda: context
    app.dependency_overrides[get_settings_service] = lambda: service
    for dependency in construction_module_dependencies(router):
        app.dependency_overrides[dependency.dependency] = lambda: actor

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            "/api/v1/construction/settings/company",
            json={
                "expected_row_version": 1,
                "display_name_override": "Crew",
            },
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "construction_settings_not_found"
    service.update_for_actor.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post", "patch"])
async def test_c002_http_converts_logo_reference_to_uuid(method):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.routes.construction_settings import (
        FIELDS, get_construction_access_context,
        get_construction_scoped_session, get_settings_service, router,
    )
    from services.construction.construction_permissions import (
        ConstructionAccessContext, ConstructionGrantContext,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",), permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=actor.tenant_id,
        ),),
    )
    logo_id = uuid4()
    settings = SimpleNamespace(**{field: None for field in FIELDS})
    settings.tenant_id = actor.tenant_id
    settings.logo_file_id_override = logo_id
    settings.row_version = 2
    service = SimpleNamespace(
        create_for_actor=AsyncMock(return_value=settings),
        update_for_actor=AsyncMock(return_value=settings),
    )
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[get_construction_scoped_session] = lambda: object()
    app.dependency_overrides[get_construction_access_context] = lambda: context
    app.dependency_overrides[get_settings_service] = lambda: service
    for dependency in construction_module_dependencies(router):
        app.dependency_overrides[dependency.dependency] = lambda: actor

    payload = {"logo_file_id_override": str(logo_id)}
    if method == "patch":
        payload["expected_row_version"] = 1
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.request(
            method.upper(),
            "/api/v1/construction/settings/company",
            json=payload,
        )

    assert response.status_code == (201 if method == "post" else 200), response.text
    operation = service.create_for_actor if method == "post" else service.update_for_actor
    operation.assert_awaited_once()
    arguments = operation.await_args.kwargs
    fields = arguments["values" if method == "post" else "changes"]
    assert fields["logo_file_id_override"] == logo_id
