from datetime import UTC, datetime
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_partner_repository_lists_filtered_tenant_api_logs():
    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    date_from = datetime(
        2026, 9, 1, tzinfo=UTC
    )
    date_to = datetime(
        2026, 9, 8, tzinfo=UTC
    )
    expected = [object(), object()]

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = PartnerApiRepository(session)

    result = await repository.list_api_logs(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        api_key_id=api_key_id,
        status_code=201,
        date_from=date_from,
        date_to=date_to,
        endpoint="/api/v1/orders",
        limit=21,
        offset=40,
    )

    assert result == expected
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    normalized = " ".join(compiled.split())

    assert "FROM api_logs" in normalized
    assert "api_logs.tenant_id =" in normalized
    assert str(tenant_id) in normalized
    assert "api_logs.api_client_id =" in normalized
    assert str(api_client_id) in normalized
    assert "api_logs.api_key_id =" in normalized
    assert str(api_key_id) in normalized
    assert "api_logs.status_code = 201" in normalized
    assert "api_logs.endpoint =" in normalized
    assert "/api/v1/orders" in normalized
    assert "api_logs.created_at >=" in normalized
    assert "api_logs.created_at <" in normalized
    assert "ORDER BY api_logs.created_at DESC" in normalized
    assert "LIMIT 21" in normalized
    assert "OFFSET 40" in normalized


@pytest.mark.asyncio
async def test_partner_service_defaults_production_key_expiration_to_90_days(
    monkeypatch,
):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import services.partner_api as partner_module
    from services.partner_api import PartnerApiService

    now = datetime(
        2026, 9, 7, 14, 0, tzinfo=UTC
    )
    expected_expiration = now + timedelta(days=90)
    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    calls = []

    class FixedDateTime:
        @classmethod
        def now(cls, timezone):
            assert timezone is UTC
            return now

    class FakeCodec:
        def issue_api_key(self):
            return SimpleNamespace(
                key="sghr_live_plaintext_once",
                key_prefix="sghr_live",
                key_hash="hashed-secret",
            )

    class FakeRepository:
        async def get_api_client(self, **kwargs):
            return SimpleNamespace(
                id=api_client_id,
            )

        async def create_api_key(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id=api_key_id,
                api_client_id=api_client_id,
                name=kwargs["name"],
                key_prefix=kwargs["key_prefix"],
                environment=kwargs["environment"],
                status="active",
                expires_at=kwargs["expires_at"],
                last_used_at=None,
                ip_allowlist=[],
                created_at=now,
            )

        async def create_api_key_scopes(
            self,
            **kwargs,
        ):
            return ()

    monkeypatch.setattr(
        partner_module,
        "datetime",
        FixedDateTime,
    )

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = PartnerApiService(
        session=session,
        repository=FakeRepository(),
        api_key_codec=FakeCodec(),
        environment="production",
    )

    result = await service.create_api_key(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        name="Production integration",
        expires_at=None,
        ip_allowlist=None,
        scopes=("services.read",),
    )

    assert len(calls) == 1
    assert calls[0]["expires_at"] == (
        expected_expiration
    )
    assert result.expires_at == expected_expiration
    assert result.expires_at.tzinfo is UTC
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_partner_service_rejects_api_key_expiration_over_365_days(
    monkeypatch,
):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import services.partner_api as partner_module
    from services.partner_api import (
        PartnerApiOperationError,
        PartnerApiService,
    )

    now = datetime(
        2026, 9, 7, 14, 0, tzinfo=UTC
    )

    class FixedDateTime:
        @classmethod
        def now(cls, timezone):
            assert timezone is UTC
            return now

    monkeypatch.setattr(
        partner_module,
        "datetime",
        FixedDateTime,
    )

    repository = SimpleNamespace(
        get_api_client=AsyncMock(),
        create_api_key=AsyncMock(),
        create_api_key_scopes=AsyncMock(),
    )
    codec = SimpleNamespace(
        issue_api_key=Mock(),
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = PartnerApiService(
        session=session,
        repository=repository,
        api_key_codec=codec,
        environment="production",
    )

    with pytest.raises(
        PartnerApiOperationError
    ):
        await service.create_api_key(
            tenant_id=uuid4(),
            api_client_id=uuid4(),
            name="Invalid long-lived key",
            expires_at=(
                now + timedelta(days=366)
            ),
            ip_allowlist=None,
            scopes=("services.read",),
        )

    codec.issue_api_key.assert_not_called()
    repository.create_api_key.assert_not_awaited()
    repository.create_api_key_scopes.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_partner_repository_locks_tenant_api_client_before_update():
    from sqlalchemy.dialects import postgresql

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
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

    session = FakeSession()
    repository = PartnerApiRepository(session)

    result = await (
        repository.get_api_client_for_update(
            tenant_id=tenant_id,
            api_client_id=api_client_id,
        )
    )

    assert result is expected
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert "FROM api_clients" in normalized
    assert "api_clients.tenant_id =" in normalized
    assert str(tenant_id) in normalized
    assert "api_clients.id =" in normalized
    assert str(api_client_id) in normalized
    assert normalized.endswith("FOR UPDATE")


@pytest.mark.asyncio
async def test_partner_repository_updates_only_allowed_api_client_fields():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from database.repositories.partner_api import (
        PartnerApiRepository,
    )

    tenant_id = uuid4()
    owner_id = uuid4()
    api_client = SimpleNamespace(
        tenant_id=tenant_id,
        owner_type="partner",
        owner_id=owner_id,
        name="Old name",
        status="active",
        extra_metadata={"old": True},
    )
    session = SimpleNamespace(
        flush=AsyncMock(),
    )
    repository = PartnerApiRepository(session)

    result = await repository.update_api_client(
        api_client=api_client,
        name="  Updated integration  ",
        status="suspended",
        metadata={
            "description": "Temporarily paused",
        },
    )

    assert result is api_client
    assert api_client.name == "Updated integration"
    assert api_client.status == "suspended"
    assert api_client.extra_metadata == {
        "description": "Temporarily paused",
    }

    assert api_client.tenant_id == tenant_id
    assert api_client.owner_type == "partner"
    assert api_client.owner_id == owner_id
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_partner_service_updates_scoped_api_client_and_preserves_omitted_fields():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from services.partner_api import PartnerApiService

    tenant_id = uuid4()
    api_client_id = uuid4()
    owner_id = uuid4()
    now = datetime(
        2026, 9, 7, 15, 0, tzinfo=UTC
    )
    calls = []

    api_client = SimpleNamespace(
        id=api_client_id,
        tenant_id=tenant_id,
        owner_type="partner",
        owner_id=owner_id,
        name="Existing integration",
        status="active",
        extra_metadata={
            "description": "Existing metadata",
        },
        created_at=now,
        updated_at=now,
    )

    class FakeRepository:
        async def get_api_client_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return api_client

        async def update_api_client(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            kwargs["api_client"].name = kwargs["name"]
            kwargs["api_client"].status = kwargs["status"]
            kwargs["api_client"].extra_metadata = (
                kwargs["metadata"]
            )
            return kwargs["api_client"]

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = PartnerApiService(
        session=session,
        repository=FakeRepository(),
    )

    result = await service.update_api_client(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        name=None,
        status="suspended",
        metadata=None,
    )

    assert calls == [
        (
            "lock",
            {
                "tenant_id": tenant_id,
                "api_client_id": api_client_id,
            },
        ),
        (
            "update",
            {
                "api_client": api_client,
                "name": "Existing integration",
                "status": "suspended",
                "metadata": {
                    "description": (
                        "Existing metadata"
                    ),
                },
            },
        ),
    ]

    assert result.id == api_client_id
    assert result.name == "Existing integration"
    assert result.status == "suspended"
    assert result.metadata == {
        "description": "Existing metadata",
    }
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_partner_api_client_update_endpoint_uses_jwt_actor_tenant():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_identity import ApiActorContext
    from services.partner_api import ApiClientView

    tenant_id = uuid4()
    actor_user_id = uuid4()
    api_client_id = uuid4()
    owner_id = uuid4()
    now = datetime(
        2026, 9, 7, 16, 0, tzinfo=UTC
    )
    calls = []

    actor = ApiActorContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=("api.clients.update",),
    )

    class FakeService:
        async def update_api_client(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiClientView(
                id=api_client_id,
                owner_type="partner",
                owner_id=owner_id,
                name="Existing integration",
                status="suspended",
                metadata={"reason": "maintenance"},
                created_at=now,
                updated_at=now,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            (
                "/api/v1/partner/api-clients/"
                f"{api_client_id}"
            ),
            headers={
                "X-Request-ID": (
                    "partner-api-client-update"
                ),
            },
            json={
                "status": "suspended",
                "metadata": {
                    "reason": "maintenance",
                },
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_client_id": api_client_id,
            "name": None,
            "status": "suspended",
            "metadata": {
                "reason": "maintenance",
            },
        }
    ]

    payload = response.json()
    assert payload["data"]["id"] == str(
        api_client_id
    )
    assert payload["data"]["status"] == (
        "suspended"
    )
    assert payload["data"]["metadata"] == {
        "reason": "maintenance",
    }
    assert payload["request_id"] == (
        "partner-api-client-update"
    )


@pytest.mark.asyncio
async def test_partner_api_client_update_rejects_tenant_and_ownership_fields():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_partner_api_service,
    )
    from services.api_identity import ApiActorContext

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=("api.clients.update",),
    )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_partner_api_service
    ] = lambda: object()

    foreign_tenant_id = str(uuid4())
    foreign_owner_id = str(uuid4())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.patch(
            (
                "/api/v1/partner/api-clients/"
                f"{uuid4()}"
            ),
            headers={
                "X-Request-ID": (
                    "partner-client-invalid-fields"
                ),
            },
            json={
                "status": "disabled",
                "tenant_id": foreign_tenant_id,
                "owner_type": "enterprise",
                "owner_id": foreign_owner_id,
            },
        )

    assert response.status_code == 422

    payload = response.json()
    assert payload == {
        "error": {
            "code": "validation_error",
            "message": (
                "Request validation failed."
            ),
            "request_id": (
                "partner-client-invalid-fields"
            ),
        }
    }

    serialized = str(payload)
    assert foreign_tenant_id not in serialized
    assert foreign_owner_id not in serialized
