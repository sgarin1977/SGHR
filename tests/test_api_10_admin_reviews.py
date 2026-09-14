from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_admin_repository_lists_reviews_by_server_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.admin_scope import (
        AdminScopeContext,
    )
    from database.repositories.api_admin import (
        AdminApiRepository,
    )

    tenant_id = uuid4()
    country_id = uuid4()
    admin_user_id = uuid4()
    expected = [
        object(),
        object(),
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

    scope_context = AdminScopeContext(
        admin_user_id=admin_user_id,
        tenant_id=tenant_id,
        is_global=False,
        country_ids=frozenset(
            {country_id}
        ),
        language_codes=frozenset(
            {"uk"}
        ),
    )

    session = FakeSession()
    repository = AdminApiRepository(session)

    result = await repository.list_reviews(
        scope_context=scope_context,
        status="pending_moderation",
        limit=21,
        offset=0,
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

    assert "FROM reviews" in sql
    assert (
        "JOIN professional_cabinets"
        in sql
    )
    assert "JOIN specialists" in sql
    assert "JOIN users" in sql

    assert "reviews.tenant_id" in sql
    assert (
        "professional_cabinets.tenant_id"
        in sql
    )
    assert "specialists.tenant_id" in sql
    assert "users.tenant_id" in sql
    assert str(tenant_id) in sql

    assert "reviews.status" in sql
    assert "'pending_moderation'" in sql

    assert "coalesce(" in sql.lower()
    assert str(country_id) in sql
    assert "lower(users.language_code)" in sql
    assert "'uk'" in sql
    assert "LIMIT 21" in sql
    assert "OFFSET 0" in sql



@pytest.mark.asyncio
async def test_admin_reviews_service_uses_current_actor_scope():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_admin_reviews import (
        ApiAdminReviewPage,
        ApiAdminReviewView,
        ApiAdminReviewsService,
    )
    from services.api_identity import (
        ApiActorContext,
        ApiRoleScopeContext,
    )

    tenant_id = uuid4()
    moderator_user_id = uuid4()
    reviewer_user_id = uuid4()
    review_id = uuid4()
    cabinet_id = uuid4()
    specialist_id = uuid4()
    country_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        3,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=moderator_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "moderation.reviews.view",
        ),
        role_scopes=(
            ApiRoleScopeContext(
                role="moderator",
                scope_type="country",
                scope_id=country_id,
                scope_code=None,
            ),
        ),
    )

    review = SimpleNamespace(
        id=review_id,
        tenant_id=tenant_id,
        reviewer_user_id=reviewer_user_id,
        professional_cabinet_id=cabinet_id,
        service_order_id=None,
        context_type="contact_request",
        context_id=uuid4(),
        rating=5,
        text="Excellent service.",
        status="pending_moderation",
        published_at=None,
        created_at=created_at,
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
    )
    specialist = SimpleNamespace(
        id=specialist_id,
        tenant_id=tenant_id,
    )

    class FakeRepository:
        async def list_reviews(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            row = (
                review,
                cabinet,
                specialist,
            )
            return [row, row, row]

    service = ApiAdminReviewsService(
        repository=FakeRepository(),
    )

    result = await service.list_reviews(
        actor=actor,
        status="pending_moderation",
        page=1,
        page_size=2,
    )

    assert isinstance(
        result,
        ApiAdminReviewPage,
    )
    assert len(result.items) == 2
    assert result.page == 1
    assert result.has_next is True

    item = result.items[0]
    assert isinstance(
        item,
        ApiAdminReviewView,
    )
    assert item.id == review_id
    assert item.reviewer_user_id == (
        reviewer_user_id
    )
    assert (
        item.professional_cabinet_id
        == cabinet_id
    )
    assert item.specialist_id == specialist_id
    assert item.rating == 5
    assert item.status == (
        "pending_moderation"
    )

    assert not hasattr(item, "tenant_id")
    assert not hasattr(item, "metadata")

    assert len(calls) == 1
    scope_context = calls[0][
        "scope_context"
    ]
    assert (
        scope_context.admin_user_id
        == moderator_user_id
    )
    assert scope_context.tenant_id == tenant_id
    assert scope_context.country_ids == (
        frozenset({country_id})
    )
    assert calls[0]["status"] == (
        "pending_moderation"
    )
    assert calls[0]["limit"] == 3
    assert calls[0]["offset"] == 2



@pytest.mark.asyncio
async def test_admin_reviews_endpoint_uses_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_reviews_service,
    )
    from services.api_admin_reviews import (
        ApiAdminReviewPage,
        ApiAdminReviewView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    moderator_user_id = uuid4()
    reviewer_user_id = uuid4()
    review_id = uuid4()
    cabinet_id = uuid4()
    specialist_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        4,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=moderator_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "moderation.reviews.view",
        ),
    )

    item = ApiAdminReviewView(
        id=review_id,
        reviewer_user_id=reviewer_user_id,
        professional_cabinet_id=cabinet_id,
        specialist_id=specialist_id,
        service_order_id=None,
        context_type="contact_request",
        context_id=uuid4(),
        rating=5,
        text="Excellent service.",
        status="pending_moderation",
        published_at=None,
        created_at=created_at,
    )

    class FakeService:
        async def list_reviews(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiAdminReviewPage(
                items=(item,),
                page=0,
                has_next=False,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_reviews_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/admin/reviews",
            params={
                "status": (
                    "pending_moderation"
                ),
                "limit": 20,
            },
            headers={
                "X-Request-ID": (
                    "admin-reviews-list"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["data"][0]["id"] == (
        str(review_id)
    )
    assert "tenant_id" not in (
        payload["data"][0]
    )
    assert calls == [
        {
            "actor": actor,
            "status": (
                "pending_moderation"
            ),
            "page": 0,
            "page_size": 20,
        }
    ]

    parameters = application.openapi()[
        "paths"
    ][
        "/api/v1/admin/reviews"
    ]["get"].get("parameters", [])

    assert all(
        parameter["name"] != "tenant_id"
        for parameter in parameters
    )



@pytest.mark.asyncio
async def test_admin_review_moderation_reuses_existing_scoped_flow():
    from types import SimpleNamespace

    from services.api_admin_reviews import (
        ApiAdminReviewModerationResult,
        ApiAdminReviewsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    moderator_user_id = uuid4()
    review_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=moderator_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "moderation.reviews.hide",
        ),
    )

    class FakeReviews:
        async def moderate_review(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                review=SimpleNamespace(
                    id=review_id,
                    status=kwargs["status"],
                ),
            )

    service = ApiAdminReviewsService(
        repository=object(),
        reviews=FakeReviews(),
    )

    result = await service.moderate_review(
        actor=actor,
        review_id=review_id,
        status="published",
        reason="Content verified.",
    )

    assert isinstance(
        result,
        ApiAdminReviewModerationResult,
    )
    assert result.review_id == review_id
    assert result.status == "published"

    assert calls == [
        {
            "tenant_id": tenant_id,
            "moderator_user_id": (
                moderator_user_id
            ),
            "review_id": review_id,
            "status": "published",
            "reason": "Content verified.",
        }
    ]



@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "action",
        "service_status",
    ),
    (
        ("publish", "published"),
        ("hide", "hidden"),
    ),
)
async def test_admin_review_moderation_endpoints_use_current_actor(
    action,
    service_status,
):
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_reviews_service,
    )
    from services.api_admin_reviews import (
        ApiAdminReviewModerationResult,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    moderator_user_id = uuid4()
    review_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=moderator_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "moderation.reviews.hide",
        ),
    )

    class FakeService:
        async def moderate_review(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiAdminReviewModerationResult(
                review_id=review_id,
                status=kwargs["status"],
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_reviews_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                f"/api/v1/admin/reviews/"
                f"{review_id}/{action}"
            ),
            json={
                "reason": "Moderation decision.",
            },
            headers={
                "X-Request-ID": (
                    f"admin-review-{action}"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["data"] == {
        "review_id": str(review_id),
        "status": service_status,
    }
    assert calls == [
        {
            "actor": actor,
            "review_id": review_id,
            "status": service_status,
            "reason": (
                "Moderation decision."
            ),
        }
    ]

    operation = application.openapi()[
        "paths"
    ][
        (
            "/api/v1/admin/reviews/"
            "{review_id}/"
            f"{action}"
        )
    ]["post"]

    parameters = operation.get(
        "parameters",
        [],
    )
    assert all(
        parameter["name"] != "tenant_id"
        for parameter in parameters
    )
