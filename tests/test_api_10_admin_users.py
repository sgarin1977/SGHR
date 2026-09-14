from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_admin_repository_lists_users_by_server_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.admin_scope import (
        AdminScopeContext,
    )
    from database.repositories.api_admin import (
        AdminApiRepository,
    )

    tenant_id = uuid4()
    country_id = uuid4()
    admin_user_id = uuid4()
    expected = [
        object(),
        object(),
    ]

    class FakeResult:
        def all(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    scope_context = AdminScopeContext(
        admin_user_id=admin_user_id,
        tenant_id=tenant_id,
        is_global=False,
        country_ids=frozenset(
            {country_id}
        ),
        language_codes=frozenset(
            {"uk"}
        ),
    )

    session = FakeSession()
    repository = AdminApiRepository(session)

    result = await repository.list_users(
        scope_context=scope_context,
        limit=21,
        offset=0,
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "FROM users" in sql
    assert "users.tenant_id" in sql
    assert str(tenant_id) in sql
    assert "users.country_id" in sql
    assert str(country_id) in sql
    assert "lower(users.language_code)" in sql
    assert "'uk'" in sql
    assert "LIMIT 21" in sql
    assert "OFFSET 0" in sql



@pytest.mark.asyncio
async def test_admin_repository_gets_user_by_server_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.admin_scope import (
        AdminScopeContext,
    )
    from database.repositories.api_admin import (
        AdminApiRepository,
    )

    tenant_id = uuid4()
    country_id = uuid4()
    admin_user_id = uuid4()
    target_user_id = uuid4()
    expected = object()

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    scope_context = AdminScopeContext(
        admin_user_id=admin_user_id,
        tenant_id=tenant_id,
        is_global=False,
        country_ids=frozenset(
            {country_id}
        ),
        language_codes=frozenset(
            {"uk"}
        ),
    )

    session = FakeSession()
    repository = AdminApiRepository(session)

    result = await repository.get_user(
        scope_context=scope_context,
        user_id=target_user_id,
    )

    assert result is expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "FROM users" in sql
    assert "users.tenant_id" in sql
    assert str(tenant_id) in sql
    assert "users.id" in sql
    assert str(target_user_id) in sql
    assert "users.country_id" in sql
    assert str(country_id) in sql
    assert "lower(users.language_code)" in sql
    assert "'uk'" in sql
    assert "LIMIT 1" in sql



@pytest.mark.asyncio
async def test_admin_users_service_uses_current_actor_scope():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_admin_users import (
        ApiAdminUserPage,
        ApiAdminUserView,
        ApiAdminUsersService,
    )
    from services.api_identity import (
        ApiActorContext,
        ApiRoleScopeContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    target_user_id = uuid4()
    country_id = uuid4()
    city_id = uuid4()
    created_at = datetime(
        2026,
        9,
        4,
        19,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.users.read",
        ),
        role_scopes=(
            ApiRoleScopeContext(
                role="admin",
                scope_type="country",
                scope_id=country_id,
                scope_code=None,
            ),
            ApiRoleScopeContext(
                role="admin",
                scope_type="language",
                scope_id=None,
                scope_code="UK",
            ),
        ),
    )

    user = SimpleNamespace(
        id=target_user_id,
        tenant_id=tenant_id,
        active_role="client",
        language_code="uk",
        country_id=country_id,
        city_id=city_id,
        status="active",
        last_seen_at=None,
        created_at=created_at,
        extra_metadata={
            "private": "must-not-leak",
        },
        risk_score=90,
        trust_score=5,
    )

    class FakeRepository:
        async def list_users(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return [user, user, user]

    service = ApiAdminUsersService(
        repository=FakeRepository(),
    )

    result = await service.list_users(
        actor=actor,
        page=1,
        page_size=2,
    )

    assert isinstance(result, ApiAdminUserPage)
    assert len(result.items) == 2
    assert result.page == 1
    assert result.has_next is True

    item = result.items[0]
    assert isinstance(item, ApiAdminUserView)
    assert item.id == target_user_id
    assert item.active_role == "client"
    assert item.language_code == "uk"
    assert item.country_id == country_id
    assert item.city_id == city_id
    assert item.status == "active"
    assert item.created_at == created_at

    assert not hasattr(item, "tenant_id")
    assert not hasattr(item, "metadata")
    assert not hasattr(item, "extra_metadata")
    assert not hasattr(item, "risk_score")
    assert not hasattr(item, "trust_score")

    assert len(calls) == 1
    scope_context = calls[0][
        "scope_context"
    ]

    assert (
        scope_context.admin_user_id
        == admin_user_id
    )
    assert scope_context.tenant_id == tenant_id
    assert scope_context.is_global is False
    assert scope_context.country_ids == (
        frozenset({country_id})
    )
    assert scope_context.language_codes == (
        frozenset({"uk"})
    )
    assert calls[0]["limit"] == 3
    assert calls[0]["offset"] == 2



@pytest.mark.asyncio
async def test_admin_user_detail_uses_current_actor_scope():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_admin_users import (
        ApiAdminUserView,
        ApiAdminUsersService,
    )
    from services.api_identity import (
        ApiActorContext,
        ApiRoleScopeContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    target_user_id = uuid4()
    country_id = uuid4()
    created_at = datetime(
        2026,
        9,
        4,
        20,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.users.read",
        ),
        role_scopes=(
            ApiRoleScopeContext(
                role="moderator",
                scope_type="country",
                scope_id=country_id,
                scope_code=None,
            ),
        ),
    )

    user = SimpleNamespace(
        id=target_user_id,
        tenant_id=tenant_id,
        active_role="client",
        language_code="uk",
        country_id=country_id,
        city_id=None,
        status="active",
        last_seen_at=None,
        created_at=created_at,
        extra_metadata={
            "private": "must-not-leak",
        },
    )

    class FakeRepository:
        async def get_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return user

    service = ApiAdminUsersService(
        repository=FakeRepository(),
    )

    result = await service.get_user(
        actor=actor,
        user_id=target_user_id,
    )

    assert isinstance(result, ApiAdminUserView)
    assert result.id == target_user_id
    assert not hasattr(result, "tenant_id")
    assert not hasattr(result, "metadata")

    assert len(calls) == 1
    assert calls[0]["user_id"] == (
        target_user_id
    )

    scope_context = calls[0][
        "scope_context"
    ]
    assert (
        scope_context.admin_user_id
        == admin_user_id
    )
    assert scope_context.tenant_id == tenant_id
    assert scope_context.is_global is False
    assert scope_context.country_ids == (
        frozenset({country_id})
    )



@pytest.mark.asyncio
async def test_admin_users_endpoint_uses_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_users_service,
    )
    from services.api_admin_users import (
        ApiAdminUserPage,
        ApiAdminUserView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    target_user_id = uuid4()
    created_at = datetime(
        2026,
        9,
        4,
        21,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.users.read",
        ),
    )

    class FakeService:
        async def list_users(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiAdminUserPage(
                items=(
                    ApiAdminUserView(
                        id=target_user_id,
                        active_role="client",
                        language_code="uk",
                        country_id=None,
                        city_id=None,
                        status="active",
                        last_seen_at=None,
                        created_at=created_at,
                    ),
                ),
                page=0,
                has_next=False,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_users_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/admin/users",
            params={
                "limit": 20,
            },
            headers={
                "X-Request-ID": (
                    "admin-users-list"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["data"][0]["id"] == (
        str(target_user_id)
    )
    assert "tenant_id" not in (
        payload["data"][0]
    )
    assert calls == [
        {
            "actor": actor,
            "page": 0,
            "page_size": 20,
        }
    ]

    openapi = application.openapi()
    parameters = openapi["paths"][
        "/api/v1/admin/users"
    ]["get"].get("parameters", [])

    assert all(
        parameter["name"] != "tenant_id"
        for parameter in parameters
    )



@pytest.mark.asyncio
async def test_admin_user_detail_endpoint_uses_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_users_service,
    )
    from services.api_admin_users import (
        ApiAdminUserView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    target_user_id = uuid4()
    created_at = datetime(
        2026,
        9,
        4,
        22,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.users.read",
        ),
    )

    class FakeService:
        async def get_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiAdminUserView(
                id=target_user_id,
                active_role="client",
                language_code="uk",
                country_id=None,
                city_id=None,
                status="active",
                last_seen_at=None,
                created_at=created_at,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_users_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/admin/users/"
                f"{target_user_id}"
            ),
            headers={
                "X-Request-ID": (
                    "admin-user-detail"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["data"]["id"] == (
        str(target_user_id)
    )
    assert "tenant_id" not in payload["data"]
    assert calls == [
        {
            "actor": actor,
            "user_id": target_user_id,
        }
    ]
