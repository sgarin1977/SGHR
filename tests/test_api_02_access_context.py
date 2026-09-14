from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.api_identity import (
    ApiIdentityService,
)


@pytest.mark.asyncio
async def test_api_actor_loads_current_role_scopes():
    user_id = uuid4()
    tenant_id = uuid4()
    country_id = uuid4()

    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        active_role="admin",
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserService:
        async def get_user_by_id(self, value):
            assert value == user_id
            return user

    class FakeUserRepository:
        async def list_active_roles(
            self,
            value,
            *,
            tenant_id,
        ):
            assert value == user_id
            assert tenant_id == user.tenant_id
            return ["admin"]

        async def list_active_role_scopes(
            self,
            *,
            user_id,
            tenant_id,
            roles,
        ):
            assert roles == ("admin",)
            return [
                SimpleNamespace(
                    role="admin",
                    scope_type="country",
                    scope_id=country_id,
                    scope_code=None,
                )
            ]

        async def list_active_permissions(
            self,
            *,
            roles,
        ):
            return []

    service = ApiIdentityService(
        session=None,
        user_service=FakeUserService(),
        user_repository=FakeUserRepository(),
    )

    actor = await service.require_actor(
        user_id=user_id,
        tenant_id=tenant_id,
    )

    assert len(actor.role_scopes) == 1
    scope = actor.role_scopes[0]
    assert scope.role == "admin"
    assert scope.scope_type == "country"
    assert scope.scope_id == country_id
    assert scope.scope_code is None


@pytest.mark.asyncio
async def test_api_actor_roles_are_tenant_scoped():
    user_id = uuid4()
    tenant_id = uuid4()

    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserService:
        async def get_user_by_id(self, value):
            assert value == user_id
            return user

    class FakeUserRepository:
        async def list_active_roles(
            self,
            value,
            *,
            tenant_id,
        ):
            assert value == user_id
            assert tenant_id == user.tenant_id
            return ["specialist"]

        async def list_active_role_scopes(
            self,
            *,
            user_id,
            tenant_id,
            roles,
        ):
            return []

        async def list_active_permissions(
            self,
            *,
            roles,
        ):
            return []

    service = ApiIdentityService(
        session=None,
        user_service=FakeUserService(),
        user_repository=FakeUserRepository(),
    )

    actor = await service.require_actor(
        user_id=user_id,
        tenant_id=tenant_id,
    )

    assert actor.roles == ("specialist",)


@pytest.mark.asyncio
async def test_api_actor_loads_current_permissions():
    user_id = uuid4()
    tenant_id = uuid4()

    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserService:
        async def get_user_by_id(self, value):
            assert value == user_id
            return user

    class FakeUserRepository:
        async def list_active_roles(
            self,
            value,
            *,
            tenant_id,
        ):
            assert value == user_id
            assert tenant_id == user.tenant_id
            return [
                "client",
                "specialist",
            ]

        async def list_active_role_scopes(
            self,
            *,
            user_id,
            tenant_id,
            roles,
        ):
            return []

        async def list_active_permissions(
            self,
            *,
            roles,
        ):
            assert roles == (
                "client",
                "specialist",
            )
            return [
                "specialist.cabinets.write",
                "specialist.cabinets.read",
                "specialist.cabinets.read",
            ]

    service = ApiIdentityService(
        session=None,
        user_service=FakeUserService(),
        user_repository=FakeUserRepository(),
    )

    actor = await service.require_actor(
        user_id=user_id,
        tenant_id=tenant_id,
    )

    assert actor.permissions == (
        "specialist.cabinets.read",
        "specialist.cabinets.write",
    )


@pytest.mark.asyncio
async def test_api_permission_guard_is_fail_closed():
    from api.auth import require_permission
    from api.errors import ApiHttpError

    permission = (
        "specialist.cabinets.read"
    )
    guard = require_permission(permission)

    allowed_actor = SimpleNamespace(
        permissions=(permission,)
    )
    assert (
        await guard(allowed_actor)
        is allowed_actor
    )

    denied_actor = SimpleNamespace(
        permissions=()
    )

    with pytest.raises(ApiHttpError) as error:
        await guard(denied_actor)

    assert error.value.status_code == 403
    assert (
        error.value.code
        == "permission_required"
    )


@pytest.mark.asyncio
async def test_specialist_cabinets_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_cabinets_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(),
    )

    class ForbiddenService:
        async def list_cabinets_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Service must not be called "
                "without permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_cabinets_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/specialist/cabinets"
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )

