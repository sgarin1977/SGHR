from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_cabinets import (
    SpecialistCabinetsActor,
)
from services.specialist_reviews import (
    SpecialistReviewsService,
)


@pytest.mark.asyncio
async def test_neutral_review_list_uses_owned_cabinet_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
    )
    review_page = SimpleNamespace(
        reviews=[],
        reputation=None,
        total_count=0,
        page=0,
        page_size=10,
        has_previous=False,
        has_next=False,
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
                result=cabinet,
            )

    class FakeReviews:
        async def list_public_reviews_for_viewer(
            self,
            **kwargs,
        ):
            calls.append(
                ("reviews", kwargs)
            )
            return review_page

    service = SpecialistReviewsService(
        object(),
        cabinets=FakeCabinets(),
        reviews=FakeReviews(),
    )

    result = await service.list_reviews_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        page=0,
        page_size=10,
        platform="api",
    )

    assert result.actor is actor
    assert result.result is review_page
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
            "reviews",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "viewer_user_id": user_id,
                "page": 0,
                "page_size": 10,
                "source": (
                    "specialist_cabinet"
                ),
                "platform": "api",
            },
        ),
    ]

@pytest.mark.asyncio
async def test_review_cabinet_scope_rejects_foreign_cabinet():
    from services.specialist_cabinets import (
        SpecialistCabinetsSelectionError,
        SpecialistCabinetsService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()

    class FakeRepository:
        async def get_by_user_id(
            self,
            value,
        ):
            assert value == user_id
            return SimpleNamespace(
                id=specialist_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )

        async def get_professional_cabinet(
            self,
            **kwargs,
        ):
            assert kwargs == {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            }
            return None

    service = SpecialistCabinetsService(
        object(),
        settings=object(),
        repository=FakeRepository(),
        specialists=object(),
    )

    with pytest.raises(
        SpecialistCabinetsSelectionError
    ):
        await (
            service
            .require_owned_cabinet_for_user(
                user_id=user_id,
                tenant_id=tenant_id,
                language="uk",
                professional_cabinet_id=(
                    cabinet_id
                ),
            )
        )

@pytest.mark.asyncio
async def test_specialist_cabinet_reviews_use_current_actor():
    from datetime import (
        UTC,
        datetime,
    )

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_reviews_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    review_id = uuid4()
    request_id = "specialist-reviews-list"
    created_at = datetime(
        2026,
        8,
        29,
        14,
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
            "specialist.reviews.read",
        ),
    )

    review_page = SimpleNamespace(
        reviews=[
            SimpleNamespace(
                id=review_id,
                rating=5,
                text="Excellent service",
                specialist_reply="Thank you",
                created_at=created_at,
                reviewer_user_id=uuid4(),
                tenant_id=tenant_id,
                target_id=cabinet_id,
            )
        ],
        reputation=SimpleNamespace(
            score=4.8,
            review_count=12,
            complaint_count=3,
        ),
        total_count=12,
        page=0,
        page_size=10,
        has_previous=False,
        has_next=True,
    )

    class FakeSpecialistReviews:
        async def list_reviews_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=review_page,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_reviews_service
    ] = lambda: FakeSpecialistReviews()

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
                f"{cabinet_id}/reviews"
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
            "page": 0,
            "page_size": 10,
            "platform": "api",
        }
    ]

    assert response.json() == {
        "data": {
            "items": [
                {
                    "id": str(review_id),
                    "rating": 5,
                    "text": "Excellent service",
                    "specialist_reply": (
                        "Thank you"
                    ),
                    "created_at": (
                        "2026-08-29T14:00:00Z"
                    ),
                }
            ],
            "reputation": {
                "score": 4.8,
                "review_count": 12,
            },
        },
        "meta": {
            "next_cursor": "MQ",
            "has_more": True,
        },
        "request_id": request_id,
    }

    private_fields = (
        "reviewer_user_id",
        "tenant_id",
        "target_id",
        "complaint_count",
    )

    for field in private_fields:
        assert field not in response.text

@pytest.mark.asyncio
async def test_specialist_cabinet_reviews_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_reviews_service,
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

    class ForbiddenReviews:
        async def list_reviews_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Reviews service must not "
                "be called without permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_reviews_service
    ] = lambda: ForbiddenReviews()

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
                f"{uuid4()}/reviews"
            )
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []

@pytest.mark.asyncio
async def test_specialist_cabinet_reviews_require_authentication():
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
                f"{uuid4()}/reviews"
            )
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )

@pytest.mark.asyncio
async def test_specialist_reviews_hide_foreign_cabinet_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_reviews_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsSelectionError,
    )

    private_details = (
        "private foreign tenant review details"
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
            "specialist.reviews.read",
        ),
    )

    class ForeignReviews:
        async def list_reviews_for_user(
            self,
            **kwargs,
        ):
            raise (
                SpecialistCabinetsSelectionError(
                    private_details
                )
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_reviews_service
    ] = lambda: ForeignReviews()

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
                f"{uuid4()}/reviews"
            )
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == (
        "cabinet_not_found"
    )
    assert private_details not in response.text

def test_specialist_reviews_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/reviews"
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
            "SpecialistReviewListResponse"
        )
    }

    properties = schema[
        "components"
    ]["schemas"][
        "SpecialistReviewItem"
    ]["properties"]

    private_fields = {
        "tenant_id",
        "reviewer_user_id",
        "professional_cabinet_id",
        "target_id",
        "context_id",
    }

    assert private_fields.isdisjoint(
        properties
    )
