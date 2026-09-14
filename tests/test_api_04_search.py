import ast
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_neutral_specialist_search_uses_actor_scope():
    from services.user_search import (
        UserSearchService,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    expected = SimpleNamespace(
        visible_results=(),
    )
    calls = []

    service = UserSearchService(
        object(),
        settings=object(),
        repository=object(),
        selection=object(),
        text_search=object(),
        search_repository=object(),
        geo_search=object(),
        favorite_repository=object(),
        favorites=object(),
    )

    async def fake_search_results_for_actor(
        **kwargs,
    ):
        calls.append(kwargs)
        return expected

    service._search_results_for_actor = (
        fake_search_results_for_actor
    )

    result = await (
        service.search_specialists_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            data={
                "available_only": True,
            },
            page=0,
            page_size=20,
            default_radius_km=25,
        )
    )

    assert result is expected
    assert len(calls) == 1

    call = calls[0]
    actor = call.pop("actor")

    assert actor.user_id == user_id
    assert actor.tenant_id == tenant_id
    assert actor.language == "uk"

    assert call == {
        "platform_user_id": None,
        "platform": "api",
        "data": {
            "available_only": True,
        },
        "page": 0,
        "page_size": 20,
        "default_radius_km": 25,
    }


def test_neutral_specialist_search_has_no_transport_id():
    from pathlib import Path

    source = Path(
        "services/user_search.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "UserSearchService"
    )
    method = next(
        node
        for node in service.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "search_specialists_for_user"
    )

    arguments = {
        argument.arg
        for argument in (
            list(method.args.args)
            + list(method.args.kwonlyargs)
        )
    }

    assert "platform_user_id" not in arguments
    assert "telegram_id" not in arguments


@pytest.mark.asyncio
async def test_specialist_search_endpoint_uses_current_actor():
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_search_service,
    )
    from api.pagination import (
        encode_page_cursor,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    category_id = uuid4()
    profession_id = uuid4()
    country_id = uuid4()
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

    item = SimpleNamespace(
        specialist=SimpleNamespace(
            id=specialist_id,
            display_name="Test Specialist",
            experience_years=7,
            is_verified=True,
        ),
        professional_cabinet=SimpleNamespace(
            id=cabinet_id,
            title="Plumbing",
            description="Safe public description.",
            category_id=category_id,
            profession_id=profession_id,
            country_id=country_id,
            city_id=city_id,
            work_format="at_client",
            availability_status="available",
        ),
        city_name="Kyiv",
        category_name="Home services",
        profession_name="Plumber",
        languages=["uk", "en"],
        rating=4.8,
        reviews_count=12,
        is_premium=True,
        distance_km=3.2,
    )

    class FakeSearchService:
        async def search_specialists_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                visible_results=(item,),
                total_count=3,
                has_next=True,
                saved_professional_cabinet_ids=(
                    frozenset({cabinet_id})
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_search_service
    ] = lambda: FakeSearchService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/search/specialists",
            params={
                "q": "plumber",
                "available_only": "true",
                "limit": 2,
            },
            headers={
                "X-Request-ID": "search-request",
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "data": {
                "search_text_query": "plumber",
                "available_only": True,
            },
            "page": 0,
            "page_size": 2,
            "default_radius_km": 25,
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
                "title": "Plumbing",
                "short_description": (
                    "Safe public description."
                ),
                "experience_years": 7,
                "category_id": str(category_id),
                "category_name": "Home services",
                "profession_id": str(profession_id),
                "profession_name": "Plumber",
                "country_id": str(country_id),
                "city_id": str(city_id),
                "city_name": "Kyiv",
                "work_format": "at_client",
                "languages": ["uk", "en"],
                "rating": 4.8,
                "reviews_count": 12,
                "is_verified": True,
                "is_available": True,
                "is_premium": True,
                "distance_km": 3.2,
                "is_favorite": True,
            }
        ],
        "meta": {
            "next_cursor": (
                encode_page_cursor(1)
            ),
            "has_more": True,
            "total_count": 3,
        },
        "request_id": "search-request",
    }


@pytest.mark.asyncio
async def test_specialist_search_endpoint_maps_existing_filters():
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_search_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    category_id = uuid4()
    profession_id = uuid4()
    country_id = uuid4()
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

    class FakeSearchService:
        async def search_specialists_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                visible_results=(),
                total_count=0,
                has_next=False,
                saved_professional_cabinet_ids=(
                    frozenset()
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_search_service
    ] = lambda: FakeSearchService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/search/specialists",
            params={
                "q": "  plumber  ",
                "category_id": str(category_id),
                "profession_id": str(
                    profession_id
                ),
                "country_id": str(country_id),
                "latitude": 50.45,
                "longitude": 30.52,
                "radius_km": 20,
                "country_wide": "true",
                "language_code": "uk",
                "verified_only": "true",
                "available_only": "true",
                "premium_only": "true",
                "work_format": "at_client",
                "rating_min": 4,
                "sort_by": "relevance",
                "limit": 20,
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "data": {
                "search_text_query": "plumber",
                "category_id": str(category_id),
                "profession_id": str(
                    profession_id
                ),
                "country_id": str(country_id),
                "latitude": 50.45,
                "longitude": 30.52,
                "radius_km": 20.0,
                "country_wide": True,
                "language_code": "uk",
                "verified_only": True,
                "available_only": True,
                "premium_only": True,
                "work_format": "at_client",
                "rating_min": 4.0,
                "sort_by": "relevance",
            },
            "page": 0,
            "page_size": 20,
            "default_radius_km": 25,
        }
    ]

    assert response.json()["data"] == []
    assert response.json()["meta"] == {
        "next_cursor": None,
        "has_more": False,
        "total_count": 0,
    }


@pytest.mark.asyncio
async def test_specialist_search_rejects_partial_coordinates():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_search_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    request_id = "search-partial-geo"

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
        async def search_specialists_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Search service must not be called "
                "with partial coordinates."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_search_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/search/specialists",
            params={
                "latitude": 50.45,
            },
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "search_validation_error",
            "message": (
                "Search filters are not valid."
            ),
            "request_id": request_id,
        },
    }


@pytest.mark.asyncio
async def test_specialist_search_repository_is_tenant_and_visibility_scoped():
    from sqlalchemy.dialects import postgresql

    from database.repositories.search import (
        SpecialistSearchFilters,
        SpecialistSearchRepository,
    )

    tenant_id = uuid4()

    class FakeTuples:
        def all(self):
            return []

    class FakeResult:
        def tuples(self):
            return FakeTuples()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = SpecialistSearchRepository(
        session
    )

    result = await (
        repository.search_professional_cabinets(
            SpecialistSearchFilters(
                limit=20,
                offset=0,
                sort_by="relevance",
            ),
            tenant_id=tenant_id,
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
        f"specialists.tenant_id = "
        f"'{tenant_id}'"
        in sql
    )
    assert (
        f"professional_cabinets.tenant_id = "
        f"'{tenant_id}'"
        in sql
    )
    assert (
        "professional_cabinets.is_active "
        "is true"
        in sql
    )
    assert (
        "professional_cabinets."
        "moderation_status in "
        "('approved', 'pending_moderation')"
        in sql
    )
    assert "specialists.status != 'deleted'" in sql
    assert (
        "users.status not in "
        "('blocked', 'deleted')"
        in sql
    )


@pytest.mark.asyncio
async def test_specialist_search_requires_authentication():
    import httpx

    from api.app import create_app

    request_id = "search-auth-required"
    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/search/specialists",
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


def test_specialist_search_openapi_has_safe_typed_response():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        "/api/v1/search/specialists"
    ]["get"]

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )

    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistSearchResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistSearchResponse"
        ]["properties"]
    ) == {
        "data",
        "meta",
        "request_id",
    }

    item_properties = set(
        components[
            "SpecialistSearchItem"
        ]["properties"]
    )

    assert item_properties == {
        "specialist_id",
        "professional_cabinet_id",
        "display_name",
        "title",
        "short_description",
        "experience_years",
        "category_id",
        "category_name",
        "profession_id",
        "profession_name",
        "country_id",
        "city_id",
        "city_name",
        "work_format",
        "languages",
        "rating",
        "reviews_count",
        "is_verified",
        "is_available",
        "is_premium",
        "distance_km",
        "is_favorite",
    }

    private_fields = {
        "tenant_id",
        "user_id",
        "phone",
        "email",
        "moderation_status",
        "priority_score",
        "internal_notes",
        "billing",
    }

    assert item_properties.isdisjoint(
        private_fields
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "error_type",
        "expected_status",
        "expected_code",
        "expected_message",
    ),
    [
        (
            "validation",
            422,
            "search_validation_error",
            "Search filters are not valid.",
        ),
        (
            "access",
            403,
            "search_access_denied",
            "Search access denied.",
        ),
    ],
)
async def test_specialist_search_maps_service_errors(
    error_type,
    expected_status,
    expected_code,
    expected_message,
):
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_user_search_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.user_search import (
        UserSearchAccessError,
        UserSearchQueryError,
    )

    request_id = (
        f"search-{error_type}-error"
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

    class FailingSearchService:
        async def search_specialists_for_user(
            self,
            **kwargs,
        ):
            if error_type == "validation":
                raise UserSearchQueryError(
                    "PRIVATE SEARCH DETAILS"
                )

            raise UserSearchAccessError(
                "PRIVATE TENANT DETAILS"
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_user_search_service
    ] = lambda: FailingSearchService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/search/specialists",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == (
        expected_status
    )
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": expected_message,
            "request_id": request_id,
        },
    }

    assert "PRIVATE" not in response.text


@pytest.mark.asyncio
async def test_search_result_audit_accepts_neutral_platform():
    from types import SimpleNamespace

    from services.geo_search import (
        GeoSearchService,
        SearchResultsViewedEvent,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    calls = []

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeEvents:
        async def create_event(self, **kwargs):
            calls.append(kwargs)

    session = FakeSession()
    service = GeoSearchService(
        SimpleNamespace(session=session)
    )
    service.events = FakeEvents()

    await service.record_results_viewed(
        tenant_id=tenant_id,
        user_id=user_id,
        event=SearchResultsViewedEvent(
            platform_user_id=None,
            page=0,
            visible_count=2,
            has_next=False,
            category_id=None,
            profession_id=None,
            city_id=None,
            location_state=None,
            radius_km=25,
            country_wide=False,
            sort_by="relevance",
            category_name=None,
            profession_name=None,
            city_name=None,
            search_text_query="plumber",
        ),
        platform="api",
    )

    assert len(calls) == 1
    assert calls[0]["tenant_id"] == tenant_id
    assert calls[0]["user_id"] == user_id
    assert calls[0]["platform"] == "api"

    payload = calls[0]["payload"]
    assert payload["platform_user_id"] is None
    assert "telegram_id" not in payload

    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_empty_search_audit_accepts_neutral_platform():
    from types import SimpleNamespace

    from services.geo_search import (
        EmptySearchEvent,
        GeoSearchService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    calls = []

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeEvents:
        async def create_event(self, **kwargs):
            calls.append(kwargs)

    session = FakeSession()
    service = GeoSearchService(
        SimpleNamespace(session=session)
    )
    service.events = FakeEvents()

    await service.record_empty_search(
        tenant_id=tenant_id,
        user_id=user_id,
        event=EmptySearchEvent(
            page=0,
            category_id=None,
            profession_id=None,
            city_id=None,
            location_state=None,
            radius_km=25,
            country_wide=False,
            language_code="uk",
            work_format=None,
        ),
        platform="api",
    )

    assert len(calls) == 1
    assert calls[0]["tenant_id"] == tenant_id
    assert calls[0]["user_id"] == user_id
    assert calls[0]["platform"] == "api"
    assert session.commits == 1
    assert session.rollbacks == 0

