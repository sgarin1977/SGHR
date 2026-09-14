import re
from pathlib import Path

import pytest


def declared_packages():
    source = Path(
        "requirements.txt"
    ).read_text(
        encoding="utf-8-sig"
    )

    return {
        re.split(
            r"[<>=!~\[]",
            line.strip(),
            maxsplit=1,
        )[0].lower()
        for line in source.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
    }


def test_pyjwt_dependency_is_declared():
    assert "pyjwt" in declared_packages()


def create_token_codec(
    *,
    secret="s" * 64,
    access_ttl_seconds=900,
):
    from api.security import AccessTokenCodec

    return AccessTokenCodec(
        secret=secret,
        issuer="sghr-api",
        audience="sghr-core",
        access_ttl_seconds=(
            access_ttl_seconds
        ),
    )


def test_access_token_round_trip_uses_actor_ids():
    from uuid import uuid4

    user_id = uuid4()
    tenant_id = uuid4()
    codec = create_token_codec()

    token = codec.issue_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
    )
    claims = codec.decode_access_token(
        token
    )

    assert claims.user_id == user_id
    assert claims.tenant_id == tenant_id
    assert claims.token_id
    assert claims.expires_at > claims.issued_at


def test_access_token_contains_no_authorization_snapshot():
    from uuid import uuid4

    import jwt

    codec = create_token_codec()

    token = codec.issue_access_token(
        user_id=uuid4(),
        tenant_id=uuid4(),
    )

    payload = jwt.decode(
        token,
        options={
            "verify_signature": False,
        },
        algorithms=["HS256"],
    )

    assert payload["typ"] == "access"
    assert "roles" not in payload
    assert "permissions" not in payload
    assert "scopes" not in payload


def test_expired_access_token_is_rejected():
    from datetime import (
        UTC,
        datetime,
        timedelta,
    )
    from uuid import uuid4

    import pytest

    from api.security import (
        ApiAuthenticationError,
    )

    codec = create_token_codec(
        access_ttl_seconds=60,
    )
    old_time = (
        datetime.now(UTC)
        - timedelta(hours=1)
    )

    token = codec.issue_access_token(
        user_id=uuid4(),
        tenant_id=uuid4(),
        now=old_time,
    )

    with pytest.raises(
        ApiAuthenticationError
    ):
        codec.decode_access_token(token)


def test_access_token_with_wrong_signature_is_rejected():
    from uuid import uuid4

    import pytest

    from api.security import (
        ApiAuthenticationError,
    )

    issuing_codec = create_token_codec(
        secret="a" * 64,
    )
    verifying_codec = create_token_codec(
        secret="b" * 64,
    )

    token = (
        issuing_codec.issue_access_token(
            user_id=uuid4(),
            tenant_id=uuid4(),
        )
    )

    with pytest.raises(
        ApiAuthenticationError
    ):
        verifying_codec.decode_access_token(
            token
        )


def test_auth_settings_require_strong_secret():
    import pytest

    from api.settings import (
        ApiAuthSettings,
        ApiConfigurationError,
    )

    with pytest.raises(
        ApiConfigurationError
    ):
        ApiAuthSettings.from_env({})

    with pytest.raises(
        ApiConfigurationError
    ):
        ApiAuthSettings.from_env(
            {
                "API_JWT_SECRET": "too-short",
            }
        )


def test_auth_settings_have_safe_defaults():
    from api.settings import ApiAuthSettings

    settings = ApiAuthSettings.from_env(
        {
            "API_JWT_SECRET": "x" * 64,
        }
    )

    assert settings.jwt_secret == "x" * 64
    assert settings.jwt_issuer == "sghr-api"
    assert settings.jwt_audience == "sghr-core"
    assert (
        settings.access_token_ttl_seconds
        == 900
    )
    assert (
        settings.refresh_token_ttl_seconds
        == 2592000
    )


def test_auth_settings_are_documented():
    source = Path(".env.example").read_text(
        encoding="utf-8-sig"
    )

    assert "API_JWT_SECRET=" in source
    assert (
        "API_JWT_ISSUER=sghr-api"
        in source
    )
    assert (
        "API_JWT_AUDIENCE=sghr-core"
        in source
    )
    assert (
        "API_ACCESS_TOKEN_TTL_SECONDS=900"
        in source
    )
    assert (
        "API_REFRESH_TOKEN_TTL_SECONDS=2592000"
        in source
    )


class FakeApiIdentityUsers:
    def __init__(self, user):
        self.user = user
        self.calls = []

    async def get_user_by_id(
        self,
        user_id,
    ):
        self.calls.append(user_id)
        return self.user


class FakeApiIdentityRepository:
    def __init__(self, roles):
        self.roles = roles
        self.calls = []

    async def list_active_roles(
        self,
        user_id,
        *,
        tenant_id=None,
    ):
        self.calls.append(user_id)
        return list(self.roles)

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


@pytest.mark.asyncio
async def test_api_identity_loads_current_actor():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_identity import (
        ApiIdentityService,
    )

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
    users = FakeApiIdentityUsers(user)
    repository = (
        FakeApiIdentityRepository(
            [
                "client",
                "specialist",
            ]
        )
    )

    actor = await ApiIdentityService(
        session=None,
        user_service=users,
        user_repository=repository,
    ).require_actor(
        user_id=user_id,
        tenant_id=tenant_id,
    )

    assert actor.user_id == user_id
    assert actor.tenant_id == tenant_id
    assert actor.active_role == "specialist"
    assert actor.roles == (
        "client",
        "specialist",
    )
    assert actor.language_code == "uk"
    assert actor.timezone == "Europe/Kyiv"
    assert actor.status == "active"

    assert users.calls == [user_id]
    assert repository.calls == [user_id]


@pytest.mark.asyncio
async def test_api_identity_rejects_wrong_tenant():
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from services.api_identity import (
        ApiIdentityAccessError,
        ApiIdentityService,
    )

    user_id = uuid4()

    service = ApiIdentityService(
        session=None,
        user_service=FakeApiIdentityUsers(
            SimpleNamespace(
                id=user_id,
                tenant_id=uuid4(),
                active_role="client",
                language_code="en",
                timezone=None,
                status="active",
            )
        ),
        user_repository=(
            FakeApiIdentityRepository(
                ["client"]
            )
        ),
    )

    with pytest.raises(
        ApiIdentityAccessError
    ):
        await service.require_actor(
            user_id=user_id,
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_api_identity_rejects_inactive_user():
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from services.api_identity import (
        ApiIdentityAccessError,
        ApiIdentityService,
    )

    user_id = uuid4()
    tenant_id = uuid4()

    service = ApiIdentityService(
        session=None,
        user_service=FakeApiIdentityUsers(
            SimpleNamespace(
                id=user_id,
                tenant_id=tenant_id,
                active_role="client",
                language_code="en",
                timezone=None,
                status="blocked",
            )
        ),
        user_repository=(
            FakeApiIdentityRepository(
                ["client"]
            )
        ),
    )

    with pytest.raises(
        ApiIdentityAccessError
    ):
        await service.require_actor(
            user_id=user_id,
            tenant_id=tenant_id,
        )


@pytest.mark.asyncio
async def test_api_identity_drops_stale_active_role():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_identity import (
        ApiIdentityService,
    )

    user_id = uuid4()
    tenant_id = uuid4()

    actor = await ApiIdentityService(
        session=None,
        user_service=FakeApiIdentityUsers(
            SimpleNamespace(
                id=user_id,
                tenant_id=tenant_id,
                active_role="admin",
                language_code="en",
                timezone=None,
                status="active",
            )
        ),
        user_repository=(
            FakeApiIdentityRepository(
                ["client"]
            )
        ),
    ).require_actor(
        user_id=user_id,
        tenant_id=tenant_id,
    )

    assert actor.roles == ("client",)
    assert actor.active_role is None


async def create_auth_test_client():
    import httpx

    from fastapi import Depends

    from api.app import create_app
    from api.auth import require_bearer_token

    application = create_app()

    @application.get(
        "/api/v1/auth-test",
        include_in_schema=False,
    )
    async def auth_test(
        token: str = Depends(
            require_bearer_token
        ),
    ):
        return {
            "token": token,
        }

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    )


@pytest.mark.asyncio
async def test_api_rejects_missing_bearer_token():
    request_id = "missing-bearer-test"

    async with (
        await create_auth_test_client()
    ) as client:
        response = await client.get(
            "/api/v1/auth-test",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 401
    assert (
        response.headers["WWW-Authenticate"]
        == "Bearer"
    )
    assert (
        response.headers["X-Request-ID"]
        == request_id
    )
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": request_id,
        },
    }


@pytest.mark.asyncio
async def test_api_rejects_non_bearer_scheme():
    async with (
        await create_auth_test_client()
    ) as client:
        response = await client.get(
            "/api/v1/auth-test",
            headers={
                "Authorization": (
                    "Basic invalid-credentials"
                ),
            },
        )

    assert response.status_code == 401
    assert (
        response.headers["WWW-Authenticate"]
        == "Bearer"
    )


@pytest.mark.asyncio
async def test_api_accepts_bearer_token_transport():
    async with (
        await create_auth_test_client()
    ) as client:
        response = await client.get(
            "/api/v1/auth-test",
            headers={
                "Authorization": (
                    "Bearer signed-token"
                ),
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "token": "signed-token",
    }


class FakeAccessTokenCodec:
    def __init__(
        self,
        claims=None,
        error=None,
    ):
        self.claims = claims
        self.error = error
        self.tokens = []

    def decode_access_token(self, token):
        self.tokens.append(token)

        if self.error is not None:
            raise self.error

        return self.claims


class FakeCurrentActorIdentity:
    def __init__(
        self,
        actor=None,
        error=None,
    ):
        self.actor = actor
        self.error = error
        self.calls = []

    async def require_actor(
        self,
        *,
        user_id,
        tenant_id,
    ):
        self.calls.append(
            (user_id, tenant_id)
        )

        if self.error is not None:
            raise self.error

        return self.actor


@pytest.mark.asyncio
async def test_current_actor_uses_signed_claims():
    from types import SimpleNamespace
    from uuid import uuid4

    from api.auth import get_current_actor

    user_id = uuid4()
    tenant_id = uuid4()
    actor = object()

    codec = FakeAccessTokenCodec(
        claims=SimpleNamespace(
            user_id=user_id,
            tenant_id=tenant_id,
        )
    )
    identity = FakeCurrentActorIdentity(
        actor=actor
    )

    result = await get_current_actor(
        token="signed-access-token",
        codec=codec,
        identity_service=identity,
    )

    assert result is actor
    assert codec.tokens == [
        "signed-access-token",
    ]
    assert identity.calls == [
        (
            user_id,
            tenant_id,
        )
    ]


@pytest.mark.asyncio
async def test_current_actor_rejects_invalid_token():
    import pytest

    from api.auth import get_current_actor
    from api.errors import ApiHttpError
    from api.security import (
        ApiAuthenticationError,
    )

    codec = FakeAccessTokenCodec(
        error=ApiAuthenticationError(
            "private JWT details"
        )
    )

    with pytest.raises(
        ApiHttpError
    ) as captured:
        await get_current_actor(
            token="invalid-token",
            codec=codec,
            identity_service=(
                FakeCurrentActorIdentity()
            ),
        )

    error = captured.value

    assert error.status_code == 401
    assert error.code == "invalid_token"
    assert error.message == (
        "Invalid or expired access token."
    )
    assert error.headers == {
        "WWW-Authenticate": (
            'Bearer error="invalid_token"'
        ),
    }
    assert "private JWT details" not in str(
        error
    )


@pytest.mark.asyncio
async def test_current_actor_rejects_stale_identity():
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from api.auth import get_current_actor
    from api.errors import ApiHttpError
    from services.api_identity import (
        ApiIdentityAccessError,
    )

    identity = FakeCurrentActorIdentity(
        error=ApiIdentityAccessError(
            "private actor details"
        )
    )

    with pytest.raises(
        ApiHttpError
    ) as captured:
        await get_current_actor(
            token="valid-but-stale-token",
            codec=FakeAccessTokenCodec(
                claims=SimpleNamespace(
                    user_id=uuid4(),
                    tenant_id=uuid4(),
                )
            ),
            identity_service=identity,
        )

    error = captured.value

    assert error.status_code == 401
    assert error.code == "invalid_token"
    assert error.message == (
        "Invalid or expired access token."
    )
    assert "private actor details" not in str(
        error
    )


@pytest.mark.asyncio
async def test_me_requires_authentication():
    import httpx

    from api.app import create_app

    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me"
        )

    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "authentication_required",
        "message": "Authentication required.",
        "request_id": response.headers[
            "X-Request-ID"
        ],
    }


@pytest.mark.asyncio
async def test_me_returns_current_database_actor():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    request_id = "me-endpoint-test"

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=(
            "client",
            "specialist",
        ),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "id": str(user_id),
            "tenant_id": str(tenant_id),
            "active_role": "specialist",
            "roles": [
                "client",
                "specialist",
            ],
            "language_code": "uk",
            "timezone": "Europe/Kyiv",
            "status": "active",
        },
        "meta": {},
        "request_id": request_id,
    }


def test_me_is_documented_as_bearer_endpoint():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema[
        "paths"
    ]["/api/v1/me"]["get"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    security_scheme = schema[
        "components"
    ]["securitySchemes"]["BearerAuth"]

    assert security_scheme["type"] == "http"
    assert security_scheme["scheme"] == "bearer"

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )

    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "MeResponse"
        )
    }


@pytest.mark.asyncio
async def test_me_verifies_real_jwt_chain():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_access_token_codec,
        get_api_identity_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    codec = create_token_codec()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="en",
        timezone=None,
        status="active",
    )
    identity = FakeCurrentActorIdentity(
        actor=actor
    )

    application = create_app()
    application.dependency_overrides[
        get_access_token_codec
    ] = lambda: codec
    application.dependency_overrides[
        get_api_identity_service
    ] = lambda: identity

    token = codec.issue_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me",
            headers={
                "Authorization": (
                    f"Bearer {token}"
                ),
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["id"] == (
        str(user_id)
    )
    assert identity.calls == [
        (
            user_id,
            tenant_id,
        )
    ]


@pytest.mark.asyncio
async def test_me_invalid_jwt_never_loads_identity():
    import httpx

    from api.app import create_app
    from api.auth import (
        get_access_token_codec,
        get_api_identity_service,
    )

    codec = create_token_codec()
    identity = FakeCurrentActorIdentity()

    application = create_app()
    application.dependency_overrides[
        get_access_token_codec
    ] = lambda: codec
    application.dependency_overrides[
        get_api_identity_service
    ] = lambda: identity

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me",
            headers={
                "Authorization": (
                    "Bearer invalid-token"
                ),
            },
        )

    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "invalid_token",
        "message": (
            "Invalid or expired access token."
        ),
        "request_id": response.headers[
            "X-Request-ID"
        ],
    }
    assert identity.calls == []


class FakeApiRoleSession:
    def __init__(self, calls):
        self.calls = calls

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")


class FakeApiRoleRepository:
    def __init__(
        self,
        calls,
        user=None,
        error=None,
    ):
        self.calls = calls
        self.user = user
        self.error = error

    async def set_active_role(
        self,
        user_id,
        role,
    ):
        self.calls.append(
            (
                "set_active_role",
                user_id,
                role,
            )
        )

        if self.error is not None:
            raise self.error

        return self.user


class FakeApiRoleEvents:
    def __init__(self, calls):
        self.calls = calls

    async def create_event(
        self,
        **kwargs,
    ):
        self.calls.append(
            (
                "event",
                kwargs,
            )
        )


def make_api_actor(
    *,
    user_id,
    tenant_id,
    active_role="client",
    roles=("client", "specialist"),
):
    from services.api_identity import (
        ApiActorContext,
    )

    return ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role=active_role,
        roles=roles,
        language_code="en",
        timezone=None,
        status="active",
    )


@pytest.mark.asyncio
async def test_api_identity_switches_active_role():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_identity import (
        ApiIdentityService,
    )

    calls = []
    user_id = uuid4()
    tenant_id = uuid4()
    actor = make_api_actor(
        user_id=user_id,
        tenant_id=tenant_id,
    )

    service = ApiIdentityService(
        session=FakeApiRoleSession(calls),
        user_service=FakeApiIdentityUsers(
            None
        ),
        user_repository=(
            FakeApiRoleRepository(
                calls,
                user=SimpleNamespace(
                    id=user_id,
                    tenant_id=tenant_id,
                    active_role="specialist",
                ),
            )
        ),
        event_repository=(
            FakeApiRoleEvents(calls)
        ),
    )

    updated_actor = (
        await service.switch_active_role(
            actor=actor,
            role="specialist",
        )
    )

    assert updated_actor.active_role == (
        "specialist"
    )
    assert updated_actor.roles == actor.roles

    assert calls[0] == (
        "set_active_role",
        user_id,
        "specialist",
    )
    assert calls[1][0] == "event"
    assert calls[1][1] == {
        "event_type": "role_switched",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "entity_type": "user",
        "entity_id": user_id,
        "payload": {
            "active_role": "specialist",
            "available_roles": [
                "client",
                "specialist",
            ],
        },
        "platform": "api",
    }
    assert calls[2] == "commit"


@pytest.mark.asyncio
async def test_api_identity_rejects_unavailable_role():
    from uuid import uuid4

    import pytest

    from services.api_identity import (
        ApiIdentityRoleError,
        ApiIdentityService,
    )

    calls = []
    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        roles=("client",),
    )

    service = ApiIdentityService(
        session=FakeApiRoleSession(calls),
        user_service=FakeApiIdentityUsers(
            None
        ),
        user_repository=(
            FakeApiRoleRepository(calls)
        ),
        event_repository=(
            FakeApiRoleEvents(calls)
        ),
    )

    with pytest.raises(
        ApiIdentityRoleError
    ):
        await service.switch_active_role(
            actor=actor,
            role="admin",
        )

    assert calls == []


@pytest.mark.asyncio
async def test_api_identity_rolls_back_stale_role():
    from uuid import uuid4

    import pytest

    from services.api_identity import (
        ApiIdentityRoleError,
        ApiIdentityService,
    )

    calls = []
    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
    )

    service = ApiIdentityService(
        session=FakeApiRoleSession(calls),
        user_service=FakeApiIdentityUsers(
            None
        ),
        user_repository=(
            FakeApiRoleRepository(
                calls,
                error=ValueError(
                    "private repository details"
                ),
            )
        ),
        event_repository=(
            FakeApiRoleEvents(calls)
        ),
    )

    with pytest.raises(
        ApiIdentityRoleError
    ) as captured:
        await service.switch_active_role(
            actor=actor,
            role="specialist",
        )

    assert calls == [
        (
            "set_active_role",
            actor.user_id,
            "specialist",
        ),
        "rollback",
    ]
    assert (
        "private repository details"
        not in str(captured.value)
    )


class FakeApiRoleSwitchService:
    def __init__(
        self,
        result=None,
        error=None,
    ):
        self.result = result
        self.error = error
        self.calls = []

    async def switch_active_role(
        self,
        *,
        actor,
        role,
    ):
        self.calls.append(
            (
                actor,
                role,
            )
        )

        if self.error is not None:
            raise self.error

        return self.result


@pytest.mark.asyncio
async def test_me_roles_returns_current_roles():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor

    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me/roles"
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "active_role": "client",
        "roles": [
            "client",
            "specialist",
        ],
    }


@pytest.mark.asyncio
async def test_me_roles_switch_uses_identity_service():
    from dataclasses import replace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_identity_service,
        get_current_actor,
    )

    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
    )
    updated_actor = replace(
        actor,
        active_role="specialist",
    )
    service = FakeApiRoleSwitchService(
        result=updated_actor
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_identity_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/me/roles/switch",
            json={
                "role": "specialist",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "active_role": "specialist",
        "roles": [
            "client",
            "specialist",
        ],
    }
    assert service.calls == [
        (
            actor,
            "specialist",
        )
    ]


@pytest.mark.asyncio
async def test_me_roles_switch_rejects_unavailable_role():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_identity_service,
        get_current_actor,
    )
    from services.api_identity import (
        ApiIdentityRoleError,
    )

    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        roles=("client",),
    )
    service = FakeApiRoleSwitchService(
        error=ApiIdentityRoleError(
            "private role details"
        )
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_identity_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/me/roles/switch",
            json={
                "role": "admin",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "role_not_available",
        "message": (
            "Requested role is not available."
        ),
        "request_id": response.headers[
            "X-Request-ID"
        ],
    }
    assert (
        "private role details"
        not in response.text
    )


def test_me_role_endpoints_are_documented():
    from api.app import create_app

    schema = create_app().openapi()
    paths = schema["paths"]

    assert "/api/v1/me/roles" in paths
    assert (
        "/api/v1/me/roles/switch"
        in paths
    )

    assert paths[
        "/api/v1/me/roles"
    ]["get"]["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    assert paths[
        "/api/v1/me/roles/switch"
    ]["post"]["security"] == [
        {
            "BearerAuth": [],
        }
    ]


class FakeApiProfileTranslation:
    def __init__(
        self,
        result=None,
        error=None,
    ):
        self.result = result
        self.error = error
        self.calls = []

    async def update_interface_language(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return self.result


@pytest.mark.asyncio
async def test_api_identity_updates_language():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_identity import (
        ApiIdentityService,
    )

    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
    )
    translation = (
        FakeApiProfileTranslation(
            result=SimpleNamespace(
                interface_language="uk",
            )
        )
    )

    service = ApiIdentityService(
        session=None,
        user_service=FakeApiIdentityUsers(
            None
        ),
        user_repository=(
            FakeApiIdentityRepository([])
        ),
        event_repository=(
            FakeApiRoleEvents([])
        ),
        translation_service=translation,
    )

    updated_actor = (
        await service.update_profile(
            actor=actor,
            language_code="uk",
        )
    )

    assert updated_actor.language_code == "uk"
    assert translation.calls == [
        {
            "tenant_id": actor.tenant_id,
            "user_id": actor.user_id,
            "language_code": "uk",
            "source": "api_profile",
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_api_identity_hides_language_error():
    from uuid import uuid4

    import pytest

    from services.api_identity import (
        ApiIdentityProfileError,
        ApiIdentityService,
    )
    from services.translation import (
        TranslationError,
    )

    service = ApiIdentityService(
        session=None,
        user_service=FakeApiIdentityUsers(
            None
        ),
        user_repository=(
            FakeApiIdentityRepository([])
        ),
        event_repository=(
            FakeApiRoleEvents([])
        ),
        translation_service=(
            FakeApiProfileTranslation(
                error=TranslationError(
                    "private translation details"
                )
            )
        ),
    )

    with pytest.raises(
        ApiIdentityProfileError
    ) as captured:
        await service.update_profile(
            actor=make_api_actor(
                user_id=uuid4(),
                tenant_id=uuid4(),
            ),
            language_code="invalid",
        )

    assert (
        "private translation details"
        not in str(captured.value)
    )


def test_translation_language_event_accepts_platform():
    import ast

    source = Path(
        "services/translation.py"
    ).read_text(
        encoding="utf-8-sig"
    )
    tree = ast.parse(source)

    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "TranslationService"
    )
    method = next(
        node
        for node in service.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "update_interface_language"
    )

    arguments = {
        argument.arg
        for argument in (
            list(method.args.args)
            + list(method.args.kwonlyargs)
        )
    }
    block = (
        ast.get_source_segment(
            source,
            method,
        )
        or ""
    )

    assert "platform" in arguments
    assert "platform=platform" in (
        "".join(block.split())
    )


@pytest.mark.asyncio
async def test_api_identity_rejects_unsupported_language():
    from uuid import uuid4

    import pytest

    from services.api_identity import (
        ApiIdentityProfileValidationError,
        ApiIdentityService,
    )

    translation = FakeApiProfileTranslation()

    service = ApiIdentityService(
        session=None,
        user_service=FakeApiIdentityUsers(
            None
        ),
        user_repository=(
            FakeApiIdentityRepository([])
        ),
        event_repository=(
            FakeApiRoleEvents([])
        ),
        translation_service=translation,
    )

    with pytest.raises(
        ApiIdentityProfileValidationError
    ):
        await service.update_profile(
            actor=make_api_actor(
                user_id=uuid4(),
                tenant_id=uuid4(),
            ),
            language_code="unsupported",
        )

    assert translation.calls == []


class FakeApiProfileService:
    def __init__(
        self,
        result=None,
        error=None,
    ):
        self.result = result
        self.error = error
        self.calls = []

    async def update_profile(
        self,
        *,
        actor,
        language_code,
    ):
        self.calls.append(
            (
                actor,
                language_code,
            )
        )

        if self.error is not None:
            raise self.error

        return self.result


@pytest.mark.asyncio
async def test_patch_me_updates_allowed_profile_field():
    from dataclasses import replace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_identity_service,
        get_current_actor,
    )

    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
    )
    updated_actor = replace(
        actor,
        language_code="uk",
    )
    service = FakeApiProfileService(
        result=updated_actor
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_identity_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            "/api/v1/me",
            json={
                "language_code": "uk",
            },
        )

    assert response.status_code == 200
    assert (
        response.json()["data"]
        ["language_code"]
        == "uk"
    )
    assert service.calls == [
        (
            actor,
            "uk",
        )
    ]


@pytest.mark.asyncio
async def test_patch_me_rejects_forbidden_fields():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_identity_service,
        get_current_actor,
    )

    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
    )
    service = FakeApiProfileService()

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_identity_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            "/api/v1/me",
            json={
                "language_code": "uk",
                "tenant_id": str(uuid4()),
                "status": "active",
                "roles": ["super_admin"],
            },
        )

    assert response.status_code == 422
    assert response.json()["error"][
        "code"
    ] == "validation_error"
    assert service.calls == []


@pytest.mark.asyncio
async def test_patch_me_maps_profile_validation_error():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_identity_service,
        get_current_actor,
    )
    from services.api_identity import (
        ApiIdentityProfileValidationError,
    )

    actor = make_api_actor(
        user_id=uuid4(),
        tenant_id=uuid4(),
    )
    service = FakeApiProfileService(
        error=(
            ApiIdentityProfileValidationError(
                "private validation details"
            )
        )
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_identity_service
    ] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            "/api/v1/me",
            json={
                "language_code": "zz",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "profile_validation_error",
        "message": (
            "Profile update is not valid."
        ),
        "request_id": response.headers[
            "X-Request-ID"
        ],
    }
    assert (
        "private validation details"
        not in response.text
    )


def test_patch_me_is_documented():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema[
        "paths"
    ]["/api/v1/me"]["patch"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )

    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "MeResponse"
        )
    }
