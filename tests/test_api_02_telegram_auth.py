from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from urllib.parse import urlencode


def signed_init_data(
    *,
    bot_token: str,
    auth_date: int,
) -> str:
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(
            {
                "id": 123456789,
                "first_name": "Nazar",
                "last_name": "Test",
                "username": "nazar_test",
                "language_code": "uk",
            },
            separators=(",", ":"),
        ),
    }

    data_check_string = "\n".join(
        f"{key}={values[key]}"
        for key in sorted(values)
    )
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return urlencode(
        {
            **values,
            "hash": signature,
        }
    )


def test_telegram_init_data_signature_is_verified():
    from api.telegram_auth import (
        TelegramInitDataVerifier,
    )

    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    bot_token = "123456:telegram-test-token"

    verifier = TelegramInitDataVerifier(
        bot_token=bot_token,
        max_age_seconds=300,
    )

    identity = verifier.verify(
        signed_init_data(
            bot_token=bot_token,
            auth_date=int(now.timestamp()),
        ),
        now=now,
    )

    assert identity.platform_user_id == 123456789
    assert identity.username == "nazar_test"
    assert identity.first_name == "Nazar"
    assert identity.last_name == "Test"
    assert identity.language_code == "uk"


def test_tampered_telegram_init_data_is_rejected():
    from api.telegram_auth import (
        TelegramAuthenticationError,
        TelegramInitDataVerifier,
    )

    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    bot_token = "123456:telegram-test-token"
    init_data = signed_init_data(
        bot_token=bot_token,
        auth_date=int(now.timestamp()),
    )
    tampered_data = init_data.replace(
        "Nazar",
        "Changed",
        1,
    )

    verifier = TelegramInitDataVerifier(
        bot_token=bot_token,
        max_age_seconds=300,
    )

    with pytest.raises(
        TelegramAuthenticationError,
        match=(
            "Telegram authentication data "
            "is invalid"
        ),
    ):
        verifier.verify(
            tampered_data,
            now=now,
        )


def test_expired_telegram_init_data_is_rejected():
    from api.telegram_auth import (
        TelegramAuthenticationError,
        TelegramInitDataVerifier,
    )

    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    bot_token = "123456:telegram-test-token"

    verifier = TelegramInitDataVerifier(
        bot_token=bot_token,
        max_age_seconds=300,
    )

    with pytest.raises(
        TelegramAuthenticationError,
        match=(
            "Telegram authentication data "
            "is invalid"
        ),
    ):
        verifier.verify(
            signed_init_data(
                bot_token=bot_token,
                auth_date=(
                    int(now.timestamp()) - 301
                ),
            ),
            now=now,
        )


def test_telegram_auth_settings_have_safe_defaults():
    from api.settings import (
        ApiTelegramAuthSettings,
    )

    settings = (
        ApiTelegramAuthSettings.from_env(
            {
                "BOT_TOKEN": (
                    "123456:telegram-test-token"
                ),
            }
        )
    )

    assert settings.bot_token == (
        "123456:telegram-test-token"
    )
    assert settings.max_age_seconds == 300


def test_telegram_auth_settings_require_bot_token():
    from api.settings import (
        ApiConfigurationError,
        ApiTelegramAuthSettings,
    )

    with pytest.raises(
        ApiConfigurationError,
        match="BOT_TOKEN",
    ):
        ApiTelegramAuthSettings.from_env(
            {
                "BOT_TOKEN": "",
            }
        )


def test_telegram_auth_settings_are_documented():
    source = Path(".env.example").read_text(
        encoding="utf-8-sig"
    )

    assert (
        "API_TELEGRAM_AUTH_MAX_AGE_SECONDS=300"
        in source
    )


def test_refresh_token_is_opaque_and_hashable():
    from api.security import RefreshTokenCodec

    codec = RefreshTokenCodec()

    material = codec.issue_refresh_token()

    assert isinstance(material.token, str)
    assert len(material.token) >= 64
    assert material.token != material.token_hash
    assert len(material.token_hash) == 64

    assert codec.hash_refresh_token(
        material.token
    ) == material.token_hash

    assert codec.verify_refresh_token(
        material.token,
        material.token_hash,
    )
    assert not codec.verify_refresh_token(
        material.token + "changed",
        material.token_hash,
    )


@pytest.mark.asyncio
async def test_telegram_auth_service_creates_token_session():
    from datetime import timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_auth import ApiAuthService

    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    user_id = uuid4()
    tenant_id = uuid4()
    session_id = uuid4()
    calls = []

    class FakeDatabaseSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeTelegramVerifier:
        def verify(
            self,
            init_data,
            *,
            now,
        ):
            calls.append(
                (
                    "verify",
                    init_data,
                    now,
                )
            )
            return SimpleNamespace(
                platform_user_id=123456789,
                username="nazar_test",
                first_name="Nazar",
                last_name="Test",
                language_code="uk",
            )

    class FakeUserService:
        async def register_telegram_user(
            self,
            data,
        ):
            calls.append(
                (
                    "register",
                    data,
                )
            )
            return SimpleNamespace(
                user_id=user_id,
                role="client",
                is_new=False,
            )

        async def get_user_by_id(self, value):
            calls.append(
                (
                    "load_user",
                    value,
                )
            )
            return SimpleNamespace(
                id=user_id,
                tenant_id=tenant_id,
                status="active",
            )

    class FakeAccessTokenCodec:
        access_ttl_seconds = 900

        def issue_access_token(
            self,
            *,
            user_id,
            tenant_id,
            now,
        ):
            calls.append(
                (
                    "access_token",
                    user_id,
                    tenant_id,
                    now,
                )
            )
            return "signed-access-token"

    class FakeRefreshTokenCodec:
        def issue_refresh_token(self):
            calls.append(
                (
                    "refresh_token",
                )
            )
            return SimpleNamespace(
                token="opaque-refresh-token",
                token_hash="a" * 64,
            )

    class FakeSessionRepository:
        async def create_session(
            self,
            **values,
        ):
            calls.append(
                (
                    "create_session",
                    values,
                )
            )
            return SimpleNamespace(
                id=session_id,
            )

    database_session = FakeDatabaseSession()
    service = ApiAuthService(
        session=database_session,
        telegram_verifier=(
            FakeTelegramVerifier()
        ),
        user_service=FakeUserService(),
        access_token_codec=(
            FakeAccessTokenCodec()
        ),
        refresh_token_codec=(
            FakeRefreshTokenCodec()
        ),
        session_repository=(
            FakeSessionRepository()
        ),
        refresh_token_ttl_seconds=2592000,
    )

    result = await service.authenticate_telegram(
        init_data="signed-telegram-init-data",
        device_id="device-123",
        device_name="Test phone",
        now=now,
    )

    registration_data = next(
        call[1]
        for call in calls
        if call[0] == "register"
    )
    assert (
        registration_data.platform_user_id
        == "123456789"
    )
    assert registration_data.username == "nazar_test"
    assert registration_data.first_name == "Nazar"
    assert registration_data.last_name == "Test"
    assert registration_data.language_code == "uk"

    session_values = next(
        call[1]
        for call in calls
        if call[0] == "create_session"
    )
    assert session_values == {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "refresh_token_hash": "a" * 64,
        "auth_method": "telegram",
        "device_id": "device-123",
        "device_name": "Test phone",
        "expires_at": (
            now + timedelta(days=30)
        ),
    }

    assert result.access_token == (
        "signed-access-token"
    )
    assert result.refresh_token == (
        "opaque-refresh-token"
    )
    assert result.token_type == "bearer"
    assert result.expires_in == 900

    assert database_session.commits == 1
    assert database_session.rollbacks == 0


@pytest.mark.asyncio
async def test_telegram_auth_endpoint_returns_token_pair():
    import httpx

    from api.app import create_app
    from api.auth import get_api_auth_service
    from services.api_auth import ApiTokenPair

    calls = []
    request_id = "telegram-auth-request"

    class FakeApiAuthService:
        async def authenticate_telegram(
            self,
            *,
            init_data,
            device_id,
            device_name,
        ):
            calls.append(
                {
                    "init_data": init_data,
                    "device_id": device_id,
                    "device_name": device_name,
                }
            )
            return ApiTokenPair(
                access_token=(
                    "signed-access-token"
                ),
                refresh_token=(
                    "opaque-refresh-token"
                ),
                token_type="bearer",
                expires_in=900,
            )

    application = create_app()
    application.dependency_overrides[
        get_api_auth_service
    ] = lambda: FakeApiAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/telegram",
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "init_data": (
                    "signed-telegram-init-data"
                ),
                "device_id": "device-123",
                "device_name": "Test phone",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "init_data": (
                "signed-telegram-init-data"
            ),
            "device_id": "device-123",
            "device_name": "Test phone",
        }
    ]
    assert response.json() == {
        "data": {
            "access_token": (
                "signed-access-token"
            ),
            "refresh_token": (
                "opaque-refresh-token"
            ),
            "token_type": "bearer",
            "expires_in": 900,
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_telegram_auth_endpoint_rejects_invalid_signature():
    import httpx

    from api.app import create_app
    from api.auth import get_api_auth_service
    from api.telegram_auth import (
        TelegramAuthenticationError,
    )

    request_id = "invalid-telegram-auth"

    class InvalidAuthService:
        async def authenticate_telegram(
            self,
            **_values,
        ):
            raise TelegramAuthenticationError(
                "private verification details"
            )

    application = create_app()
    application.dependency_overrides[
        get_api_auth_service
    ] = lambda: InvalidAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/telegram",
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "init_data": "tampered-data",
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "invalid_telegram_auth",
            "message": (
                "Telegram authentication "
                "data is invalid."
            ),
            "request_id": request_id,
        },
    }
    assert (
        "private verification details"
        not in response.text
    )


@pytest.mark.asyncio
async def test_telegram_auth_rejects_client_actor_ids():
    import httpx

    from api.app import create_app
    from api.auth import get_api_auth_service

    class ForbiddenAuthService:
        async def authenticate_telegram(
            self,
            **_values,
        ):
            raise AssertionError(
                "Service must not receive "
                "client actor identifiers."
            )

    application = create_app()
    application.dependency_overrides[
        get_api_auth_service
    ] = lambda: ForbiddenAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/telegram",
            json={
                "init_data": (
                    "signed-telegram-init-data"
                ),
                "user_id": (
                    "00000000-0000-0000-"
                    "0000-000000000001"
                ),
                "tenant_id": (
                    "00000000-0000-0000-"
                    "0000-000000000002"
                ),
            },
        )

    assert response.status_code == 422
    assert response.json()[
        "error"
    ]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_refresh_endpoint_rotates_token_pair():
    import httpx

    from api.app import create_app
    from api.auth import get_api_auth_service
    from services.api_auth import ApiTokenPair

    calls = []
    request_id = "refresh-token-request"

    class FakeApiAuthService:
        async def refresh_tokens(
            self,
            *,
            refresh_token,
        ):
            calls.append(refresh_token)
            return ApiTokenPair(
                access_token=(
                    "rotated-access-token"
                ),
                refresh_token=(
                    "rotated-refresh-token"
                ),
                token_type="bearer",
                expires_in=900,
            )

    application = create_app()
    application.dependency_overrides[
        get_api_auth_service
    ] = lambda: FakeApiAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "refresh_token": (
                    "old-refresh-token"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == ["old-refresh-token"]
    assert response.json() == {
        "data": {
            "access_token": (
                "rotated-access-token"
            ),
            "refresh_token": (
                "rotated-refresh-token"
            ),
            "token_type": "bearer",
            "expires_in": 900,
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_refresh_endpoint_rejects_invalid_token():
    import httpx

    from api.app import create_app
    from api.auth import get_api_auth_service
    from services.api_auth import (
        ApiAuthRefreshError,
    )

    request_id = "invalid-refresh-token"

    class InvalidRefreshService:
        async def refresh_tokens(
            self,
            **_values,
        ):
            raise ApiAuthRefreshError(
                "private session details"
            )

    application = create_app()
    application.dependency_overrides[
        get_api_auth_service
    ] = lambda: InvalidRefreshService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "refresh_token": (
                    "expired-or-replayed-token"
                ),
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "invalid_refresh_token",
            "message": (
                "Refresh token is invalid "
                "or expired."
            ),
            "request_id": request_id,
        },
    }
    assert (
        "private session details"
        not in response.text
    )


@pytest.mark.asyncio
async def test_logout_endpoint_revokes_refresh_session():
    import httpx

    from api.app import create_app
    from api.auth import get_api_auth_service

    calls = []
    request_id = "logout-request"

    class FakeApiAuthService:
        async def logout(
            self,
            *,
            refresh_token,
        ):
            calls.append(refresh_token)

    application = create_app()
    application.dependency_overrides[
        get_api_auth_service
    ] = lambda: FakeApiAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/logout",
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "refresh_token": (
                    "active-refresh-token"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == ["active-refresh-token"]
    assert response.json() == {
        "data": {
            "logged_out": True,
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_sessions_endpoint_uses_current_actor():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_auth_service,
        get_current_actor,
    )
    from services.api_auth import (
        ApiAuthSessionView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    session_id = uuid4()
    request_id = "auth-sessions-request"
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )
    created_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    expires_at = datetime(
        2026,
        9,
        25,
        12,
        0,
        tzinfo=UTC,
    )

    class FakeApiAuthService:
        async def list_sessions(
            self,
            *,
            tenant_id,
            user_id,
        ):
            calls.append(
                (
                    tenant_id,
                    user_id,
                )
            )
            return (
                ApiAuthSessionView(
                    id=session_id,
                    auth_method="telegram",
                    device_id="device-123",
                    device_name="Test phone",
                    status="active",
                    expires_at=expires_at,
                    last_used_at=None,
                    created_at=created_at,
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_auth_service
    ] = lambda: FakeApiAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/auth/sessions",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert calls == [
        (
            tenant_id,
            user_id,
        )
    ]
    assert response.json() == {
        "data": {
            "items": [
                {
                    "id": str(session_id),
                    "auth_method": "telegram",
                    "device_id": "device-123",
                    "device_name": "Test phone",
                    "status": "active",
                    "expires_at": (
                        "2026-09-25T12:00:00Z"
                    ),
                    "last_used_at": None,
                    "created_at": (
                        "2026-08-20T10:00:00Z"
                    ),
                }
            ],
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_sessions_endpoint_requires_authentication():
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
            "/api/v1/auth/sessions"
        )

    assert response.status_code == 401
    assert response.json()[
        "error"
    ]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_successful_telegram_auth_is_security_audited():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from services.api_auth import (
        ApiAuthService,
        ApiTokenPair,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    now = datetime(
        2026,
        8,
        28,
        23,
        0,
        tzinfo=UTC,
    )
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        status="active",
    )
    tokens = ApiTokenPair(
        access_token="telegram-access-token",
        refresh_token=(
            "telegram-refresh-token"
        ),
        token_type="bearer",
        expires_in=900,
    )

    class FakeUserService:
        async def register_telegram_user(
            self,
            data,
        ):
            return SimpleNamespace(
                user_id=user_id
            )

        async def get_user_by_id(
            self,
            value,
        ):
            assert value == user_id
            return user

    verifier = SimpleNamespace(
        verify=Mock(
            return_value=SimpleNamespace(
                platform_user_id=123456789,
                username="test_user",
                first_name="Test",
                last_name="User",
                language_code="uk",
            )
        )
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    events = SimpleNamespace(
        create_event=AsyncMock(),
    )

    service = ApiAuthService(
        session=session,
        telegram_verifier=verifier,
        user_service=FakeUserService(),
        access_token_codec=object(),
        refresh_token_codec=object(),
        session_repository=object(),
        event_repository=events,
        refresh_token_ttl_seconds=2592000,
    )
    service.prepare_token_pair = AsyncMock(
        return_value=tokens
    )

    result = await (
        service.authenticate_telegram(
            init_data="signed-init-data",
            device_id="telegram-device",
            device_name="Telegram Mini App",
            now=now,
        )
    )

    assert result is tokens
    events.create_event.assert_awaited_once_with(
        event_type=(
            "api_telegram_auth_succeeded"
        ),
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        payload={
            "auth_method": "telegram",
        },
        platform="api",
    )

    payload = (
        events.create_event
        .await_args.kwargs["payload"]
    )
    assert "init_data" not in payload


@pytest.mark.asyncio
async def test_invalid_telegram_auth_is_security_audited():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import pytest

    from api.telegram_auth import (
        TelegramAuthenticationError,
    )
    from services.api_auth import ApiAuthService

    now = datetime(
        2026,
        8,
        28,
        23,
        30,
        tzinfo=UTC,
    )
    verifier = SimpleNamespace(
        verify=Mock(
            side_effect=(
                TelegramAuthenticationError(
                    "private signature details"
                )
            )
        )
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    events = SimpleNamespace(
        create_event=AsyncMock(),
    )

    service = ApiAuthService(
        session=session,
        telegram_verifier=verifier,
        user_service=object(),
        access_token_codec=object(),
        refresh_token_codec=object(),
        session_repository=object(),
        event_repository=events,
        refresh_token_ttl_seconds=2592000,
    )

    with pytest.raises(
        TelegramAuthenticationError
    ):
        await service.authenticate_telegram(
            init_data=(
                "private-invalid-init-data"
            ),
            device_id=None,
            device_name=None,
            now=now,
        )

    events.create_event.assert_awaited_once_with(
        event_type=(
            "api_telegram_auth_failed"
        ),
        tenant_id=None,
        user_id=None,
        entity_type=None,
        entity_id=None,
        payload={
            "auth_method": "telegram",
            "reason": "invalid_init_data",
        },
        platform="api",
    )
    session.commit.assert_awaited_once()

    payload = (
        events.create_event
        .await_args.kwargs["payload"]
    )
    assert "init_data" not in payload
    assert (
        "private-invalid-init-data"
        not in str(payload)
    )
