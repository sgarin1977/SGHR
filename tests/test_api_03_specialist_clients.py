from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_neutral_client_list_uses_owned_cabinet_scope():
    from services.specialist_clients import (
        SpecialistClientsService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []
    clients = (
        SimpleNamespace(
            client_id=uuid4(),
        ),
    )

    actor = SimpleNamespace(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(("cabinet", kwargs))
            return SimpleNamespace(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeClients:
        async def list_specialist_clients_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(("clients", kwargs))
            return clients

    service = SpecialistClientsService(
        object(),
        cabinets=FakeCabinets(),
        clients=FakeClients(),
    )

    action = await service.list_clients_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        limit=20,
        offset=0,
    )

    assert action.actor is actor
    assert action.result is clients
    assert calls == [
        (
            "cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "clients",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "limit": 20,
                "offset": 0,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_specialist_client_repository_uses_contact_request_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()

    expected = [
        SimpleNamespace(
            client_id=uuid4(),
            display_name="Test Client",
            requests_count=2,
        ),
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

    session = FakeSession()
    repository = ContactChatRepository(session)

    result = await (
        repository
        .list_specialist_clients_for_cabinet(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            professional_cabinet_id=cabinet_id,
            limit=20,
            offset=0,
        )
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
    ).lower()

    normalized_sql = " ".join(sql.split())

    required_scope = (
        "contact_requests.tenant_id",
        "contact_requests.specialist_id",
        (
            "contact_requests."
            "professional_cabinet_id"
        ),
        "contact_requests.from_user_id",
        "row_number() over",
        (
            "partition by "
            "contact_requests.from_user_id"
        ),
    )

    for value in required_scope:
        assert value in normalized_sql

    assert "service_orders" not in normalized_sql
    assert "user_accounts.email" not in normalized_sql
    assert "user_accounts.phone" not in normalized_sql


@pytest.mark.asyncio
async def test_contact_service_maps_safe_specialist_client_views():
    from datetime import UTC, datetime

    from services.contact_chat import (
        ContactChatService,
        SpecialistClientListItem,
    )

    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    client_id = uuid4()
    thread_id = uuid4()

    first_request_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    last_request_at = datetime(
        2026,
        8,
        28,
        15,
        0,
        tzinfo=UTC,
    )
    calls = []

    row = SimpleNamespace(
        client_id=client_id,
        display_name="Test Client",
        requests_count=3,
        first_request_at=first_request_at,
        last_request_at=last_request_at,
        last_request_status="completed",
        latest_thread_id=thread_id,
        email="private@example.com",
        phone="+380000000000",
        platform_user_id="private-telegram-id",
    )

    class FakeRepository:
        session = object()

        async def list_specialist_clients_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return [row]

    service = ContactChatService(
        FakeRepository()
    )

    result = await (
        service
        .list_specialist_clients_for_cabinet(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            professional_cabinet_id=cabinet_id,
            limit=20,
            offset=0,
        )
    )

    assert result == [
        SpecialistClientListItem(
            client_id=client_id,
            display_name="Test Client",
            requests_count=3,
            first_request_at=first_request_at,
            last_request_at=last_request_at,
            last_request_status="completed",
            latest_thread_id=thread_id,
        )
    ]

    assert calls == [
        {
            "tenant_id": tenant_id,
            "specialist_id": specialist_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "limit": 20,
            "offset": 0,
        }
    ]

    assert set(vars(result[0])) == {
        "client_id",
        "display_name",
        "requests_count",
        "first_request_at",
        "last_request_at",
        "last_request_status",
        "latest_thread_id",
    }


@pytest.mark.asyncio
async def test_specialist_cabinet_clients_use_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_clients_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.contact_chat import (
        SpecialistClientListItem,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    client_id = uuid4()
    thread_id = uuid4()
    request_id = "specialist-clients-request"
    calls = []

    first_request_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    last_request_at = datetime(
        2026,
        8,
        28,
        15,
        0,
        tzinfo=UTC,
    )

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.clients.read",
        ),
    )

    item = SpecialistClientListItem(
        client_id=client_id,
        display_name="Test Client",
        requests_count=3,
        first_request_at=first_request_at,
        last_request_at=last_request_at,
        last_request_status="completed",
        latest_thread_id=thread_id,
    )

    class FakeSpecialistClients:
        async def list_clients_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=[item],
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_clients_service
    ] = lambda: FakeSpecialistClients()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/clients"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "limit": 21,
            "offset": 0,
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(client_id),
                "display_name": "Test Client",
                "requests_count": 3,
                "first_request_at": (
                    "2026-08-20T10:00:00Z"
                ),
                "last_request_at": (
                    "2026-08-28T15:00:00Z"
                ),
                "last_request_status": (
                    "completed"
                ),
                "latest_thread_id": (
                    str(thread_id)
                ),
            }
        ],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": request_id,
    }

    assert "email" not in response.text
    assert "phone" not in response.text
    assert "platform_user_id" not in response.text


@pytest.mark.asyncio
async def test_specialist_cabinet_clients_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_clients_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    calls = []

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(),
    )

    class ForbiddenClientsService:
        async def list_clients_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Clients service must not run "
                "without read permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_clients_service
    ] = lambda: ForbiddenClientsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/clients"
            )
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_specialist_cabinet_clients_require_authentication():
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
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/clients"
            )
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )


@pytest.mark.asyncio
async def test_specialist_cabinet_clients_hide_missing_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_clients_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsSelectionError,
    )

    request_id = "specialist-clients-missing"
    private_detail = (
        "foreign tenant cabinet identifier"
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.clients.read",
        ),
    )

    class MissingClientsService:
        async def list_clients_for_user(
            self,
            **kwargs,
        ):
            raise SpecialistCabinetsSelectionError(
                private_detail
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_clients_service
    ] = lambda: MissingClientsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{uuid4()}/clients"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "cabinet_not_found",
            "message": (
                "Professional cabinet "
                "was not found."
            ),
            "request_id": request_id,
        },
    }
    assert private_detail not in response.text


def test_specialist_clients_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/clients"
        )
    ]["get"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    parameters = {
        parameter["name"]: parameter
        for parameter in operation[
            "parameters"
        ]
    }
    assert {
        "cabinet_id",
        "limit",
        "cursor",
    } <= set(parameters)

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistClientListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistClientItem"
        ]["properties"]
    ) == {
        "id",
        "display_name",
        "requests_count",
        "first_request_at",
        "last_request_at",
        "last_request_status",
        "latest_thread_id",
    }

    private_fields = {
        "email",
        "phone",
        "platform_user_id",
        "tenant_id",
        "specialist_id",
        "professional_cabinet_id",
    }

    assert not (
        private_fields
        & set(
            components[
                "SpecialistClientItem"
            ]["properties"]
        )
    )

