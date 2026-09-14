from datetime import UTC, datetime
from uuid import uuid4

import pytest


def test_api_auth_session_model_contract():
    from database.models import ApiAuthSession

    table = ApiAuthSession.__table__
    columns = table.columns

    assert table.name == "api_auth_sessions"
    assert set(columns.keys()) == {
        "id",
        "tenant_id",
        "user_id",
        "refresh_token_hash",
        "auth_method",
        "device_id",
        "device_name",
        "status",
        "expires_at",
        "last_used_at",
        "revoked_at",
        "created_at",
        "updated_at",
    }

    assert "refresh_token" not in columns
    assert columns.refresh_token_hash.type.length == 64
    assert columns.refresh_token_hash.nullable is False
    assert columns.refresh_token_hash.unique is True

    assert columns.tenant_id.nullable is False
    assert columns.user_id.nullable is False
    assert columns.expires_at.nullable is False

    tenant_targets = {
        foreign_key.target_fullname
        for foreign_key
        in columns.tenant_id.foreign_keys
    }
    user_targets = {
        foreign_key.target_fullname
        for foreign_key
        in columns.user_id.foreign_keys
    }

    assert tenant_targets == {"tenants.id"}
    assert user_targets == {"users.id"}

    assert columns.expires_at.type.timezone is True
    assert columns.last_used_at.type.timezone is True
    assert columns.revoked_at.type.timezone is True
    assert columns.created_at.type.timezone is True
    assert columns.updated_at.type.timezone is True

    assert columns.status.default.arg == "active"
    assert (
        columns.auth_method.default.arg
        == "telegram"
    )


@pytest.mark.asyncio
async def test_auth_session_repository_stores_token_hash_only():
    from database.repositories.api_auth import (
        ApiAuthSessionRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    expires_at = datetime(
        2026,
        9,
        25,
        12,
        0,
        tzinfo=UTC,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushes = 0

        def add(self, instance):
            self.added.append(instance)

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = ApiAuthSessionRepository(
        session
    )

    result = await repository.create_session(
        tenant_id=tenant_id,
        user_id=user_id,
        refresh_token_hash="a" * 64,
        auth_method="telegram",
        device_id="device-123",
        device_name="Test phone",
        expires_at=expires_at,
    )

    assert session.added == [result]
    assert session.flushes == 1

    assert result.tenant_id == tenant_id
    assert result.user_id == user_id
    assert result.refresh_token_hash == (
        "a" * 64
    )
    assert result.auth_method == "telegram"
    assert result.device_id == "device-123"
    assert result.device_name == "Test phone"
    assert result.expires_at == expires_at
    assert result.status == "active"

    assert not hasattr(
        result,
        "refresh_token",
    )


@pytest.mark.asyncio
async def test_refresh_session_is_locked_before_rotation():
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from database.repositories.api_auth import (
        ApiAuthSessionRepository,
    )

    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    expected = SimpleNamespace(
        id=uuid4(),
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = ApiAuthSessionRepository(
        session
    )

    result = await (
        repository
        .get_active_session_for_update(
            refresh_token_hash="b" * 64,
            now=now,
        )
    )

    assert result is expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
        )
    )

    assert (
        "api_auth_sessions.refresh_token_hash"
        in sql
    )
    assert "api_auth_sessions.status" in sql
    assert "api_auth_sessions.expires_at" in sql
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_auth_service_rotates_refresh_token_atomically():
    from datetime import timedelta
    from types import SimpleNamespace

    from services.api_auth import ApiAuthService

    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    tenant_id = uuid4()
    user_id = uuid4()
    auth_session = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
    )
    calls = []

    class FakeDatabaseSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeUserService:
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
            return "rotated-access-token"

    class FakeRefreshTokenCodec:
        def hash_refresh_token(self, token):
            calls.append(
                (
                    "hash",
                    token,
                )
            )
            return "b" * 64

        def issue_refresh_token(self):
            calls.append(
                (
                    "issue_refresh",
                )
            )
            return SimpleNamespace(
                token="new-refresh-token",
                token_hash="c" * 64,
            )

    class FakeSessionRepository:
        async def get_active_session_for_update(
            self,
            *,
            refresh_token_hash,
            now,
        ):
            calls.append(
                (
                    "lock_session",
                    refresh_token_hash,
                    now,
                )
            )
            return auth_session

        async def rotate_session(
            self,
            *,
            auth_session,
            refresh_token_hash,
            expires_at,
            now,
        ):
            calls.append(
                (
                    "rotate_session",
                    auth_session,
                    refresh_token_hash,
                    expires_at,
                    now,
                )
            )

    database_session = FakeDatabaseSession()
    service = ApiAuthService(
        session=database_session,
        telegram_verifier=object(),
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

    result = await service.refresh_tokens(
        refresh_token="old-refresh-token",
        now=now,
    )

    lock_index = next(
        index
        for index, call in enumerate(calls)
        if call[0] == "lock_session"
    )
    rotate_index = next(
        index
        for index, call in enumerate(calls)
        if call[0] == "rotate_session"
    )
    assert lock_index < rotate_index

    rotation = calls[rotate_index]
    assert rotation == (
        "rotate_session",
        auth_session,
        "c" * 64,
        now + timedelta(days=30),
        now,
    )

    assert result.access_token == (
        "rotated-access-token"
    )
    assert result.refresh_token == (
        "new-refresh-token"
    )
    assert result.token_type == "bearer"
    assert result.expires_in == 900

    assert database_session.commits == 1
    assert database_session.rollbacks == 0


@pytest.mark.asyncio
async def test_auth_session_repository_revokes_locked_session():
    from types import SimpleNamespace

    from database.repositories.api_auth import (
        ApiAuthSessionRepository,
    )

    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    auth_session = SimpleNamespace(
        status="active",
        revoked_at=None,
        updated_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = ApiAuthSessionRepository(
        session
    )

    result = await repository.revoke_session(
        auth_session=auth_session,
        now=now,
    )

    assert result is auth_session
    assert auth_session.status == "revoked"
    assert auth_session.revoked_at == now
    assert auth_session.updated_at == now
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_auth_service_logout_revokes_refresh_session():
    from types import SimpleNamespace

    from services.api_auth import ApiAuthService

    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    auth_session = SimpleNamespace(
        id=uuid4(),
    )
    calls = []

    class FakeDatabaseSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeRefreshTokenCodec:
        def hash_refresh_token(self, token):
            calls.append(
                (
                    "hash",
                    token,
                )
            )
            return "d" * 64

    class FakeSessionRepository:
        async def get_active_session_for_update(
            self,
            *,
            refresh_token_hash,
            now,
        ):
            calls.append(
                (
                    "lock_session",
                    refresh_token_hash,
                    now,
                )
            )
            return auth_session

        async def revoke_session(
            self,
            *,
            auth_session,
            now,
        ):
            calls.append(
                (
                    "revoke_session",
                    auth_session,
                    now,
                )
            )

    database_session = FakeDatabaseSession()
    service = ApiAuthService(
        session=database_session,
        telegram_verifier=object(),
        user_service=object(),
        access_token_codec=object(),
        refresh_token_codec=(
            FakeRefreshTokenCodec()
        ),
        session_repository=(
            FakeSessionRepository()
        ),
        refresh_token_ttl_seconds=2592000,
    )

    await service.logout(
        refresh_token="active-refresh-token",
        now=now,
    )

    assert calls == [
        (
            "hash",
            "active-refresh-token",
        ),
        (
            "lock_session",
            "d" * 64,
            now,
        ),
        (
            "revoke_session",
            auth_session,
            now,
        ),
    ]
    assert database_session.commits == 1
    assert database_session.rollbacks == 0


@pytest.mark.asyncio
async def test_auth_session_list_is_tenant_and_user_scoped():
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from database.repositories.api_auth import (
        ApiAuthSessionRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
    )
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = ApiAuthSessionRepository(
        session
    )

    result = await repository.list_active_sessions(
        tenant_id=tenant_id,
        user_id=user_id,
        now=now,
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
        )
    )

    assert "api_auth_sessions.tenant_id" in sql
    assert "api_auth_sessions.user_id" in sql
    assert "api_auth_sessions.status" in sql
    assert "api_auth_sessions.expires_at" in sql
    assert "ORDER BY" in sql
    assert "created_at DESC" in sql


@pytest.mark.asyncio
async def test_auth_service_lists_safe_session_views():
    from types import SimpleNamespace

    from services.api_auth import ApiAuthService

    tenant_id = uuid4()
    user_id = uuid4()
    session_id = uuid4()
    now = datetime(
        2026,
        8,
        26,
        12,
        0,
        tzinfo=UTC,
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
    calls = []

    class FakeSessionRepository:
        async def list_active_sessions(
            self,
            *,
            tenant_id,
            user_id,
            now,
        ):
            calls.append(
                (
                    tenant_id,
                    user_id,
                    now,
                )
            )
            return [
                SimpleNamespace(
                    id=session_id,
                    auth_method="telegram",
                    device_id="device-123",
                    device_name="Test phone",
                    status="active",
                    expires_at=expires_at,
                    last_used_at=None,
                    created_at=created_at,
                    refresh_token_hash=(
                        "private-hash"
                    ),
                )
            ]

    service = ApiAuthService(
        session=object(),
        telegram_verifier=object(),
        user_service=object(),
        access_token_codec=object(),
        refresh_token_codec=object(),
        session_repository=(
            FakeSessionRepository()
        ),
        refresh_token_ttl_seconds=2592000,
    )

    result = await service.list_sessions(
        tenant_id=tenant_id,
        user_id=user_id,
        now=now,
    )

    assert calls == [
        (
            tenant_id,
            user_id,
            now,
        )
    ]
    assert len(result) == 1

    item = result[0]
    assert item.id == session_id
    assert item.auth_method == "telegram"
    assert item.device_id == "device-123"
    assert item.device_name == "Test phone"
    assert item.status == "active"
    assert item.created_at == created_at
    assert item.expires_at == expires_at
    assert item.last_used_at is None

    assert not hasattr(
        item,
        "refresh_token_hash",
    )
    assert not hasattr(
        item,
        "refresh_token",
    )


@pytest.mark.asyncio
async def test_auth_session_repository_revokes_all_user_sessions():
    from sqlalchemy.dialects import postgresql

    from database.repositories.api_auth import (
        ApiAuthSessionRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    now = datetime(
        2026,
        8,
        29,
        0,
        0,
        tzinfo=UTC,
    )

    class FakeResult:
        rowcount = 3

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = ApiAuthSessionRepository(
        session
    )

    revoked_count = await (
        repository.revoke_all_sessions(
            tenant_id=tenant_id,
            user_id=user_id,
            now=now,
        )
    )

    assert revoked_count == 3
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
        )
    )

    assert "UPDATE api_auth_sessions" in sql
    assert "api_auth_sessions.tenant_id" in sql
    assert "api_auth_sessions.user_id" in sql
    assert "api_auth_sessions.status" in sql
    assert "api_auth_sessions.revoked_at" in sql
    assert "updated_at=" in sql


@pytest.mark.asyncio
async def test_auth_service_logs_out_all_actor_sessions():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from services.api_auth import ApiAuthService

    tenant_id = uuid4()
    user_id = uuid4()
    now = datetime(
        2026,
        8,
        29,
        0,
        30,
        tzinfo=UTC,
    )

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    repository = SimpleNamespace(
        revoke_all_sessions=AsyncMock(
            return_value=3
        )
    )
    events = SimpleNamespace(
        create_event=AsyncMock(),
    )

    service = ApiAuthService(
        session=session,
        telegram_verifier=None,
        user_service=object(),
        access_token_codec=object(),
        refresh_token_codec=object(),
        session_repository=repository,
        event_repository=events,
        refresh_token_ttl_seconds=2592000,
    )

    revoked_count = await (
        service.logout_all(
            tenant_id=tenant_id,
            user_id=user_id,
            now=now,
        )
    )

    assert revoked_count == 3
    repository.revoke_all_sessions.assert_awaited_once_with(
        tenant_id=tenant_id,
        user_id=user_id,
        now=now,
    )
    events.create_event.assert_awaited_once_with(
        event_type=(
            "api_auth_all_sessions_revoked"
        ),
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        payload={
            "revoked_count": 3,
        },
        platform="api",
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_all_endpoint_uses_current_actor():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import (
        get_api_auth_service,
        get_current_actor,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
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

    class FakeAuthService:
        async def logout_all(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return 3

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_auth_service
    ] = lambda: FakeAuthService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/logout-all",
            headers={
                "X-Request-ID": (
                    "logout-all-request"
                ),
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "logged_out": True,
            "revoked_sessions": 3,
        },
        "meta": {},
        "request_id": "logout-all-request",
    }
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
        }
    ]
