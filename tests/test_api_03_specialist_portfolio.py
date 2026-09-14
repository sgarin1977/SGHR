from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.specialist_portfolio import (
    SpecialistPortfolioService,
)


class FakePortfolioSpecialists:
    def __init__(self, specialist):
        self.specialist = specialist
        self.user_ids = []

    async def get_by_user_id(self, user_id):
        self.user_ids.append(user_id)
        return self.specialist


@pytest.mark.asyncio
async def test_neutral_portfolio_actor_uses_user_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()

    specialists = FakePortfolioSpecialists(
        SimpleNamespace(
            id=specialist_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
    )

    service = SpecialistPortfolioService(
        object(),
        users=object(),
        translations=object(),
        specialists=specialists,
        portfolio=object(),
    )

    actor = await service.require_user_actor(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
    )

    assert specialists.user_ids == [user_id]
    assert actor.user_id == user_id
    assert actor.tenant_id == tenant_id
    assert actor.specialist_id == specialist_id
    assert actor.language == "uk"

@pytest.mark.asyncio
async def test_neutral_portfolio_list_uses_owned_cabinet_scope():
    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    items = (
        SimpleNamespace(id=uuid4()),
    )
    calls = []

    class FakeSpecialists:
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
            calls.append(
                ("cabinet", kwargs)
            )
            return (
                SimpleNamespace(
                    id=cabinet_id,
                    specialist_id=specialist_id,
                    tenant_id=tenant_id,
                ),
                SimpleNamespace(id=uuid4()),
            )

    class FakePortfolio:
        async def list_active_items_for_viewer(
            self,
            **kwargs,
        ):
            calls.append(
                ("portfolio", kwargs)
            )
            return items

    service = SpecialistPortfolioService(
        object(),
        users=object(),
        translations=object(),
        specialists=FakeSpecialists(),
        portfolio=FakePortfolio(),
    )

    result = await service.list_portfolio_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        page=0,
        platform="api",
    )

    assert result.actor.user_id == user_id
    assert result.actor.tenant_id == tenant_id
    assert (
        result.actor.specialist_id
        == specialist_id
    )
    assert result.result is items

    assert calls == [
        (
            "cabinet",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "portfolio",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "viewer_user_id": user_id,
                "page": 0,
                "platform": "api",
            },
        ),
    ]

@pytest.mark.asyncio
async def test_neutral_portfolio_list_rejects_foreign_cabinet():
    from services.specialist_portfolio import (
        SpecialistPortfolioAccessError,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    portfolio_calls = []

    class FakeSpecialists:
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
            return None

    class ForbiddenPortfolio:
        async def list_active_items_for_viewer(
            self,
            **kwargs,
        ):
            portfolio_calls.append(kwargs)
            raise AssertionError(
                "Foreign cabinet portfolio "
                "must not be queried."
            )

    service = SpecialistPortfolioService(
        object(),
        users=object(),
        translations=object(),
        specialists=FakeSpecialists(),
        portfolio=ForbiddenPortfolio(),
    )

    with pytest.raises(
        SpecialistPortfolioAccessError
    ):
        await service.list_portfolio_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=uuid4(),
            page=0,
            platform="api",
        )

    assert portfolio_calls == []

@pytest.mark.asyncio
async def test_specialist_cabinet_portfolio_uses_current_actor():
    from datetime import (
        UTC,
        datetime,
    )

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_portfolio_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    item_id = uuid4()
    request_id = "specialist-portfolio-list"
    created_at = datetime(
        2026,
        8,
        29,
        10,
        30,
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
            "specialist.portfolio.read",
        ),
    )

    view = SimpleNamespace(
        item=SimpleNamespace(
            id=item_id,
            title="Completed project",
            description="Project description",
            status="active",
            created_at=created_at,
        ),
        storage_object=SimpleNamespace(
            file_type="photo",
            mime_type="image/jpeg",
            size_bytes=1024,
            storage_path=(
                "private/internal/path.jpg"
            ),
            owner_user_id=user_id,
        ),
        signed_url=(
            "https://storage.example.com/signed"
        ),
    )

    class FakeSpecialistPortfolio:
        async def list_portfolio_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=(view,),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_portfolio_service
    ] = lambda: FakeSpecialistPortfolio()

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
                f"{cabinet_id}/portfolio"
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
            "platform": "api",
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(item_id),
                "title": "Completed project",
                "description": (
                    "Project description"
                ),
                "file_type": "photo",
                "mime_type": "image/jpeg",
                "size_bytes": 1024,
                "url": (
                    "https://storage.example.com/"
                    "signed"
                ),
                "status": "active",
                "created_at": (
                    "2026-08-29T10:30:00Z"
                ),
            }
        ],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": request_id,
    }

    assert "storage_path" not in response.text
    assert "owner_user_id" not in response.text

@pytest.mark.asyncio
async def test_specialist_cabinet_portfolio_requires_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_portfolio_service,
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

    class ForbiddenPortfolio:
        async def list_portfolio_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "Portfolio service must not "
                "be called without permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_portfolio_service
    ] = lambda: ForbiddenPortfolio()

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
                f"{uuid4()}/portfolio"
            )
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []

@pytest.mark.asyncio
async def test_specialist_cabinet_portfolio_requires_authentication():
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
                f"{uuid4()}/portfolio"
            )
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )

@pytest.mark.asyncio
async def test_specialist_portfolio_hides_foreign_cabinet_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_portfolio_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_portfolio import (
        SpecialistPortfolioAccessError,
    )

    private_details = (
        "private foreign tenant portfolio details"
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
            "specialist.portfolio.read",
        ),
    )

    class ForeignPortfolio:
        async def list_portfolio_for_user(
            self,
            **kwargs,
        ):
            raise SpecialistPortfolioAccessError(
                private_details
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_portfolio_service
    ] = lambda: ForeignPortfolio()

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
                f"{uuid4()}/portfolio"
            )
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == (
        "cabinet_not_found"
    )
    assert private_details not in response.text

@pytest.mark.asyncio
async def test_specialist_portfolio_uses_cursor_pagination():
    from datetime import (
        UTC,
        datetime,
    )

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_portfolio_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    calls = []
    cabinet_id = uuid4()

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "specialist.portfolio.read",
        ),
    )

    views = tuple(
        SimpleNamespace(
            item=SimpleNamespace(
                id=uuid4(),
                title=f"Portfolio {index}",
                description=None,
                status="active",
                created_at=datetime(
                    2026,
                    8,
                    29,
                    12,
                    0,
                    tzinfo=UTC,
                ),
            ),
            storage_object=SimpleNamespace(
                file_type="photo",
                mime_type="image/jpeg",
                size_bytes=100,
            ),
            signed_url=(
                "https://storage.example.com/signed"
            ),
        )
        for index in range(51)
    )

    class FakePortfolio:
        async def list_portfolio_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                result=views,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_portfolio_service
    ] = lambda: FakePortfolio()

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
                f"{cabinet_id}/portfolio"
                "?limit=25&cursor=MQ"
            )
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": actor.user_id,
            "tenant_id": actor.tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "page": 1,
            "platform": "api",
        }
    ]

    body = response.json()

    assert len(body["data"]) == 25
    assert body["meta"] == {
        "next_cursor": "Mg",
        "has_more": True,
    }

def test_specialist_portfolio_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/portfolio"
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
            "SpecialistPortfolioListResponse"
        )
    }

    properties = schema[
        "components"
    ]["schemas"][
        "SpecialistPortfolioItem"
    ]["properties"]

    assert "storage_path" not in properties
    assert "owner_user_id" not in properties
    assert "tenant_id" not in properties
