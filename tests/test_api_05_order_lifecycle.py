from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_contact_service_forwards_proposed_order_interval():
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    thread_id = uuid4()
    order_id = uuid4()
    contact_request_id = uuid4()
    start_at = datetime(
        2026, 9, 10, 9, 0, tzinfo=UTC
    )
    end_at = datetime(
        2026, 9, 10, 10, 0, tzinfo=UTC
    )
    calls = []

    class FakeRepository:
        async def create_service_order_draft_from_thread(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=order_id,
                thread_id=thread_id,
                contact_request_id=contact_request_id,
                status="draft",
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await (
        service.create_service_order_draft_from_thread(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            thread_id=thread_id,
            description="Scheduled consultation",
            schedule_text=None,
            start_at=start_at,
            end_at=end_at,
            agreed_amount=100.0,
            currency="EUR",
        )
    )

    assert calls == [
        {
            "thread_id": thread_id,
            "actor_user_id": actor_user_id,
            "tenant_id": tenant_id,
            "description": "Scheduled consultation",
            "schedule_text": None,
            "start_at": start_at,
            "end_at": end_at,
            "agreed_amount": 100.0,
            "currency": "EUR",
            "platform": "telegram",
        }
    ]
    assert result.order_id == order_id
    assert result.status == "draft"


@pytest.mark.asyncio
async def test_order_repository_persists_interval_and_thread_cabinet():
    from unittest.mock import AsyncMock

    from database.models import (
        ContactRequest,
        ServiceOrder,
        Specialist,
    )
    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    specialist_user_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    profession_id = uuid4()
    start_at = datetime(
        2026, 9, 10, 9, 0, tzinfo=UTC
    )
    end_at = datetime(
        2026, 9, 10, 10, 0, tzinfo=UTC
    )
    added = []

    thread = SimpleNamespace(
        id=thread_id,
        tenant_id=tenant_id,
        context_type="contact_request",
        context_id=contact_request_id,
        status="in_discussion",
        client_user_id=actor_user_id,
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
    )
    contact_request = SimpleNamespace(
        id=contact_request_id,
        profession_id=profession_id,
        message="Original request",
    )
    specialist = SimpleNamespace(
        id=specialist_id,
        user_id=specialist_user_id,
        active_professional_cabinet_id=(
            cabinet_id
        ),
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def get(self, model, record_id):
            if model is ContactRequest:
                assert record_id == contact_request_id
                return contact_request
            if model is Specialist:
                assert record_id == specialist_id
                return specialist
            raise AssertionError(
                f"Unexpected model lookup: {model}"
            )

        async def execute(self, statement):
            return FakeResult()

        def add(self, item):
            added.append(item)

        async def flush(self):
            return None

        async def commit(self):
            return None

    repository = ContactChatRepository(
        FakeSession()
    )
    repository.get_thread_for_user = AsyncMock(
        return_value=thread
    )

    result = await (
        repository.create_service_order_draft_from_thread(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            thread_id=thread_id,
            description="Scheduled consultation",
            schedule_text=None,
            start_at=start_at,
            end_at=end_at,
            agreed_amount=100.0,
            currency="EUR",
            platform="api",
        )
    )

    assert isinstance(result, ServiceOrder)
    assert result.tenant_id == tenant_id
    assert result.thread_id == thread_id
    assert result.professional_cabinet_id == (
        cabinet_id
    )
    assert result.start_at == start_at
    assert result.end_at == end_at
    assert result.status == "draft"

    assert any(
        isinstance(item, ServiceOrder)
        for item in added
    )


@pytest.mark.asyncio
async def test_contact_service_forwards_neutral_order_platform():
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    thread_id = uuid4()
    calls = []

    class FakeRepository:
        async def create_service_order_draft_from_thread(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=uuid4(),
                thread_id=thread_id,
                contact_request_id=uuid4(),
                status="draft",
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    await service.create_service_order_draft_from_thread(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        thread_id=thread_id,
        description="API order",
        platform="api",
    )

    assert calls == [
        {
            "thread_id": thread_id,
            "actor_user_id": actor_user_id,
            "tenant_id": tenant_id,
            "description": "API order",
            "schedule_text": None,
            "start_at": None,
            "end_at": None,
            "agreed_amount": None,
            "currency": "EUR",
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_user_order_service_reuses_shared_draft_creation_flow():
    from services.user_orders import (
        UserOrdersService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    order_id = uuid4()
    contact_request_id = uuid4()
    start_at = datetime(
        2026, 9, 11, 11, 0, tzinfo=UTC
    )
    end_at = datetime(
        2026, 9, 11, 12, 0, tzinfo=UTC
    )
    calls = []

    expected = SimpleNamespace(
        order_id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        status="draft",
    )

    class FakeChats:
        async def create_service_order_draft_from_thread(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = UserOrdersService(
        chats=FakeChats(),
    )

    result = await service.create_order_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        description="Scheduled consultation",
        start_at=start_at,
        end_at=end_at,
        agreed_amount=125.5,
        currency="EUR",
        idempotency_key="order-service-create-001",
    )

    assert result is expected
    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": user_id,
            "thread_id": thread_id,
            "description": "Scheduled consultation",
            "schedule_text": None,
            "start_at": start_at,
            "end_at": end_at,
            "agreed_amount": 125.5,
            "currency": "EUR",
            "platform": "api",
        }
    ]



@pytest.mark.asyncio
async def test_order_create_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    order_id = uuid4()
    contact_request_id = uuid4()

    start_at = datetime(
        2026, 9, 11, 11, 0, tzinfo=UTC
    )
    end_at = datetime(
        2026, 9, 11, 12, 0, tzinfo=UTC
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

    class FakeUserOrdersService:
        async def create_order_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                order_id=order_id,
                thread_id=thread_id,
                contact_request_id=contact_request_id,
                status="draft",
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
        response = await client.post(
            "/api/v1/orders",
            headers={
                "X-Request-ID": "order-create",
                "Idempotency-Key": (
                    "order-create-20260911-001"
                ),
            },
            json={
                "dialog_id": str(thread_id),
                "description": (
                    "Scheduled consultation"
                ),
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "agreed_amount": 125.5,
                "currency": "EUR",
            },
        )

    assert response.status_code == 201
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "description": (
                "Scheduled consultation"
            ),
            "start_at": start_at,
            "end_at": end_at,
            "agreed_amount": 125.5,
            "currency": "EUR",
            "schedule_text": None,
            "idempotency_key": (
                "order-create-20260911-001"
            ),
        }
    ]

    body = response.json()
    assert body["data"] == {
        "id": str(order_id),
        "dialog_id": str(thread_id),
        "contact_request_id": str(
            contact_request_id
        ),
        "status": "draft",
    }
    assert body["meta"] == {}
    assert body["request_id"] == "order-create"



@pytest.mark.asyncio
async def test_order_repository_locks_actor_scoped_order_for_update():
    from sqlalchemy.dialects import postgresql

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    expected = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        status="draft",
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
    repository = ContactChatRepository(session)

    result = await (
        repository
        .get_service_order_for_update(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            order_id=order_id,
        )
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

    assert "FOR UPDATE" in sql
    assert (
        "service_orders.id = "
        f"'{order_id}'"
    ) in sql
    assert (
        "service_orders.tenant_id = "
        f"'{tenant_id}'"
    ) in sql
    assert (
        "service_orders.client_user_id = "
        f"'{actor_user_id}'"
    ) in sql
    assert (
        "service_orders.specialist_user_id = "
        f"'{actor_user_id}'"
    ) in sql



@pytest.mark.asyncio
async def test_order_repository_updates_locked_draft_terms():
    from unittest.mock import AsyncMock

    from database.models import EventLog
    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()

    start_at = datetime(
        2026, 9, 12, 10, 0, tzinfo=UTC
    )
    end_at = datetime(
        2026, 9, 12, 11, 30, tzinfo=UTC
    )

    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        client_user_id=actor_user_id,
        specialist_user_id=uuid4(),
        status="draft",
        description="Old terms",
        start_at=None,
        end_at=None,
        agreed_amount=None,
        currency="EUR",
        updated_at=None,
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return order

    class FakeSession:
        def __init__(self):
            self.added = []
            self.commit = AsyncMock()

        async def execute(self, statement):
            return FakeResult()

        def add(self, value):
            self.added.append(value)

    session = FakeSession()
    repository = ContactChatRepository(session)

    result = await repository.update_service_order_draft(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        description="Updated consultation",
        start_at=start_at,
        end_at=end_at,
        agreed_amount=150.0,
        currency="EUR",
        platform="api",
    )

    assert result is order
    assert order.status == "draft"
    assert order.description == "Updated consultation"
    assert order.start_at == start_at
    assert order.end_at == end_at
    assert order.agreed_amount == 150.0
    assert order.currency == "EUR"
    assert order.updated_at.tzinfo is UTC

    events = [
        value
        for value in session.added
        if isinstance(value, EventLog)
    ]
    assert len(events) == 1
    assert events[0].tenant_id == tenant_id
    assert events[0].user_id == actor_user_id
    assert events[0].entity_id == order_id
    assert events[0].event_type == (
        "service_order_updated"
    )
    assert events[0].platform == "api"

    session.commit.assert_awaited_once()



@pytest.mark.asyncio
async def test_contact_service_updates_order_through_shared_flow():
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()

    start_at = datetime(
        2026, 9, 13, 13, 0, tzinfo=UTC
    )
    end_at = datetime(
        2026, 9, 13, 14, 0, tzinfo=UTC
    )
    calls = []

    class FakeRepository:
        async def update_service_order_draft(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=order_id,
                thread_id=thread_id,
                contact_request_id=contact_request_id,
                status="draft",
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await service.update_service_order_draft(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        description="Updated consultation",
        start_at=start_at,
        end_at=end_at,
        agreed_amount=175.0,
        currency="EUR",
        platform="api",
    )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "order_id": order_id,
            "description": (
                "Updated consultation"
            ),
            "start_at": start_at,
            "end_at": end_at,
            "agreed_amount": 175.0,
            "currency": "EUR",
            "platform": "api",
        }
    ]
    assert result.order_id == order_id
    assert result.thread_id == thread_id
    assert (
        result.contact_request_id
        == contact_request_id
    )
    assert result.status == "draft"



@pytest.mark.asyncio
async def test_user_order_service_reuses_shared_update_flow():
    from services.user_orders import (
        UserOrdersService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()

    start_at = datetime(
        2026, 9, 14, 9, 0, tzinfo=UTC
    )
    end_at = datetime(
        2026, 9, 14, 10, 30, tzinfo=UTC
    )
    calls = []

    expected = SimpleNamespace(
        order_id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        status="draft",
    )

    class FakeChats:
        async def update_service_order_draft(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = UserOrdersService(
        chats=FakeChats(),
    )

    result = await service.update_order_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        order_id=order_id,
        description="Updated consultation",
        start_at=start_at,
        end_at=end_at,
        agreed_amount=200.0,
        currency="EUR",
    )

    assert result is expected
    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": user_id,
            "order_id": order_id,
            "description": (
                "Updated consultation"
            ),
            "start_at": start_at,
            "end_at": end_at,
            "agreed_amount": 200.0,
            "currency": "EUR",
            "platform": "api",
        }
    ]



@pytest.mark.asyncio
async def test_order_update_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()

    start_at = datetime(
        2026, 9, 15, 14, 0, tzinfo=UTC
    )
    end_at = datetime(
        2026, 9, 15, 15, 0, tzinfo=UTC
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

    class FakeUserOrdersService:
        async def update_order_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                order_id=order_id,
                thread_id=thread_id,
                contact_request_id=(
                    contact_request_id
                ),
                status="draft",
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
        response = await client.patch(
            f"/api/v1/orders/{order_id}",
            headers={
                "X-Request-ID": "order-update",
            },
            json={
                "description": (
                    "Updated consultation"
                ),
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "agreed_amount": 225.0,
                "currency": "EUR",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "order_id": order_id,
            "description": (
                "Updated consultation"
            ),
            "start_at": start_at,
            "end_at": end_at,
            "agreed_amount": 225.0,
            "currency": "EUR",
        }
    ]

    body = response.json()
    assert body["data"] == {
        "id": str(order_id),
        "dialog_id": str(thread_id),
        "contact_request_id": str(
            contact_request_id
        ),
        "status": "draft",
    }
    assert body["meta"] == {}
    assert body["request_id"] == "order-update"



@pytest.mark.asyncio
async def test_order_repository_confirms_locked_draft():
    from unittest.mock import AsyncMock

    from database.models import EventLog
    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    client_user_id = uuid4()
    specialist_user_id = uuid4()
    creator_user_id = specialist_user_id
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()

    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        client_user_id=client_user_id,
        specialist_user_id=specialist_user_id,
        specialist_id=uuid4(),
        professional_cabinet_id=uuid4(),
        created_by=creator_user_id,
        status="draft",
        confirmed_by=None,
        confirmed_at=None,
        updated_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.commit = AsyncMock()

        async def get(self, model, entity_id):
            return order

        def add(self, value):
            self.added.append(value)

    session = FakeSession()
    repository = ContactChatRepository(session)
    repository.get_service_order_for_update = (
        AsyncMock(return_value=order)
    )

    result = await repository.confirm_service_order(
        tenant_id=tenant_id,
        actor_user_id=client_user_id,
        order_id=order_id,
        platform="api",
    )

    assert result is order
    repository.get_service_order_for_update.assert_awaited_once_with(
        tenant_id=tenant_id,
        actor_user_id=client_user_id,
        order_id=order_id,
    )

    assert order.status == "confirmed"
    assert order.confirmed_by == client_user_id
    assert order.confirmed_at.tzinfo is UTC
    assert order.updated_at == order.confirmed_at

    events = [
        value
        for value in session.added
        if isinstance(value, EventLog)
    ]
    assert len(events) == 1
    assert events[0].event_type == (
        "service_order_confirmed"
    )
    assert events[0].tenant_id == tenant_id
    assert events[0].user_id == client_user_id
    assert events[0].entity_id == order_id
    assert events[0].platform == "api"

    session.commit.assert_awaited_once()



@pytest.mark.asyncio
async def test_contact_service_forwards_neutral_confirmation_platform():
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    class FakeRepository:
        async def confirm_service_order(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=order_id,
                thread_id=thread_id,
                contact_request_id=(
                    contact_request_id
                ),
                status="confirmed",
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await service.confirm_service_order(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        platform="api",
    )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "order_id": order_id,
            "platform": "api",
        }
    ]
    assert result.order_id == order_id
    assert result.thread_id == thread_id
    assert (
        result.contact_request_id
        == contact_request_id
    )
    assert result.status == "confirmed"



@pytest.mark.asyncio
async def test_user_order_service_reuses_shared_confirmation_flow():
    from services.user_orders import (
        UserOrdersService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    expected = SimpleNamespace(
        order_id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        status="confirmed",
    )

    class FakeChats:
        async def confirm_service_order(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = UserOrdersService(
        chats=FakeChats(),
    )

    result = await service.confirm_order_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        order_id=order_id,
    )

    assert result is expected
    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": user_id,
            "order_id": order_id,
            "platform": "api",
        }
    ]



@pytest.mark.asyncio
async def test_order_confirm_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
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

    class FakeUserOrdersService:
        async def confirm_order_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                order_id=order_id,
                thread_id=thread_id,
                contact_request_id=(
                    contact_request_id
                ),
                status="confirmed",
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
        response = await client.post(
            f"/api/v1/orders/{order_id}/confirm",
            headers={
                "X-Request-ID": "order-confirm",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "order_id": order_id,
        }
    ]

    body = response.json()
    assert body["data"] == {
        "id": str(order_id),
        "dialog_id": str(thread_id),
        "contact_request_id": str(
            contact_request_id
        ),
        "status": "confirmed",
    }
    assert body["meta"] == {}
    assert body["request_id"] == "order-confirm"



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_status",
    ["draft", "confirmed"],
)
async def test_order_repository_cancels_locked_draft_or_confirmed_order(
    initial_status,
):
    from unittest.mock import AsyncMock

    from database.models import EventLog
    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()

    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        client_user_id=actor_user_id,
        specialist_user_id=uuid4(),
        status=initial_status,
        cancelled_by=None,
        cancelled_at=None,
        updated_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.commit = AsyncMock()

        def add(self, value):
            self.added.append(value)

    session = FakeSession()
    repository = ContactChatRepository(session)
    repository.get_service_order_for_update = (
        AsyncMock(return_value=order)
    )

    result = await repository.cancel_service_order(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        platform="api",
    )

    assert result is order
    repository.get_service_order_for_update.assert_awaited_once_with(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
    )

    assert order.status == "cancelled"
    assert order.cancelled_by == actor_user_id
    assert order.cancelled_at.tzinfo is UTC
    assert order.updated_at == order.cancelled_at

    events = [
        value
        for value in session.added
        if isinstance(value, EventLog)
    ]
    assert len(events) == 1
    assert events[0].event_type == (
        "service_order_cancelled"
    )
    assert events[0].tenant_id == tenant_id
    assert events[0].user_id == actor_user_id
    assert events[0].entity_id == order_id
    assert events[0].platform == "api"

    session.commit.assert_awaited_once()



@pytest.mark.asyncio
async def test_contact_service_reuses_shared_order_cancellation():
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    class FakeRepository:
        async def cancel_service_order(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=order_id,
                thread_id=thread_id,
                contact_request_id=(
                    contact_request_id
                ),
                status="cancelled",
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await service.cancel_service_order(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        platform="api",
    )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "order_id": order_id,
            "platform": "api",
        }
    ]
    assert result.order_id == order_id
    assert result.thread_id == thread_id
    assert (
        result.contact_request_id
        == contact_request_id
    )
    assert result.status == "cancelled"



@pytest.mark.asyncio
async def test_user_order_service_reuses_shared_cancellation_flow():
    from services.user_orders import (
        UserOrdersService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    expected = SimpleNamespace(
        order_id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        status="cancelled",
    )

    class FakeChats:
        async def cancel_service_order(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = UserOrdersService(
        chats=FakeChats(),
    )

    result = await service.cancel_order_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        order_id=order_id,
    )

    assert result is expected
    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": user_id,
            "order_id": order_id,
            "platform": "api",
        }
    ]



@pytest.mark.asyncio
async def test_order_cancel_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
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

    class FakeUserOrdersService:
        async def cancel_order_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                order_id=order_id,
                thread_id=thread_id,
                contact_request_id=(
                    contact_request_id
                ),
                status="cancelled",
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
        response = await client.post(
            f"/api/v1/orders/{order_id}/cancel",
            headers={
                "X-Request-ID": "order-cancel",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "order_id": order_id,
        }
    ]

    body = response.json()
    assert body["data"] == {
        "id": str(order_id),
        "dialog_id": str(thread_id),
        "contact_request_id": str(
            contact_request_id
        ),
        "status": "cancelled",
    }
    assert body["meta"] == {}
    assert body["request_id"] == "order-cancel"



@pytest.mark.asyncio
async def test_order_repository_completes_locked_confirmed_order():
    from unittest.mock import AsyncMock

    from database.models import EventLog
    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()

    order = SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        client_user_id=actor_user_id,
        specialist_user_id=uuid4(),
        specialist_id=uuid4(),
        professional_cabinet_id=uuid4(),
        status="confirmed",
        completed_by=None,
        completed_at=None,
        updated_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.commit = AsyncMock()

        async def get(self, model, entity_id):
            return order

        def add(self, value):
            self.added.append(value)

    session = FakeSession()
    repository = ContactChatRepository(session)
    repository.get_service_order_for_update = (
        AsyncMock(return_value=order)
    )

    result = await repository.complete_service_order(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        platform="api",
    )

    assert result is order
    repository.get_service_order_for_update.assert_awaited_once_with(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
    )

    assert order.status == "completed"
    assert order.completed_by == actor_user_id
    assert order.completed_at.tzinfo is UTC
    assert order.updated_at == order.completed_at

    events = [
        value
        for value in session.added
        if isinstance(value, EventLog)
    ]
    assert len(events) == 1
    assert events[0].event_type == (
        "service_order_completed"
    )
    assert events[0].tenant_id == tenant_id
    assert events[0].user_id == actor_user_id
    assert events[0].entity_id == order_id
    assert events[0].platform == "api"

    session.commit.assert_awaited_once()



@pytest.mark.asyncio
async def test_contact_service_forwards_neutral_completion_platform():
    from services.contact_chat import (
        ContactChatService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    class FakeRepository:
        async def complete_service_order(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                id=order_id,
                thread_id=thread_id,
                contact_request_id=(
                    contact_request_id
                ),
                status="completed",
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    result = await service.complete_service_order(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
        platform="api",
    )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "order_id": order_id,
            "platform": "api",
        }
    ]
    assert result.order_id == order_id
    assert result.thread_id == thread_id
    assert (
        result.contact_request_id
        == contact_request_id
    )
    assert result.status == "completed"



@pytest.mark.asyncio
async def test_user_order_service_reuses_shared_completion_flow():
    from services.user_orders import (
        UserOrdersService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
    calls = []

    expected = SimpleNamespace(
        order_id=order_id,
        thread_id=thread_id,
        contact_request_id=contact_request_id,
        status="completed",
    )

    class FakeChats:
        async def complete_service_order(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = UserOrdersService(
        chats=FakeChats(),
    )

    result = await service.complete_order_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        order_id=order_id,
    )

    assert result is expected
    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": user_id,
            "order_id": order_id,
            "platform": "api",
        }
    ]



@pytest.mark.asyncio
async def test_order_complete_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    thread_id = uuid4()
    contact_request_id = uuid4()
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

    class FakeUserOrdersService:
        async def complete_order_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                order_id=order_id,
                thread_id=thread_id,
                contact_request_id=(
                    contact_request_id
                ),
                status="completed",
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
        response = await client.post(
            f"/api/v1/orders/{order_id}/complete",
            headers={
                "X-Request-ID": "order-complete",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "order_id": order_id,
        }
    ]

    body = response.json()
    assert body["data"] == {
        "id": str(order_id),
        "dialog_id": str(thread_id),
        "contact_request_id": str(
            contact_request_id
        ),
        "status": "completed",
    }
    assert body["meta"] == {}
    assert body["request_id"] == "order-complete"



@pytest.mark.asyncio
async def test_order_repository_uses_typed_not_found_after_scoped_lock():
    from unittest.mock import AsyncMock

    from database.repositories.contact import (
        ContactChatRepository,
        ServiceOrderNotFoundError,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()

    repository = ContactChatRepository(
        SimpleNamespace()
    )
    repository.get_service_order_for_update = (
        AsyncMock(return_value=None)
    )

    with pytest.raises(
        ServiceOrderNotFoundError
    ):
        await repository.confirm_service_order(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            order_id=order_id,
            platform="api",
        )

    repository.get_service_order_for_update.assert_awaited_once_with(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        order_id=order_id,
    )



@pytest.mark.asyncio
async def test_contact_service_preserves_typed_order_not_found():
    from database.repositories.contact import (
        ServiceOrderNotFoundError,
    )
    from services.contact_chat import (
        ContactChatOrderNotFoundError,
        ContactChatService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    order_id = uuid4()

    class FakeRepository:
        async def confirm_service_order(
            self,
            **kwargs,
        ):
            raise ServiceOrderNotFoundError(
                "Foreign tenant order details."
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    with pytest.raises(
        ContactChatOrderNotFoundError
    ):
        await service.confirm_service_order(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            order_id=order_id,
            platform="api",
        )



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_method", "path_suffix"),
    [
        ("PATCH", ""),
        ("POST", "/confirm"),
        ("POST", "/cancel"),
        ("POST", "/complete"),
    ],
)
async def test_order_lifecycle_endpoints_hide_missing_or_foreign_order(
    http_method,
    path_suffix,
):
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext
    from services.contact_chat import (
        ContactChatOrderNotFoundError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    async def missing_order(**kwargs):
        raise ContactChatOrderNotFoundError(
            "Foreign tenant order details."
        )

    class FakeUserOrdersService:
        update_order_for_user = staticmethod(
            missing_order
        )
        confirm_order_for_user = staticmethod(
            missing_order
        )
        cancel_order_for_user = staticmethod(
            missing_order
        )
        complete_order_for_user = staticmethod(
            missing_order
        )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_orders_service
    ] = lambda: FakeUserOrdersService()

    request_kwargs = {
        "method": http_method,
        "url": (
            f"/api/v1/orders/{order_id}"
            f"{path_suffix}"
        ),
        "headers": {
            "X-Request-ID": "order-not-found",
        },
    }

    if http_method == "PATCH":
        request_kwargs["json"] = {
            "description": "Updated order",
            "start_at": None,
            "end_at": None,
            "agreed_amount": None,
            "currency": "EUR",
        }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.request(
            **request_kwargs
        )

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == (
        "order_not_found"
    )
    assert body["error"]["message"] == (
        "Order was not found."
    )
    assert body["error"]["request_id"] == (
        "order-not-found"
    )
    assert "Foreign tenant" not in response.text



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path_suffix",
    [
        "/confirm",
        "/cancel",
        "/complete",
    ],
)
async def test_order_status_endpoints_sanitize_invalid_transition(
    path_suffix,
):
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext
    from services.contact_chat import (
        ContactChatError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    async def invalid_transition(**kwargs):
        raise ContactChatError(
            "Private lifecycle details."
        )

    class FakeUserOrdersService:
        confirm_order_for_user = staticmethod(
            invalid_transition
        )
        cancel_order_for_user = staticmethod(
            invalid_transition
        )
        complete_order_for_user = staticmethod(
            invalid_transition
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
        response = await client.post(
            (
                f"/api/v1/orders/{order_id}"
                f"{path_suffix}"
            ),
            headers={
                "X-Request-ID": "order-conflict",
            },
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == (
        "order_conflict"
    )
    assert body["error"]["message"] == (
        "Order state does not allow this action."
    )
    assert body["error"]["request_id"] == (
        "order-conflict"
    )
    assert "Private lifecycle" not in response.text



@pytest.mark.asyncio
async def test_order_update_sanitizes_domain_validation():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext
    from services.contact_chat import ContactChatError

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserOrdersService:
        async def update_order_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatError(
                "Private validation details."
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
        response = await client.patch(
            f"/api/v1/orders/{order_id}",
            headers={
                "X-Request-ID": (
                    "order-update-validation"
                ),
            },
            json={
                "description": "Invalid terms",
                "start_at": None,
                "end_at": None,
                "agreed_amount": None,
                "currency": "EUR",
            },
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == (
        "order_validation_error"
    )
    assert body["error"]["message"] == (
        "Order data is not valid."
    )
    assert body["error"]["request_id"] == (
        "order-update-validation"
    )
    assert "Private validation" not in response.text



@pytest.mark.asyncio
async def test_order_create_hides_missing_or_foreign_dialog():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext
    from services.contact_chat import (
        ContactChatOrderNotFoundError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    dialog_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserOrdersService:
        async def create_order_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatOrderNotFoundError(
                "Private foreign tenant dialog details."
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
        response = await client.post(
            "/api/v1/orders",
            headers={
                "X-Request-ID": "order-create-not-found",
                "Idempotency-Key": (
                    "order-create-not-found-001"
                ),
            },
            json={
                "dialog_id": str(dialog_id),
                "description": "Scheduled consultation",
                "start_at": None,
                "end_at": None,
                "agreed_amount": None,
                "currency": "EUR",
            },
        )

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "order_not_found"
    assert body["error"]["message"] == (
        "Order was not found."
    )
    assert body["error"]["request_id"] == (
        "order-create-not-found"
    )
    assert "Private foreign" not in response.text



@pytest.mark.asyncio
async def test_order_create_repository_uses_typed_not_found_for_scoped_dialog():
    from unittest.mock import AsyncMock

    from database.repositories.contact import (
        ContactChatRepository,
        ContactThreadNotFoundError,
    )

    repository = ContactChatRepository(object())
    repository.get_thread_for_user = AsyncMock(
        return_value=None
    )

    with pytest.raises(ContactThreadNotFoundError):
        await repository.create_service_order_draft_from_thread(
            tenant_id=uuid4(),
            actor_user_id=uuid4(),
            thread_id=uuid4(),
            description="Scheduled consultation",
            schedule_text=None,
            start_at=None,
            end_at=None,
            agreed_amount=None,
            currency="EUR",
            platform="api",
        )



@pytest.mark.asyncio
async def test_contact_service_preserves_typed_dialog_not_found_during_order_create():
    from database.repositories.contact import (
        ContactThreadNotFoundError,
    )
    from services.contact_chat import (
        ContactChatOrderNotFoundError,
        ContactChatService,
    )

    class FakeRepository:
        async def create_service_order_draft_from_thread(
            self,
            **kwargs,
        ):
            raise ContactThreadNotFoundError(
                "Private foreign tenant dialog details."
            )

    service = ContactChatService(
        FakeRepository(),
        rate_limit_service=object(),
    )

    with pytest.raises(ContactChatOrderNotFoundError):
        await service.create_service_order_draft_from_thread(
            tenant_id=uuid4(),
            actor_user_id=uuid4(),
            thread_id=uuid4(),
            description="Scheduled consultation",
            schedule_text=None,
            start_at=None,
            end_at=None,
            agreed_amount=None,
            currency="EUR",
            platform="api",
        )



@pytest.mark.asyncio
async def test_order_create_sanitizes_domain_validation():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext
    from services.contact_chat import ContactChatError

    tenant_id = uuid4()
    user_id = uuid4()
    dialog_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserOrdersService:
        async def create_order_for_user(
            self,
            **kwargs,
        ):
            raise ContactChatError(
                "Private order validation details."
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
        response = await client.post(
            "/api/v1/orders",
            headers={
                "X-Request-ID": (
                    "order-create-validation"
                ),
                "Idempotency-Key": (
                    "order-create-validation-001"
                ),
            },
            json={
                "dialog_id": str(dialog_id),
                "description": "Invalid terms",
                "start_at": None,
                "end_at": None,
                "agreed_amount": None,
                "currency": "EUR",
            },
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == (
        "order_validation_error"
    )
    assert body["error"]["message"] == (
        "Order data is not valid."
    )
    assert body["error"]["request_id"] == (
        "order-create-validation"
    )
    assert "Private order validation" not in response.text



@pytest.mark.asyncio
async def test_user_order_create_replays_idempotent_result_without_duplicate():
    from types import SimpleNamespace

    from services.user_orders import UserOrdersService

    tenant_id = uuid4()
    user_id = uuid4()
    dialog_id = uuid4()
    order_id = uuid4()
    contact_request_id = uuid4()
    reserve_calls = []
    chat_calls = []

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            reserve_calls.append(kwargs)
            return SimpleNamespace(
                is_replay=True,
                response_status=201,
                response_payload={
                    "order_id": str(order_id),
                    "thread_id": str(dialog_id),
                    "contact_request_id": str(
                        contact_request_id
                    ),
                    "status": "draft",
                },
            )

        async def complete(self, **kwargs):
            raise AssertionError(
                "Replay must not be completed again."
            )

    class FakeChats:
        async def create_service_order_draft_from_thread(
            self,
            **kwargs,
        ):
            chat_calls.append(kwargs)
            raise AssertionError(
                "Replay must not create another order."
            )

    service = UserOrdersService(
        chats=FakeChats(),
        idempotency=FakeIdempotency(),
    )

    result = await service.create_order_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=dialog_id,
        description="Scheduled consultation",
        start_at=None,
        end_at=None,
        agreed_amount=125.5,
        currency="EUR",
        schedule_text=None,
        idempotency_key="same-order-key",
    )

    assert len(reserve_calls) == 1
    assert reserve_calls[0] == {
        "tenant_id": tenant_id,
        "principal_type": "user",
        "principal_id": user_id,
        "operation": "service_order.create",
        "idempotency_key": "same-order-key",
        "payload": {
            "dialog_id": str(dialog_id),
            "description": "Scheduled consultation",
            "start_at": None,
            "end_at": None,
            "agreed_amount": 125.5,
            "currency": "EUR",
        },
    }
    assert chat_calls == []

    assert result.order_id == order_id
    assert result.thread_id == dialog_id
    assert (
        result.contact_request_id
        == contact_request_id
    )
    assert result.status == "draft"



def test_user_orders_dependency_injects_shared_idempotency_service(
    monkeypatch,
):
    from cryptography.fernet import Fernet

    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_idempotency import (
        ApiIdempotencyService,
    )

    monkeypatch.setenv(
        "API_IDEMPOTENCY_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )

    session = object()
    service = get_user_orders_service(
        session=session,
    )

    assert isinstance(
        service.idempotency,
        ApiIdempotencyService,
    )
    assert (
        service.idempotency.repository.session
        is session
    )



@pytest.mark.asyncio
async def test_order_create_maps_idempotency_key_reuse_to_exact_conflict():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_orders_service,
    )
    from services.api_identity import ApiActorContext
    from services.api_idempotency import (
        ApiIdempotencyKeyReusedError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    dialog_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserOrdersService:
        async def create_order_for_user(
            self,
            **kwargs,
        ):
            raise ApiIdempotencyKeyReusedError(
                "Private request hash details."
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
        response = await client.post(
            "/api/v1/orders",
            headers={
                "X-Request-ID": (
                    "order-idempotency-conflict"
                ),
                "Idempotency-Key": (
                    "reused-order-key-001"
                ),
            },
            json={
                "dialog_id": str(dialog_id),
                "description": "Different request body",
                "start_at": None,
                "end_at": None,
                "agreed_amount": None,
                "currency": "EUR",
            },
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == (
        "IDEMPOTENCY_KEY_REUSED"
    )
    assert body["error"]["message"] == (
        "Idempotency key was reused with "
        "a different request."
    )
    assert body["error"]["request_id"] == (
        "order-idempotency-conflict"
    )
    assert "Private request hash" not in response.text



@pytest.mark.asyncio
async def test_user_order_create_commits_order_and_idempotency_response_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_orders import UserOrdersService

    tenant_id = uuid4()
    user_id = uuid4()
    dialog_id = uuid4()
    order_id = uuid4()
    contact_request_id = uuid4()
    record = object()
    events = []
    chat_calls = []

    class FakeIdempotency:
        async def reserve(self, **kwargs):
            events.append("reserve")
            return SimpleNamespace(
                record=record,
                is_replay=False,
                response_status=None,
                response_payload=None,
            )

        async def complete(self, **kwargs):
            events.append("complete")
            assert kwargs["reservation"].record is record
            assert kwargs["response_status"] == 201

        async def commit(self):
            events.append("commit")

    class FakeChats:
        async def create_service_order_draft_from_thread(
            self,
            **kwargs,
        ):
            events.append("create")
            chat_calls.append(kwargs)
            return SimpleNamespace(
                order_id=order_id,
                thread_id=dialog_id,
                contact_request_id=contact_request_id,
                status="draft",
            )

    service = UserOrdersService(
        chats=FakeChats(),
        idempotency=FakeIdempotency(),
    )

    result = await service.create_order_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=dialog_id,
        description="Scheduled consultation",
        start_at=None,
        end_at=None,
        agreed_amount=125.5,
        currency="EUR",
        schedule_text=None,
        idempotency_key="atomic-order-key",
    )

    assert result.order_id == order_id
    assert events == [
        "reserve",
        "create",
        "complete",
        "commit",
    ]
    assert len(chat_calls) == 1
    assert chat_calls[0]["commit"] is False
