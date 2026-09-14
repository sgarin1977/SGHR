from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_neutral_order_list_uses_owned_cabinet_scope():
    from services.specialist_orders import (
        SpecialistOrdersService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []
    orders = (
        SimpleNamespace(
            order_id=uuid4(),
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

    class FakeOrders:
        async def list_specialist_service_orders_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(("orders", kwargs))
            return orders

    service = SpecialistOrdersService(
        object(),
        cabinets=FakeCabinets(),
        orders=FakeOrders(),
    )

    action = await service.list_orders_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        limit=20,
        offset=0,
    )

    assert action.actor is actor
    assert action.result is orders
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
            "orders",
            {
                "tenant_id": tenant_id,
                "specialist_user_id": user_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "language": "uk",
                "limit": 20,
                "offset": 0,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_specialist_order_repository_uses_full_owner_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    expected = [
        (
            SimpleNamespace(id=uuid4()),
            "Specialist",
            "Profession",
            "Client",
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
    repository = ContactChatRepository(
        session
    )

    result = await (
        repository
        .list_service_orders_for_specialist_cabinet(
            tenant_id=tenant_id,
            specialist_user_id=user_id,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                cabinet_id
            ),
            language="uk",
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
    )

    required_scope = (
        "service_orders.tenant_id",
        "service_orders.specialist_user_id",
        "service_orders.specialist_id",
        (
            "service_orders."
            "professional_cabinet_id"
        ),
    )
    for expression in required_scope:
        assert expression in sql


@pytest.mark.asyncio
async def test_contact_service_maps_specialist_cabinet_orders():
    from datetime import UTC, datetime

    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    specialist_user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    request_id = uuid4()
    created_at = datetime(
        2026,
        8,
        29,
        12,
        0,
        tzinfo=UTC,
    )
    calls = []

    order = SimpleNamespace(
        id=order_id,
        thread_id=thread_id,
        contact_request_id=request_id,
        client_user_id=uuid4(),
        specialist_user_id=(
            specialist_user_id
        ),
        status="confirmed",
        description="Audit service",
        agreed_amount=125.50,
        currency="EUR",
        created_at=created_at,
        extra_metadata={
            "schedule_text": "Monday 10:00",
        },
    )

    class FakeRepository:
        async def list_service_orders_for_specialist_cabinet(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return [
                (
                    order,
                    "Specialist Name",
                    "Auditor",
                    "Client Name",
                )
            ]

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await (
        service
        .list_specialist_service_orders_for_cabinet(
            tenant_id=tenant_id,
            specialist_user_id=(
                specialist_user_id
            ),
            specialist_id=specialist_id,
            professional_cabinet_id=(
                cabinet_id
            ),
            language="uk",
            limit=20,
            offset=0,
        )
    )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "specialist_user_id": (
                specialist_user_id
            ),
            "specialist_id": specialist_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "language": "uk",
            "limit": 20,
            "offset": 0,
        }
    ]
    assert len(result) == 1

    item = result[0]
    assert item.order_id == order_id
    assert item.thread_id == thread_id
    assert (
        item.contact_request_id
        == request_id
    )
    assert item.specialist_name == (
        "Specialist Name"
    )
    assert item.client_name == "Client Name"
    assert item.profession_name == "Auditor"
    assert item.status == "confirmed"
    assert item.description == "Audit service"
    assert item.schedule_text == (
        "Monday 10:00"
    )
    assert item.agreed_amount == 125.50
    assert item.currency == "EUR"
    assert item.created_at == created_at
    assert item.is_client is False


@pytest.mark.asyncio
async def test_specialist_cabinet_orders_use_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_orders_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.contact_chat import (
        ServiceOrderListItem,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    request_id = "specialist-orders-list"
    created_at = datetime(
        2026,
        8,
        29,
        13,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.orders.read",
        ),
    )

    item = ServiceOrderListItem(
        order_id=order_id,
        thread_id=thread_id,
        contact_request_id=(
            contact_request_id
        ),
        specialist_name="Private Own Name",
        client_name="Client Name",
        profession_name="Auditor",
        status="confirmed",
        description="Audit service",
        schedule_text="Monday 10:00",
        agreed_amount=125.5,
        currency="EUR",
        created_at=created_at,
        is_client=False,
    )

    class FakeSpecialistOrders:
        async def list_orders_for_user(
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
        get_specialist_orders_service
    ] = lambda: FakeSpecialistOrders()

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
                f"{cabinet_id}/orders"
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
                "id": str(order_id),
                "thread_id": str(thread_id),
                "contact_request_id": (
                    str(contact_request_id)
                ),
                "client_name": "Client Name",
                "profession_name": "Auditor",
                "status": "confirmed",
                "description": "Audit service",
                "schedule_text": (
                    "Monday 10:00"
                ),
                "agreed_amount": 125.5,
                "currency": "EUR",
                "created_at": (
                    "2026-08-29T13:00:00Z"
                ),
            }
        ],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": request_id,
    }
    assert "specialist_name" not in response.text
    assert "is_client" not in response.text


@pytest.mark.asyncio
async def test_specialist_cabinet_orders_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_orders_service,
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

    class ForbiddenOrdersService:
        async def list_orders_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Orders service must not run "
                "without read permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_orders_service
    ] = lambda: ForbiddenOrdersService()

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
                f"{uuid4()}/orders"
            )
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_specialist_cabinet_orders_hide_missing_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_orders_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsSelectionError,
    )

    request_id = "specialist-orders-missing"
    private_detail = (
        "foreign tenant order cabinet"
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
            "specialist.orders.read",
        ),
    )

    class MissingOrdersService:
        async def list_orders_for_user(
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
        get_specialist_orders_service
    ] = lambda: MissingOrdersService()

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
                f"{uuid4()}/orders"
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


def test_specialist_orders_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/orders"
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
            "SpecialistOrderListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistOrderItem"
        ]["properties"]
    ) == {
        "id",
        "thread_id",
        "contact_request_id",
        "client_name",
        "profession_name",
        "status",
        "description",
        "schedule_text",
        "agreed_amount",
        "currency",
        "created_at",
    }

