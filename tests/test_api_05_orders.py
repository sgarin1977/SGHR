from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_order_repository_list_uses_actor_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    class FakeResult:
        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = ContactChatRepository(
        session
    )

    result = await (
        repository.list_service_orders_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            language="uk",
            limit=20,
            offset=0,
        )
    )

    assert result == []
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    sql = " ".join(sql.lower().split())

    assert (
        f"service_orders.tenant_id = "
        f"'{tenant_id}'"
        in sql
    )
    assert (
        f"service_orders.client_user_id = "
        f"'{user_id}'"
        in sql
        or (
            f"service_orders.specialist_user_id = "
            f"'{user_id}'"
            in sql
        )
    )


@pytest.mark.asyncio
async def test_neutral_order_list_uses_actor_scope():
    from types import SimpleNamespace

    from services.user_orders import (
        UserOrdersPage,
        UserOrdersService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    items = [
        SimpleNamespace(order_id=uuid4()),
        SimpleNamespace(order_id=uuid4()),
        SimpleNamespace(order_id=uuid4()),
    ]
    calls = []

    class FakeChats:
        async def list_user_service_orders(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return items

    service = UserOrdersService(
        chats=FakeChats(),
    )

    page = await service.list_orders_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        language="uk",
        page=0,
        page_size=2,
    )

    assert isinstance(page, UserOrdersPage)
    assert page.items == items[:2]
    assert page.page == 0
    assert page.has_next is True

    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "language": "uk",
            "limit": 3,
            "offset": 0,
        }
    ]


@pytest.mark.asyncio
async def test_me_orders_endpoint_uses_current_actor():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from api.pagination import (
        encode_page_cursor,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    created_at = datetime(
        2026,
        9,
        2,
        16,
        0,
        tzinfo=UTC,
    )
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

    item = SimpleNamespace(
        order_id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        specialist_name="Test Specialist",
        client_name="Private Client",
        profession_name="Consultant",
        status="confirmed",
        description="Online consultation",
        schedule_text="Tomorrow at 10:00",
        agreed_amount=125.5,
        currency="EUR",
        created_at=created_at,
        is_client=True,
        tenant_id=uuid4(),
        specialist_user_id=uuid4(),
        client_user_id=uuid4(),
    )

    class FakeUserOrdersService:
        async def list_orders_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                items=[item],
                page=0,
                has_next=True,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_orders_service
    ] = lambda: FakeUserOrdersService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me/orders",
            params={
                "limit": 20,
            },
            headers={
                "X-Request-ID": "me-orders",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "language": "uk",
            "page": 0,
            "page_size": 20,
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(order_id),
                "dialog_id": str(thread_id),
                "contact_request_id": (
                    str(contact_request_id)
                ),
                "counterparty_name": (
                    "Test Specialist"
                ),
                "profession_name": "Consultant",
                "status": "confirmed",
                "description": (
                    "Online consultation"
                ),
                "schedule_text": (
                    "Tomorrow at 10:00"
                ),
                "agreed_amount": 125.5,
                "currency": "EUR",
                "created_at": (
                    "2026-09-02T16:00:00Z"
                ),
                "actor_role": "client",
            }
        ],
        "meta": {
            "next_cursor": (
                encode_page_cursor(1)
            ),
            "has_more": True,
        },
        "request_id": "me-orders",
    }

    private_fields = (
        "tenant_id",
        "specialist_user_id",
        "client_user_id",
        "client_name",
    )
    for field in private_fields:
        assert field not in response.text


@pytest.mark.asyncio
async def test_me_orders_require_authentication():
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
            "/api/v1/me/orders",
            headers={
                "X-Request-ID": (
                    "me-orders-auth"
                ),
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": (
                "me-orders-auth"
            ),
        },
    }


def test_me_orders_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        "/api/v1/me/orders"
    ]["get"]

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )

    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "UserOrderListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "UserOrderListItem"
        ]["properties"]
    ) == {
        "id",
        "dialog_id",
        "contact_request_id",
        "counterparty_name",
        "profession_name",
        "status",
        "description",
        "schedule_text",
        "agreed_amount",
        "currency",
        "created_at",
        "actor_role",
    }

    private_fields = {
        "tenant_id",
        "client_user_id",
        "specialist_user_id",
        "client_name",
        "specialist_name",
    }
    assert private_fields.isdisjoint(
        components[
            "UserOrderListItem"
        ]["properties"]
    )
