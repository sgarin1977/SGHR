from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_search import (
    UserSearchActor,
)
from services.user_search_location import (
    UserSearchLocationError,
    UserSearchLocationService,
)


class FakeActorResolver:
    def __init__(self, actor):
        self.actor = actor
        self.calls = []

    async def resolve_actor(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return self.actor


class FakeGeo:
    def __init__(self):
        self.calls = []
        self.candidates = ["candidate"]
        self.place = SimpleNamespace(
            city_id=uuid4(),
            country_id=uuid4(),
        )

    async def search_places(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("search", kwargs)
        )
        return self.candidates

    async def nearby_places(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("nearby", kwargs)
        )
        return self.candidates

    async def confirm_search_place(
        self,
        candidate,
        **kwargs,
    ):
        self.calls.append(
            (
                "confirm",
                candidate,
                kwargs,
            )
        )
        return self.place


def actor(*, registered=True):
    return UserSearchActor(
        user_id=(
            uuid4()
            if registered
            else None
        ),
        tenant_id=(
            uuid4()
            if registered
            else None
        ),
        language="uk",
    )


def build_service(current_actor):
    search = FakeActorResolver(
        current_actor
    )
    geo = FakeGeo()
    service = UserSearchLocationService(
        object(),
        search=search,
        repository=object(),
        geo=geo,
    )
    return service, search, geo


@pytest.mark.asyncio
async def test_location_query_uses_actor_language():
    current = actor()
    service, search, geo = build_service(
        current
    )

    action = await service.search_places(
        platform_user_id=123,
        query="  Kyiv  ",
        fallback_language="en",
        limit=8,
    )

    assert action.actor is current
    assert action.result == geo.candidates
    assert search.calls == [
        {
            "platform_user_id": 123,
            "fallback_language": "en",
        }
    ]
    assert geo.calls == [
        (
            "search",
            {
                "query": "Kyiv",
                "language": "uk",
                "limit": 8,
            },
        )
    ]


@pytest.mark.asyncio
async def test_short_location_query_fails_closed():
    service, search, geo = build_service(
        actor()
    )

    with pytest.raises(
        UserSearchLocationError,
        match="too short",
    ):
        await service.search_places(
            platform_user_id=123,
            query=" x ",
        )

    assert search.calls == []
    assert geo.calls == []


@pytest.mark.asyncio
async def test_nearby_places_parse_coordinates():
    current = actor()
    service, _, geo = build_service(
        current
    )

    action = await service.nearby_places(
        platform_user_id=123,
        latitude="50.45",
        longitude="30.52",
        fallback_language="en",
        limit=4,
    )

    assert action.actor is current
    assert geo.calls == [
        (
            "nearby",
            {
                "latitude": 50.45,
                "longitude": 30.52,
                "language": "uk",
                "limit": 4,
            },
        )
    ]


@pytest.mark.asyncio
async def test_invalid_coordinates_fail_closed():
    service, _, geo = build_service(
        actor()
    )

    with pytest.raises(
        UserSearchLocationError,
        match="Invalid latitude",
    ):
        await service.nearby_places(
            platform_user_id=123,
            latitude="invalid",
            longitude=30.52,
        )

    assert geo.calls == []


@pytest.mark.asyncio
async def test_confirm_place_uses_actor_scope():
    current = actor()
    service, _, geo = build_service(
        current
    )
    candidate = {
        "name": "Kyiv",
    }

    action = await service.confirm_place(
        platform_user_id=123,
        candidate=candidate,
        fallback_language="en",
        source="search_filter",
    )

    assert action.actor is current
    assert action.result is geo.place
    assert geo.calls == [
        (
            "confirm",
            candidate,
            {
                "tenant_id": current.tenant_id,
                "user_id": current.user_id,
                "language": "uk",
                "source": "search_filter",
            },
        )
    ]


@pytest.mark.asyncio
async def test_public_place_confirmation_has_no_actor_scope():
    current = actor(
        registered=False
    )
    service, _, geo = build_service(
        current
    )

    await service.confirm_place(
        platform_user_id=456,
        candidate={"name": "Kyiv"},
        fallback_language="en",
    )

    operation, _, kwargs = geo.calls[0]

    assert operation == "confirm"
    assert kwargs["tenant_id"] is None
    assert kwargs["user_id"] is None
    assert kwargs["language"] == "uk"



def test_location_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "receive_location_query": (
            "search_places"
        ),
        "receive_geo": "nearby_places",
        "choose_search_geo_place": (
            "confirm_place"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "UserSearchLocationService"
            in called_names
        )
        assert service_method in called_methods
        assert not (
            called_names
            & {
                "GeoRepository",
                "GeoService",
                "get_requester_context",
            }
        )

    receive_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "receive_location_query"
    )
    receive_block = ast.get_source_segment(
        source,
        receive_node,
    ) or ""

    assert "len(query) < 2" not in receive_block
    assert ").strip()" not in receive_block

    choose_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "choose_search_geo_place"
    )
    choose_block = ast.get_source_segment(
        source,
        choose_node,
    ) or ""

    assert "index < 0" in choose_block
