from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_neutral_promotion_list_uses_owned_cabinet_scope():
    from services.specialist_promotions import (
        SpecialistPromotionsService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    promotions = (
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

    class FakePromotions:
        async def list_promotions_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(
                ("promotions", kwargs)
            )
            return promotions

    service = SpecialistPromotionsService(
        object(),
        cabinets=FakeCabinets(),
        promotions=FakePromotions(),
    )

    action = await (
        service.list_promotions_for_user(
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
    assert action.result is promotions
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
            "promotions",
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
async def test_promotion_repository_uses_full_owner_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.billing import (
        BillingRepository,
    )

    tenant_id = uuid4()
    specialist_id = uuid4()
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
        .list_specialist_promotions_for_cabinet(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
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
        "specialist_promotions.tenant_id",
        "specialist_promotions.specialist_id",
        (
            "specialist_promotions."
            "professional_cabinet_id"
        ),
        "specialist_promotions.created_at desc",
    )

    for value in required_scope:
        assert value in normalized_sql


@pytest.mark.asyncio
async def test_billing_service_maps_safe_promotion_views():
    from datetime import UTC, datetime
    from decimal import Decimal

    from services.billing import (
        BillingService,
        SpecialistPromotionView,
    )

    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    promotion_id = uuid4()

    starts_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    ends_at = datetime(
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
    calls = []

    row = SimpleNamespace(
        id=promotion_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
        promotion_type="premium",
        starts_at=starts_at,
        ends_at=ends_at,
        price=Decimal("9.00"),
        currency="EUR",
        invoice_id=uuid4(),
        status="active",
        created_at=created_at,
    )

    class FakeRepository:
        async def list_specialist_promotions_for_cabinet(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return [row]

    service = BillingService(
        FakeRepository()
    )

    result = await (
        service.list_promotions_for_cabinet(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            professional_cabinet_id=(
                cabinet_id
            ),
            limit=20,
            offset=0,
        )
    )

    assert result == [
        SpecialistPromotionView(
            id=promotion_id,
            promotion_type="premium",
            starts_at=starts_at,
            ends_at=ends_at,
            price=Decimal("9.00"),
            currency="EUR",
            status="active",
            created_at=created_at,
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
        "id",
        "promotion_type",
        "starts_at",
        "ends_at",
        "price",
        "currency",
        "status",
        "created_at",
    }


@pytest.mark.asyncio
async def test_specialist_cabinet_promotions_use_current_actor():
    from datetime import UTC, datetime
    from decimal import Decimal

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_promotions_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.billing import (
        SpecialistPromotionView,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    promotion_id = uuid4()
    request_id = "specialist-promotions-request"
    calls = []

    starts_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    ends_at = datetime(
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

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.promotions.read",
        ),
    )

    item = SpecialistPromotionView(
        id=promotion_id,
        promotion_type="premium",
        starts_at=starts_at,
        ends_at=ends_at,
        price=Decimal("9.00"),
        currency="EUR",
        status="active",
        created_at=created_at,
    )

    class FakeSpecialistPromotions:
        async def list_promotions_for_user(
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
        get_specialist_promotions_service
    ] = lambda: FakeSpecialistPromotions()

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
                f"{cabinet_id}/promotions"
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
                "id": str(promotion_id),
                "promotion_type": "premium",
                "starts_at": (
                    "2026-08-20T10:00:00Z"
                ),
                "ends_at": (
                    "2026-09-20T10:00:00Z"
                ),
                "price": "9.00",
                "currency": "EUR",
                "status": "active",
                "created_at": (
                    "2026-08-19T09:00:00Z"
                ),
            }
        ],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": request_id,
    }

    assert "invoice_id" not in response.text
    assert "tenant_id" not in response.text
    assert "specialist_id" not in response.text


@pytest.mark.asyncio
async def test_specialist_cabinet_promotions_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_promotions_service,
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

    class ForbiddenPromotionsService:
        async def list_promotions_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Promotions service must not run "
                "without read permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_promotions_service
    ] = lambda: ForbiddenPromotionsService()

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
                f"{uuid4()}/promotions"
            )
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_specialist_cabinet_promotions_require_authentication():
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
                f"{uuid4()}/promotions"
            )
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )


@pytest.mark.asyncio
async def test_specialist_cabinet_promotions_hide_missing_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_promotions_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsSelectionError,
    )

    request_id = (
        "specialist-promotions-missing"
    )
    private_detail = (
        "foreign tenant promotion cabinet"
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
            "specialist.promotions.read",
        ),
    )

    class MissingPromotionsService:
        async def list_promotions_for_user(
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
        get_specialist_promotions_service
    ] = lambda: MissingPromotionsService()

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
                f"{uuid4()}/promotions"
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


def test_specialist_promotions_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/promotions"
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
            "SpecialistPromotionListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    properties = set(
        components[
            "SpecialistPromotionItem"
        ]["properties"]
    )

    assert properties == {
        "id",
        "promotion_type",
        "starts_at",
        "ends_at",
        "price",
        "currency",
        "status",
        "created_at",
    }

    assert not (
        {
            "tenant_id",
            "specialist_id",
            "professional_cabinet_id",
            "invoice_id",
        }
        & properties
    )

