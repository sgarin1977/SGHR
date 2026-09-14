from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_neutral_review_create_uses_actor_scope():
    from services.user_reviews import (
        UserReviewCreateAction,
        UserReviewsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    contact_request_id = uuid4()
    review_id = uuid4()
    calls = []

    review = SimpleNamespace(
        id=review_id,
        tenant_id=tenant_id,
        reviewer_user_id=user_id,
        context_type="contact_request",
        context_id=contact_request_id,
        rating=5,
        text="Excellent consultation.",
        status="pending_moderation",
    )

    class FakeReviews:
        async def create_contact_review(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return review

    service = UserReviewsService(
        object(),
        reviews=FakeReviews(),
    )

    action = await service.create_review_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        context_type="contact_request",
        context_id=contact_request_id,
        rating=5,
        text="Excellent consultation.",
    )

    assert isinstance(
        action,
        UserReviewCreateAction,
    )
    assert action.review is review

    assert calls == [
        {
            "tenant_id": tenant_id,
            "reviewer_user_id": user_id,
            "contact_request_id": (
                contact_request_id
            ),
            "rating": 5,
            "text": "Excellent consultation.",
        }
    ]


@pytest.mark.asyncio
async def test_neutral_order_review_uses_actor_scope():
    from services.user_reviews import (
        UserReviewCreateAction,
        UserReviewsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    order_id = uuid4()
    calls = []

    review = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        reviewer_user_id=user_id,
        context_type="service_order",
        context_id=order_id,
        rating=4,
        text=None,
        status="pending_moderation",
    )

    class FakeReviews:
        async def create_service_order_review(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return review

    service = UserReviewsService(
        object(),
        reviews=FakeReviews(),
    )

    action = await service.create_review_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        context_type="service_order",
        context_id=order_id,
        rating=4,
        text=None,
    )

    assert isinstance(
        action,
        UserReviewCreateAction,
    )
    assert action.review is review

    assert calls == [
        {
            "tenant_id": tenant_id,
            "reviewer_user_id": user_id,
            "service_order_id": order_id,
            "rating": 4,
            "text": None,
        }
    ]


@pytest.mark.asyncio
async def test_review_create_endpoint_uses_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_reviews_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.user_reviews import (
        UserReviewCreateAction,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    contact_request_id = uuid4()
    review_id = uuid4()
    created_at = datetime(
        2026,
        9,
        2,
        18,
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

    review = SimpleNamespace(
        id=review_id,
        context_type="contact_request",
        context_id=contact_request_id,
        rating=5,
        text="Excellent consultation.",
        status="pending_moderation",
        created_at=created_at,
        tenant_id=tenant_id,
        reviewer_user_id=user_id,
    )

    class FakeUserReviewsService:
        async def create_review_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return UserReviewCreateAction(
                review=review,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_reviews_service
    ] = lambda: FakeUserReviewsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/reviews",
            headers={
                "X-Request-ID": "review-create",
            },
            json={
                "context_type": "contact_request",
                "context_id": str(
                    contact_request_id
                ),
                "rating": 5,
                "text": "Excellent consultation.",
            },
        )

    assert response.status_code == 201
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "context_type": "contact_request",
            "context_id": contact_request_id,
            "rating": 5,
            "text": "Excellent consultation.",
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(review_id),
            "context_type": "contact_request",
            "context_id": str(
                contact_request_id
            ),
            "rating": 5,
            "text": "Excellent consultation.",
            "status": "pending_moderation",
            "created_at": (
                "2026-09-02T18:00:00Z"
            ),
        },
        "meta": {},
        "request_id": "review-create",
    }

    assert "tenant_id" not in response.text
    assert "reviewer_user_id" not in response.text


@pytest.mark.asyncio
async def test_review_create_rejects_actor_fields():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_reviews_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class ForbiddenService:
        async def create_review_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Service must not be called "
                "for invalid request data."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_reviews_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/reviews",
            headers={
                "X-Request-ID": (
                    "review-actor-fields"
                ),
            },
            json={
                "context_type": "service_order",
                "context_id": str(uuid4()),
                "rating": 5,
                "text": "Test review",
                "reviewer_user_id": str(
                    uuid4()
                ),
                "tenant_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": (
                "Request validation failed."
            ),
            "request_id": (
                "review-actor-fields"
            ),
        },
    }


@pytest.mark.asyncio
async def test_neutral_review_create_sanitizes_domain_error():
    from services.reviews import ReviewServiceError
    from services.user_reviews import (
        UserReviewsCreateError,
        UserReviewsService,
    )

    class FakeReviews:
        async def create_contact_review(
            self,
            **kwargs,
        ):
            raise ReviewServiceError(
                "Private review repository details."
            )

    service = UserReviewsService(
        object(),
        reviews=FakeReviews(),
    )

    with pytest.raises(
        UserReviewsCreateError,
        match="Review cannot be created.",
    ):
        await service.create_review_for_user(
            tenant_id=uuid4(),
            user_id=uuid4(),
            context_type="contact_request",
            context_id=uuid4(),
            rating=5,
            text="Test review",
        )


@pytest.mark.asyncio
async def test_review_create_conflict_is_sanitized():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_reviews_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.user_reviews import (
        UserReviewsCreateError,
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class ConflictingReviewsService:
        async def create_review_for_user(
            self,
            **kwargs,
        ):
            raise UserReviewsCreateError(
                "Private review details."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_reviews_service
    ] = lambda: ConflictingReviewsService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/reviews",
            headers={
                "X-Request-ID": (
                    "review-conflict"
                ),
            },
            json={
                "context_type": "contact_request",
                "context_id": str(uuid4()),
                "rating": 5,
                "text": "Test review",
            },
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "review_conflict",
            "message": (
                "Review cannot be created."
            ),
            "request_id": "review-conflict",
        },
    }

    assert (
        "Private review details."
        not in response.text
    )


@pytest.mark.asyncio
async def test_review_create_requires_authentication():
    import httpx

    from api.app import create_app

    request_id = "review-auth-required"
    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/reviews",
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "context_type": "service_order",
                "context_id": str(uuid4()),
                "rating": 5,
                "text": "Test review",
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": request_id,
        },
    }

