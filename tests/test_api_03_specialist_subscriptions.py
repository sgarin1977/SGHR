def test_subscription_model_matches_database_contract():
    from database.models import Subscription

    assert Subscription.__tablename__ == (
        "subscriptions"
    )

    columns = {
        column.name: column
        for column in Subscription.__table__.columns
    }

    assert set(columns) == {
        "id",
        "tenant_id",
        "user_id",
        "professional_cabinet_id",
        "plan_code",
        "status",
        "billing_period",
        "amount",
        "currency",
        "provider",
        "provider_subscription_id",
        "starts_at",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "cancelled_at",
        "ended_at",
        "metadata",
        "created_at",
        "updated_at",
    }

    assert columns["tenant_id"].nullable is False
    assert columns["user_id"].nullable is False
    assert (
        columns[
            "professional_cabinet_id"
        ].nullable
        is True
    )
    assert columns["plan_code"].nullable is False
    assert columns["status"].nullable is False
    assert (
        columns["billing_period"].nullable
        is False
    )
    assert columns["amount"].nullable is False
    assert columns["currency"].nullable is False
    assert (
        columns[
            "cancel_at_period_end"
        ].nullable
        is False
    )
    assert columns["metadata"].nullable is False

    foreign_keys = {
        (
            column.name,
            foreign_key.target_fullname,
        )
        for column in columns.values()
        for foreign_key in column.foreign_keys
    }

    assert foreign_keys == {
        ("tenant_id", "tenants.id"),
        ("user_id", "users.id"),
        (
            "professional_cabinet_id",
            "professional_cabinets.id",
        ),
    }


import pytest
from types import SimpleNamespace
from uuid import uuid4


@pytest.mark.asyncio
async def test_subscription_repository_uses_actor_and_cabinet_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.billing import (
        BillingRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    cabinet_id = uuid4()

    expected = [
        SimpleNamespace(
            id=uuid4(),
            status="active",
        ),
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
    repository = BillingRepository(session)

    result = await (
        repository
        .list_subscriptions_for_cabinet(
            tenant_id=tenant_id,
            user_id=user_id,
            professional_cabinet_id=(
                cabinet_id
            ),
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
        "subscriptions.tenant_id",
        "subscriptions.user_id",
        (
            "subscriptions."
            "professional_cabinet_id"
        ),
        "subscriptions.created_at desc",
    )

    for value in required_scope:
        assert value in normalized_sql


@pytest.mark.asyncio
async def test_neutral_subscription_list_uses_owned_cabinet_scope():
    from services.specialist_subscriptions import (
        SpecialistSubscriptionsService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    subscriptions = (
        SimpleNamespace(
            id=uuid4(),
            status="active",
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
            calls.append(
                ("cabinet", kwargs)
            )
            return SimpleNamespace(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeSubscriptions:
        async def list_subscriptions_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(
                ("subscriptions", kwargs)
            )
            return subscriptions

    service = SpecialistSubscriptionsService(
        object(),
        cabinets=FakeCabinets(),
        subscriptions=FakeSubscriptions(),
    )

    action = await (
        service.list_subscriptions_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=(
                cabinet_id
            ),
            limit=20,
            offset=0,
        )
    )

    assert action.actor is actor
    assert action.result is subscriptions
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
            "subscriptions",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "limit": 20,
                "offset": 0,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_billing_service_maps_safe_subscription_views():
    from datetime import UTC, datetime
    from decimal import Decimal

    from services.billing import (
        BillingService,
        SpecialistSubscriptionView,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    cabinet_id = uuid4()
    subscription_id = uuid4()

    starts_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    period_start = starts_at
    period_end = datetime(
        2026,
        9,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    created_at = datetime(
        2026,
        8,
        19,
        9,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    calls = []

    row = SimpleNamespace(
        id=subscription_id,
        tenant_id=tenant_id,
        user_id=user_id,
        professional_cabinet_id=cabinet_id,
        plan_code="specialist_premium",
        status="active",
        billing_period="month",
        amount=Decimal("9.00"),
        currency="EUR",
        provider="private-provider",
        provider_subscription_id=(
            "private-provider-id"
        ),
        starts_at=starts_at,
        current_period_start=period_start,
        current_period_end=period_end,
        cancel_at_period_end=False,
        cancelled_at=None,
        ended_at=None,
        extra_metadata={
            "private": True,
        },
        created_at=created_at,
        updated_at=updated_at,
    )

    class FakeRepository:
        async def list_subscriptions_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return [row]

    service = BillingService(
        FakeRepository()
    )

    result = await (
        service.list_subscriptions_for_cabinet(
            tenant_id=tenant_id,
            user_id=user_id,
            professional_cabinet_id=(
                cabinet_id
            ),
            limit=20,
            offset=0,
        )
    )

    assert result == [
        SpecialistSubscriptionView(
            id=subscription_id,
            plan_code="specialist_premium",
            status="active",
            billing_period="month",
            amount=Decimal("9.00"),
            currency="EUR",
            starts_at=starts_at,
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=False,
            cancelled_at=None,
            ended_at=None,
            created_at=created_at,
            updated_at=updated_at,
        )
    ]

    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "limit": 20,
            "offset": 0,
        }
    ]

    assert set(vars(result[0])) == {
        "id",
        "plan_code",
        "status",
        "billing_period",
        "amount",
        "currency",
        "starts_at",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "cancelled_at",
        "ended_at",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_specialist_cabinet_subscriptions_use_current_actor():
    from datetime import UTC, datetime
    from decimal import Decimal

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_subscriptions_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.billing import (
        SpecialistSubscriptionView,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    subscription_id = uuid4()
    request_id = (
        "specialist-subscriptions-request"
    )
    calls = []

    starts_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    period_end = datetime(
        2026,
        9,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    created_at = datetime(
        2026,
        8,
        19,
        9,
        0,
        tzinfo=UTC,
    )
    updated_at = starts_at

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.subscriptions.read",
        ),
    )

    item = SpecialistSubscriptionView(
        id=subscription_id,
        plan_code="specialist_premium",
        status="active",
        billing_period="month",
        amount=Decimal("9.00"),
        currency="EUR",
        starts_at=starts_at,
        current_period_start=starts_at,
        current_period_end=period_end,
        cancel_at_period_end=False,
        cancelled_at=None,
        ended_at=None,
        created_at=created_at,
        updated_at=updated_at,
    )

    class FakeSpecialistSubscriptions:
        async def list_subscriptions_for_user(
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
        get_specialist_subscriptions_service
    ] = lambda: FakeSpecialistSubscriptions()

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
                f"{cabinet_id}/subscriptions"
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
                "id": str(subscription_id),
                "plan_code": (
                    "specialist_premium"
                ),
                "status": "active",
                "billing_period": "month",
                "amount": "9.00",
                "currency": "EUR",
                "starts_at": (
                    "2026-08-20T10:00:00Z"
                ),
                "current_period_start": (
                    "2026-08-20T10:00:00Z"
                ),
                "current_period_end": (
                    "2026-09-20T10:00:00Z"
                ),
                "cancel_at_period_end": False,
                "cancelled_at": None,
                "ended_at": None,
                "created_at": (
                    "2026-08-19T09:00:00Z"
                ),
                "updated_at": (
                    "2026-08-20T10:00:00Z"
                ),
            }
        ],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": request_id,
    }

    assert "provider_subscription_id" not in (
        response.text
    )
    assert "provider" not in response.text
    assert "metadata" not in response.text


@pytest.mark.asyncio
async def test_specialist_cabinet_subscriptions_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_subscriptions_service,
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

    class ForbiddenSubscriptionsService:
        async def list_subscriptions_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Subscriptions service must not run "
                "without read permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_subscriptions_service
    ] = lambda: ForbiddenSubscriptionsService()

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
                f"{uuid4()}/subscriptions"
            )
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_specialist_cabinet_subscriptions_require_authentication():
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
                f"{uuid4()}/subscriptions"
            )
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )


@pytest.mark.asyncio
async def test_specialist_cabinet_subscriptions_hide_missing_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_subscriptions_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsSelectionError,
    )

    request_id = (
        "specialist-subscriptions-missing"
    )
    private_detail = (
        "foreign tenant subscription cabinet"
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
            "specialist.subscriptions.read",
        ),
    )

    class MissingSubscriptionsService:
        async def list_subscriptions_for_user(
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
        get_specialist_subscriptions_service
    ] = lambda: MissingSubscriptionsService()

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
                f"{uuid4()}/subscriptions"
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


def test_specialist_subscriptions_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/subscriptions"
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
            "SpecialistSubscriptionListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    properties = set(
        components[
            "SpecialistSubscriptionItem"
        ]["properties"]
    )

    assert properties == {
        "id",
        "plan_code",
        "status",
        "billing_period",
        "amount",
        "currency",
        "starts_at",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "cancelled_at",
        "ended_at",
        "created_at",
        "updated_at",
    }

    assert not (
        {
            "tenant_id",
            "user_id",
            "professional_cabinet_id",
            "provider",
            "provider_subscription_id",
            "metadata",
        }
        & properties
    )

