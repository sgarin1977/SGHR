from construction_rate_limit_fixture import with_local_construction_rate_limit, construction_module_dependencies
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from database.repositories.construction.construction_team import (
    ConstructionTeamRepository,
)


@pytest.mark.asyncio
async def test_con_c004_lists_only_active_tenant_team_grants():
    tenant_id = uuid4()
    project_id = uuid4()
    as_of = datetime.now(timezone.utc)
    statements = []
    grants = [object()]

    class Result:
        def scalars(self):
            return self

        def all(self):
            return grants

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return Result()

    repository = ConstructionTeamRepository(Session())
    assert await repository.list_active_grants(
        tenant_id=tenant_id,
        as_of=as_of,
    ) == grants
    assert await repository.list_active_grants(
        tenant_id=tenant_id,
        as_of=as_of,
        project_id=project_id,
    ) == grants

    assert len(statements) == 2
    tenant_sql = str(
        statements[0].compile(dialect=postgresql.dialect())
    ).lower().partition("where")[2].partition("order by")[0]
    project_compiled = statements[1].compile(
        dialect=postgresql.dialect()
    )
    project_sql = str(project_compiled).lower().partition(
        "where"
    )[2].partition("order by")[0]

    assert "construction_access_grants.tenant_id" in tenant_sql
    assert "construction_access_grants.status" in tenant_sql
    assert "construction_access_grants.expires_at" in tenant_sql
    assert "construction_access_grants.scope_type" not in tenant_sql
    assert "construction_access_grants.scope_type" in project_sql
    assert "construction_access_grants.scope_id" in project_sql
    assert tenant_id in project_compiled.params.values()
    assert project_id in project_compiled.params.values()


@pytest.mark.asyncio
async def test_con_c004_team_service_enforces_scope():
    from types import SimpleNamespace

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
        ConstructionScopeDeniedError,
    )
    from services.construction.construction_team import ConstructionTeamService

    user_id = uuid4()
    tenant_id = uuid4()
    project_id = uuid4()
    as_of = datetime.now(timezone.utc)
    actor = SimpleNamespace(
        user_id=user_id,
        tenant_id=tenant_id,
        status="active",
    )
    calls = []

    class Repository:
        async def list_active_grants(self, **kwargs):
            calls.append(kwargs)
            return ["visible"]

    service = ConstructionTeamService(
        repository=Repository(),
        now_provider=lambda: as_of,
    )
    owner_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=("construction.team.manage",),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    assert await service.list_team_grants(
        actor=actor,
        access_context=owner_context,
    ) == ["visible"]
    assert calls[-1] == {
        "tenant_id": tenant_id,
        "as_of": as_of,
        "project_id": None,
    }

    project_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=("construction.team.manage",),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    assert await service.list_team_grants(
        actor=actor,
        access_context=project_context,
        project_id=project_id,
    ) == ["visible"]

    calls.clear()
    with pytest.raises(ConstructionScopeDeniedError):
        await service.list_team_grants(
            actor=actor,
            access_context=project_context,
        )
    with pytest.raises(ConstructionScopeDeniedError):
        await service.list_team_grants(
            actor=actor,
            access_context=project_context,
            project_id=uuid4(),
        )
    assert calls == []

    no_team_permission = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=("construction.access.manage",),
        grants=owner_context.grants,
    )
    with pytest.raises(ConstructionPermissionDeniedError):
        await service.list_team_grants(
            actor=actor,
            access_context=no_team_permission,
        )
    assert calls == []


def test_con_c004_team_grants_http_route_is_read_only_and_guarded():
    from api.routes.construction import (
        get_construction_scoped_session,
        router,
    )

    routes = [
        route
        for route in router.routes
        if getattr(route, "path", None)
        == "/construction/team/access-grants"
    ]
    assert len(routes) == 1
    route = routes[0]
    assert route.methods == {"GET"}

    dependency_calls = {
        dependency.call
        for dependency in route.dependant.dependencies
    }
    assert get_construction_scoped_session in dependency_calls
    assert any(
        getattr(
            call,
            "construction_permission_code",
            None,
        ) == "construction.team.manage"
        for call in dependency_calls
    )


@pytest.mark.asyncio
async def test_con_c004_http_accepts_role_policy_without_platform_permission(
    monkeypatch,
):
    from types import SimpleNamespace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    import api.routes.construction as routes
    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=(),
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        roles=("OWNER",),
        permissions=("construction.team.manage",),
        grants=(ConstructionGrantContext(
            role="OWNER",
            scope_type="tenant",
            scope_id=actor.tenant_id,
        ),),
    )

    class Resolver:
        async def resolve(self, *, actor):
            return context

    class Service:
        async def list_team_grants(self, **kwargs):
            return []

    monkeypatch.setattr(
        routes, "build_construction_team_access",
        lambda session: (Service(), Resolver()),
    )
    monkeypatch.setattr(
        routes, "build_construction_access_context_resolver",
        lambda session: Resolver(), raising=False,
    )
    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        routes.get_construction_scoped_session
    ] = lambda: object()
    for dependency in construction_module_dependencies(routes.router):
        application.dependency_overrides[dependency.dependency] = lambda: actor

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application, raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/construction/team/access-grants"
        )

    assert response.status_code == 200
    assert response.json()["data"] == []
