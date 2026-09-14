from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_neutral_favorite_list_uses_actor_scope():
    from types import SimpleNamespace

    from services.user_favorites import (
        UserFavoritesPage,
        UserFavoritesService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    cards = [
        SimpleNamespace(
            professional_cabinet_id=uuid4(),
        ),
    ]
    calls = []

    class FakeFavorites:
        async def list_public_cards_page(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                cards=cards,
                page=0,
                has_next=False,
            )

    service = UserFavoritesService(
        object(),
        settings=object(),
        favorites=FakeFavorites(),
    )

    page = await service.list_favorites_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        language="uk",
        page=0,
        page_size=20,
    )

    assert isinstance(page, UserFavoritesPage)
    assert page.actor.user_id == user_id
    assert page.actor.tenant_id == tenant_id
    assert page.actor.language == "uk"
    assert page.cards == cards
    assert page.page == 0
    assert page.has_next is False

    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "page": 0,
            "page_size": 20,
            "language": "uk",
            "platform": "api",
        }
    ]


@pytest.mark.asyncio
async def test_me_favorites_endpoint_uses_current_actor():
    from types import SimpleNamespace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_favorites_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    city_id = uuid4()
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

    card = SimpleNamespace(
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
        display_name="Test Specialist",
        short_description="Consultations",
        city_id=city_id,
        city_name="Kyiv",
        category_name="Consulting",
        profession_name="Consultant",
        work_format="online",
        service_titles=["Consultation"],
        skill_names=["Planning"],
        languages=["uk", "en"],
        rating=4.8,
        reviews_count=12,
        is_verified=True,
        is_available=True,
        is_premium=False,
        moderation_status="approved",
    )

    class FakeUserFavoritesService:
        async def list_favorites_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                cards=[card],
                page=0,
                has_next=False,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_favorites_service
    ] = lambda: FakeUserFavoritesService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me/favorites",
            params={
                "limit": 20,
            },
            headers={
                "X-Request-ID": "me-favorites",
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
                "specialist_id": str(
                    specialist_id
                ),
                "professional_cabinet_id": str(
                    cabinet_id
                ),
                "display_name": "Test Specialist",
                "short_description": "Consultations",
                "city_id": str(city_id),
                "city_name": "Kyiv",
                "category_name": "Consulting",
                "profession_name": "Consultant",
                "work_format": "online",
                "services": ["Consultation"],
                "skills": ["Planning"],
                "languages": ["uk", "en"],
                "rating": 4.8,
                "reviews_count": 12,
                "is_verified": True,
                "is_available": True,
                "is_premium": False,
            }
        ],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": "me-favorites",
    }

    assert "moderation_status" not in response.text
    assert "tenant_id" not in response.text


@pytest.mark.asyncio
async def test_me_favorites_requires_authentication():
    import httpx

    from api.app import create_app

    request_id = "favorites-auth-required"
    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/me/favorites",
            headers={
                "X-Request-ID": request_id,
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


@pytest.mark.asyncio
async def test_neutral_favorite_save_uses_actor_scope():
    from services.user_favorites import (
        UserFavoritesAction,
        UserFavoritesService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    class FakeFavorites:
        async def save_professional_cabinet(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return True

    service = UserFavoritesService(
        object(),
        settings=object(),
        favorites=FakeFavorites(),
    )

    action = await service.save_favorite_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        professional_cabinet_id=cabinet_id,
    )

    assert isinstance(
        action,
        UserFavoritesAction,
    )
    assert action.actor.tenant_id == tenant_id
    assert action.actor.user_id == user_id
    assert action.result is True

    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
        }
    ]


@pytest.mark.asyncio
async def test_me_favorite_save_endpoint_uses_current_actor():
    from types import SimpleNamespace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_favorites_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    cabinet_id = uuid4()
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

    class FakeUserFavoritesService:
        async def save_favorite_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=True,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_favorites_service
    ] = lambda: FakeUserFavoritesService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/me/favorites",
            headers={
                "X-Request-ID": (
                    "favorite-save"
                ),
            },
            json={
                "professional_cabinet_id": str(
                    cabinet_id
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
        }
    ]

    assert response.json() == {
        "data": {
            "professional_cabinet_id": str(
                cabinet_id
            ),
            "is_favorite": True,
            "created": True,
        },
        "meta": {},
        "request_id": "favorite-save",
    }


@pytest.mark.asyncio
async def test_favorite_save_rejects_actor_fields():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_favorites_service,
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
        async def save_favorite_for_user(
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
        get_user_favorites_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/me/favorites",
            headers={
                "X-Request-ID": (
                    "favorite-actor-fields"
                ),
            },
            json={
                "professional_cabinet_id": str(
                    uuid4()
                ),
                "user_id": str(uuid4()),
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
                "favorite-actor-fields"
            ),
        },
    }


@pytest.mark.asyncio
async def test_neutral_favorite_save_hides_unavailable_cabinet():
    from services.user_favorites import (
        UserFavoritesNotFoundError,
        UserFavoritesService,
    )

    class FakeFavorites:
        async def save_professional_cabinet(
            self,
            **kwargs,
        ):
            raise ValueError(
                "Internal cabinet details."
            )

    service = UserFavoritesService(
        object(),
        settings=object(),
        favorites=FakeFavorites(),
    )

    with pytest.raises(
        UserFavoritesNotFoundError,
        match="Favorite target is not available.",
    ):
        await service.save_favorite_for_user(
            tenant_id=uuid4(),
            user_id=uuid4(),
            professional_cabinet_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_favorite_save_hides_missing_or_foreign_cabinet():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_favorites_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.user_favorites import (
        UserFavoritesNotFoundError,
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

    class MissingFavoriteService:
        async def save_favorite_for_user(
            self,
            **kwargs,
        ):
            raise UserFavoritesNotFoundError(
                "Private repository details."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_favorites_service
    ] = lambda: MissingFavoriteService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/me/favorites",
            headers={
                "X-Request-ID": (
                    "favorite-not-found"
                ),
            },
            json={
                "professional_cabinet_id": str(
                    uuid4()
                ),
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "favorite_not_found",
            "message": (
                "Favorite target is not available."
            ),
            "request_id": (
                "favorite-not-found"
            ),
        },
    }

    assert (
        "Private repository details."
        not in response.text
    )

