from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_search import (
    UserSearchAccessError,
    UserSearchFilters,
    UserSearchPage,
    UserSearchQueryError,
    UserSearchSelectionError,
    UserSearchService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
)


class FakeSettings:
    def __init__(
        self,
        context=None,
        error=None,
    ):
        self.context = context
        self.error = error
        self.calls = []

    async def get_context(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return self.context


class FakeSelection:
    def __init__(self):
        self.calls = []

    async def list_active_categories(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("categories", kwargs)
        )
        return ["category"]

    async def list_profession_options(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("professions", kwargs)
        )
        return ["profession"]

    async def select_category(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("select_category", kwargs)
        )
        return "selected-category"

    async def select_profession(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("select_profession", kwargs)
        )
        return "selected-profession"


def context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


def build_service(
    *,
    current_context=None,
    settings_error=None,
):
    settings = FakeSettings(
        current_context,
        settings_error,
    )
    selection = FakeSelection()
    service = UserSearchService(
        object(),
        settings=settings,
        repository=object(),
        selection=selection,
    )
    return service, settings, selection


@pytest.mark.asyncio
async def test_registered_search_actor():
    current = context()
    service, settings, _ = build_service(
        current_context=current
    )

    actor = await service.resolve_actor(
        platform_user_id=123,
        fallback_language="en",
    )

    assert actor.user_id == current.user_id
    assert (
        actor.tenant_id
        == current.tenant_id
    )
    assert actor.language == "uk"
    assert settings.calls == [
        {
            "platform_user_id": 123,
        }
    ]


@pytest.mark.asyncio
async def test_public_search_actor_falls_back():
    service, _, _ = build_service(
        settings_error=(
            UserSettingsNotFoundError()
        )
    )

    actor = await service.resolve_actor(
        platform_user_id=456,
        fallback_language="en",
    )

    assert actor.user_id is None
    assert actor.tenant_id is None
    assert actor.language == "en"


@pytest.mark.asyncio
async def test_list_categories_uses_actor_language():
    current = context()
    (
        service,
        _,
        selection,
    ) = build_service(
        current_context=current
    )

    result = await service.list_categories(
        platform_user_id=123,
        fallback_language="en",
        limit=25,
    )

    assert result.actor.language == "uk"
    assert result.result == [
        "category"
    ]
    assert selection.calls == [
        (
            "categories",
            {
                "language": "uk",
                "limit": 25,
            },
        )
    ]


@pytest.mark.asyncio
async def test_list_professions_parses_category():
    (
        service,
        _,
        selection,
    ) = build_service(
        settings_error=(
            UserSettingsNotFoundError()
        )
    )
    category_id = uuid4()

    result = await service.list_professions(
        platform_user_id=None,
        category_id=str(category_id),
        fallback_language="en",
    )

    assert result.result == [
        "profession"
    ]
    assert selection.calls == [
        (
            "professions",
            {
                "category_id": (
                    category_id
                ),
                "language": "en",
                "limit": 100,
            },
        )
    ]


@pytest.mark.asyncio
async def test_select_category_uses_actor_scope():
    current = context()
    (
        service,
        _,
        selection,
    ) = build_service(
        current_context=current
    )
    category_id = uuid4()

    result = await service.select_category(
        platform_user_id=123,
        category_id=str(category_id),
        fallback_language="en",
    )

    assert (
        result.result
        == "selected-category"
    )
    assert selection.calls == [
        (
            "select_category",
            {
                "category_id": (
                    category_id
                ),
                "language": "uk",
                "tenant_id": (
                    current.tenant_id
                ),
                "user_id": (
                    current.user_id
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_select_profession_uses_actor_scope():
    current = context()
    (
        service,
        _,
        selection,
    ) = build_service(
        current_context=current
    )
    category_id = uuid4()
    profession_id = uuid4()

    result = await service.select_profession(
        platform_user_id=123,
        profession_id=(
            str(profession_id)
        ),
        category_id=str(category_id),
        fallback_language="en",
    )

    assert (
        result.result
        == "selected-profession"
    )
    assert selection.calls == [
        (
            "select_profession",
            {
                "profession_id": (
                    profession_id
                ),
                "category_id": (
                    category_id
                ),
                "language": "uk",
                "tenant_id": (
                    current.tenant_id
                ),
                "user_id": (
                    current.user_id
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_invalid_search_selection_fails_closed():
    (
        service,
        _,
        selection,
    ) = build_service(
        settings_error=(
            UserSettingsNotFoundError()
        )
    )

    with pytest.raises(
        UserSearchSelectionError,
        match="Invalid profession",
    ):
        await service.select_profession(
            platform_user_id=None,
            profession_id="invalid",
            category_id=None,
        )

    assert selection.calls == []



def test_search_read_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "load_search_profession_options": (
            "list_professions"
        ),
        "open_category_filter": (
            "list_categories"
        ),
        "paginate_categories": (
            "list_categories"
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
            "UserSearchService"
            in called_names
        )
        assert service_method in called_methods
        assert not (
            called_names
            & {
                "SpecialistRepository",
                (
                    "SpecialistSearch"
                    "SelectionService"
                ),
            }
        )



def test_search_selection_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "choose_category": (
            "select_category"
        ),
        "toggle_profession": (
            "select_profession"
        ),
        "choose_profession": (
            "select_profession"
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
        caught_errors = {
            handler.type.id
            for handler in ast.walk(node)
            if isinstance(
                handler,
                ast.ExceptHandler,
            )
            and isinstance(
                handler.type,
                ast.Name,
            )
        }

        assert (
            "UserSearchService"
            in called_names
        )
        assert service_method in called_methods
        assert (
            "UserSearchSelectionError"
            in caught_errors
        )
        assert not (
            called_names
            & {
                "UUID",
                "SpecialistRepository",
                (
                    "SpecialistSearch"
                    "SelectionService"
                ),
                "get_requester_context",
            }
        )

        block = ast.get_source_segment(
            source,
            node,
        ) or ""
        assert "index < 0" in block



class FakeGeoSearch:
    def __init__(self):
        self.calls = []
        self.history = [
            {"query": "psychologist"},
        ]

    async def record_search_opened(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("search_opened", kwargs)
        )

    async def list_recent_search_history(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("history", kwargs)
        )
        return self.history

    async def record_location_opened(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("location_opened", kwargs)
        )

    async def record_filter_changed(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("filter_changed", kwargs)
        )


def build_search_application(
    *,
    current_context=None,
    settings_error=None,
):
    settings = FakeSettings(
        current_context,
        settings_error,
    )
    selection = FakeSelection()
    geo_search = FakeGeoSearch()

    service = UserSearchService(
        object(),
        settings=settings,
        repository=object(),
        selection=selection,
        search_repository=object(),
        geo_search=geo_search,
    )

    return (
        service,
        settings,
        geo_search,
    )


@pytest.mark.asyncio
async def test_open_search_uses_actor_scope():
    current = context()
    service, _, geo_search = (
        build_search_application(
            current_context=current
        )
    )

    actor = await service.open_search(
        platform_user_id=123,
        fallback_language="en",
        source="search_menu",
    )

    assert actor.user_id == current.user_id
    assert actor.tenant_id == current.tenant_id
    assert actor.language == "uk"
    assert geo_search.calls == [
        (
            "search_opened",
            {
                "tenant_id": current.tenant_id,
                "user_id": current.user_id,
                "source": "search_menu",
            },
        )
    ]


@pytest.mark.asyncio
async def test_public_search_events_are_skipped():
    service, _, geo_search = (
        build_search_application(
            settings_error=(
                UserSettingsNotFoundError()
            )
        )
    )

    search_actor = await service.open_search(
        platform_user_id=456,
        fallback_language="en",
        source="search_menu",
    )
    location_actor = (
        await service.open_location_filter(
            platform_user_id=456,
            fallback_language="en",
        )
    )
    filter_actor = (
        await service.record_filter_changed(
            platform_user_id=456,
            fallback_language="en",
            filter_name="work_format",
            value="online",
        )
    )

    assert search_actor.user_id is None
    assert location_actor.user_id is None
    assert filter_actor.user_id is None
    assert geo_search.calls == []


@pytest.mark.asyncio
async def test_search_history_uses_actor_scope():
    current = context()
    service, _, geo_search = (
        build_search_application(
            current_context=current
        )
    )

    action = await service.list_history(
        platform_user_id=123,
        fallback_language="en",
        limit=5,
    )

    assert action.actor.user_id == current.user_id
    assert action.actor.language == "uk"
    assert action.result == geo_search.history
    assert geo_search.calls == [
        (
            "history",
            {
                "tenant_id": current.tenant_id,
                "user_id": current.user_id,
                "limit": 5,
            },
        )
    ]


@pytest.mark.asyncio
async def test_public_search_history_is_empty():
    service, _, geo_search = (
        build_search_application(
            settings_error=(
                UserSettingsNotFoundError()
            )
        )
    )

    action = await service.list_history(
        platform_user_id=456,
        fallback_language="de",
    )

    assert action.actor.user_id is None
    assert action.actor.tenant_id is None
    assert action.actor.language == "de"
    assert action.result == []
    assert geo_search.calls == []


@pytest.mark.asyncio
async def test_location_open_uses_actor_scope():
    current = context()
    service, _, geo_search = (
        build_search_application(
            current_context=current
        )
    )

    actor = await service.open_location_filter(
        platform_user_id=123,
        fallback_language="en",
        source="search_filter",
    )

    assert actor.user_id == current.user_id
    assert geo_search.calls == [
        (
            "location_opened",
            {
                "tenant_id": current.tenant_id,
                "user_id": current.user_id,
                "source": "search_filter",
            },
        )
    ]


@pytest.mark.asyncio
async def test_filter_change_uses_actor_scope():
    current = context()
    service, _, geo_search = (
        build_search_application(
            current_context=current
        )
    )

    actor = await service.record_filter_changed(
        platform_user_id=123,
        fallback_language="en",
        filter_name="radius",
        value=25,
    )

    assert actor.user_id == current.user_id
    assert geo_search.calls == [
        (
            "filter_changed",
            {
                "tenant_id": current.tenant_id,
                "user_id": current.user_id,
                "filter_name": "radius",
                "value": 25,
            },
        )
    ]



def test_search_core_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "start_search": "open_search",
        "show_search_history": (
            "list_history"
        ),
        "open_location_filter": (
            "open_location_filter"
        ),
        "log_search_filters_changed": (
            "record_filter_changed"
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
            "UserSearchService"
            in called_names
        )
        assert service_method in called_methods
        assert not (
            called_names
            & {
                "GeoSearchService",
                (
                    "SpecialistSearch"
                    "Repository"
                ),
                "get_requester_context",
                "get_interface_language",
            }
        )



class FakeTextSearch:
    def __init__(self):
        self.calls = []
        self.result = SimpleNamespace(
            parsed_query=SimpleNamespace(
                city_id=None,
                city_name=None,
                country_id=None,
                country_name=None,
            ),
            professions=("profession",),
        )

    async def search(
        self,
        query,
        **kwargs,
    ):
        self.calls.append(
            (query, kwargs)
        )
        return self.result


def build_text_search_application(
    *,
    current_context=None,
    settings_error=None,
):
    settings = FakeSettings(
        current_context,
        settings_error,
    )
    text_search = FakeTextSearch()

    service = UserSearchService(
        object(),
        settings=settings,
        repository=object(),
        selection=FakeSelection(),
        text_search=text_search,
        search_repository=object(),
        geo_search=FakeGeoSearch(),
    )

    return service, text_search


@pytest.mark.asyncio
async def test_text_search_uses_actor_language():
    current = context()
    service, text_search = (
        build_text_search_application(
            current_context=current
        )
    )

    action = await service.search_text(
        platform_user_id=123,
        query="  psychologist  ",
        fallback_language="en",
        limit=10,
    )

    assert action.actor.user_id == current.user_id
    assert action.actor.language == "uk"
    assert action.result is text_search.result
    assert text_search.calls == [
        (
            "psychologist",
            {
                "language": "uk",
                "limit": 10,
            },
        )
    ]


@pytest.mark.asyncio
async def test_short_text_search_fails_closed():
    service, text_search = (
        build_text_search_application(
            settings_error=(
                UserSettingsNotFoundError()
            )
        )
    )

    with pytest.raises(
        UserSearchQueryError,
        match="too short",
    ):
        await service.search_text(
            platform_user_id=456,
            query=" x ",
            fallback_language="en",
        )

    assert text_search.calls == []


@pytest.mark.asyncio
async def test_public_text_search_uses_fallback():
    service, text_search = (
        build_text_search_application(
            settings_error=(
                UserSettingsNotFoundError()
            )
        )
    )

    action = await service.search_text(
        platform_user_id=456,
        query="therapist",
        fallback_language="de",
    )

    assert action.actor.user_id is None
    assert action.actor.tenant_id is None
    assert action.actor.language == "de"
    assert action.result is text_search.result
    assert text_search.calls == [
        (
            "therapist",
            {
                "language": "de",
                "limit": 10,
            },
        )
    ]



def test_text_search_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "receive_text_search_query"
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
    caught_errors = {
        handler.type.id
        for handler in ast.walk(node)
        if isinstance(
            handler,
            ast.ExceptHandler,
        )
        and isinstance(
            handler.type,
            ast.Name,
        )
    }

    assert (
        "UserSearchService"
        in called_names
    )
    assert "search_text" in called_methods
    assert (
        "UserSearchQueryError"
        in caught_errors
    )
    assert not (
        called_names
        & {
            "SpecialistRepository",
            (
                "SpecialistSearch"
                "TextService"
            ),
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "len(query) < 2" not in block
    assert ").strip()" not in block



class FakeCardSearch:
    def __init__(self):
        self.calls = []
        self.card = SimpleNamespace(
            display_name="Specialist",
        )

    async def get_public_card_for_viewer(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("open", kwargs)
        )
        return self.card

    async def get_public_card(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("selected", kwargs)
        )
        return self.card


def build_card_search_application(
    *,
    current_context=None,
    settings_error=None,
):
    settings = FakeSettings(
        current_context,
        settings_error,
    )
    card_search = FakeCardSearch()

    service = UserSearchService(
        object(),
        settings=settings,
        repository=object(),
        selection=FakeSelection(),
        text_search=FakeTextSearch(),
        search_repository=object(),
        geo_search=card_search,
    )

    return service, card_search


@pytest.mark.asyncio
async def test_result_card_requires_actor():
    service, card_search = (
        build_card_search_application(
            settings_error=(
                UserSettingsNotFoundError()
            )
        )
    )

    with pytest.raises(
        UserSearchAccessError,
        match="Registered",
    ):
        await service.open_result_card(
            platform_user_id=456,
            specialist_id=uuid4(),
            professional_cabinet_id=uuid4(),
            results_page=0,
            result_index=0,
        )

    assert card_search.calls == []


@pytest.mark.asyncio
async def test_result_card_uses_actor_scope():
    current = context()
    service, card_search = (
        build_card_search_application(
            current_context=current
        )
    )
    specialist_id = uuid4()
    cabinet_id = uuid4()

    action = await service.open_result_card(
        platform_user_id=123,
        specialist_id=str(specialist_id),
        professional_cabinet_id=(
            str(cabinet_id)
        ),
        results_page="2",
        result_index="3",
        distance_km="4.5",
        fallback_language="en",
    )

    assert action.actor.language == "uk"
    assert action.result is card_search.card

    operation, kwargs = card_search.calls[0]
    event = kwargs.pop("event")

    assert operation == "open"
    assert kwargs == {
        "specialist_id": specialist_id,
        "professional_cabinet_id": (
            cabinet_id
        ),
        "viewer_user_id": current.user_id,
        "tenant_id": current.tenant_id,
        "language": "uk",
    }
    assert event.source == "search_results"
    assert event.results_page == 2
    assert event.result_index == 3
    assert event.distance_km == 4.5


@pytest.mark.asyncio
async def test_selected_card_uses_actor_scope():
    current = context()
    service, card_search = (
        build_card_search_application(
            current_context=current
        )
    )
    specialist_id = uuid4()
    cabinet_id = uuid4()

    action = await service.get_selected_card(
        platform_user_id=123,
        specialist_id=str(specialist_id),
        professional_cabinet_id=(
            str(cabinet_id)
        ),
        fallback_language="en",
    )

    assert action.result is card_search.card
    assert card_search.calls == [
        (
            "selected",
            {
                "tenant_id": current.tenant_id,
                "specialist_id": specialist_id,
                (
                    "professional_"
                    "cabinet_id"
                ): cabinet_id,
                "requester_user_id": (
                    current.user_id
                ),
                "language": "uk",
            },
        )
    ]


@pytest.mark.asyncio
async def test_invalid_card_id_fails_closed():
    current = context()
    service, card_search = (
        build_card_search_application(
            current_context=current
        )
    )

    with pytest.raises(
        UserSearchSelectionError,
        match="Invalid specialist",
    ):
        await service.get_selected_card(
            platform_user_id=123,
            specialist_id="invalid",
        )

    assert card_search.calls == []



def test_public_card_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "show_specialist_card": (
            "open_result_card"
        ),
        (
            "back_to_selected_"
            "specialist_card"
        ): "get_selected_card",
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
        caught = {
            handler.type.id
            for handler in ast.walk(node)
            if isinstance(
                handler,
                ast.ExceptHandler,
            )
            and isinstance(
                handler.type,
                ast.Name,
            )
        }

        assert (
            "UserSearchService"
            in called_names
        )
        assert service_method in called_methods
        assert (
            "UserSearchAccessError"
            in caught
        )
        assert (
            "UserSearchSelectionError"
            in caught
        )
        assert not (
            called_names
            & {
                "UUID",
                "GeoSearchService",
                (
                    "SpecialistSearch"
                    "Repository"
                ),
                "PublicCardViewEvent",
                "get_requester_context",
            }
        )



def test_search_filters_default_to_without_location():
    filters = UserSearchService.build_filters(
        {},
        default_radius_km=25,
    )

    assert isinstance(
        filters,
        UserSearchFilters,
    )
    assert filters.location_state == "without"
    assert filters.without_location is True
    assert filters.has_geo is False
    assert filters.radius_km == 25.0
    assert filters.sort_by == "distance"


def test_search_filters_parse_ids_and_flags():
    category_id = uuid4()
    profession_id = uuid4()
    second_profession_id = uuid4()
    city_id = uuid4()
    country_id = uuid4()

    filters = UserSearchService.build_filters(
        {
            "category_id": str(category_id),
            "profession_id": (
                str(profession_id)
            ),
            "selected_profession_ids": [
                str(profession_id),
                str(second_profession_id),
            ],
            "city_id": str(city_id),
            "country_id": str(country_id),
            "location_state": "city",
            "country_wide": True,
            "verified_only": True,
            "available_only": True,
            "premium_only": True,
            "work_format": "offline",
            "rating_min": "4.5",
            "sort_by": "rating",
        }
    )

    assert filters.category_id == category_id
    assert filters.profession_id == profession_id
    assert filters.profession_ids == (
        profession_id,
        second_profession_id,
    )
    assert filters.city_id == city_id
    assert filters.country_id == country_id
    assert filters.rating_min == 4.5
    assert filters.country_wide is True
    assert filters.verified_only is True
    assert filters.available_only is True
    assert filters.premium_only is True
    assert filters.sort_by == "rating"


def test_search_filters_detect_geo_and_remote():
    geo = UserSearchService.build_filters(
        {
            "latitude": "50.45",
            "longitude": "30.52",
            "radius_km": "35",
        }
    )
    remote = UserSearchService.build_filters(
        {
            "work_format": "remote",
        }
    )

    assert geo.has_geo is True
    assert geo.latitude == 50.45
    assert geo.longitude == 30.52
    assert geo.radius_km == 35.0
    assert remote.remote_only is True
    assert remote.without_location is True


def test_invalid_search_filters_fail_closed():
    with pytest.raises(
        UserSearchSelectionError,
        match="Invalid category",
    ):
        UserSearchService.build_filters(
            {
                "category_id": "invalid",
            }
        )

    with pytest.raises(
        UserSearchSelectionError,
        match="Invalid latitude",
    ):
        UserSearchService.build_filters(
            {
                "latitude": "invalid",
                "longitude": "30.52",
            }
        )



def search_result():
    cabinet = SimpleNamespace(
        id=uuid4(),
        profession_id=uuid4(),
    )
    return SimpleNamespace(
        specialist=SimpleNamespace(
            id=uuid4(),
        ),
        professional_cabinet=cabinet,
        distance_km=None,
    )


class FakeResultGeoSearch:
    def __init__(self):
        self.calls = []
        self.page_results = []
        self.total_results = []

    async def _search(
        self,
        method,
        kwargs,
    ):
        self.calls.append(
            (method, kwargs)
        )
        return (
            self.total_results
            if kwargs["limit"] == 200
            else self.page_results
        )

    async def search_without_location(
        self,
        **kwargs,
    ):
        return await self._search(
            "without",
            kwargs,
        )

    async def search_by_radius(
        self,
        **kwargs,
    ):
        return await self._search(
            "radius",
            kwargs,
        )

    async def search_by_city(
        self,
        **kwargs,
    ):
        return await self._search(
            "city",
            kwargs,
        )

    async def record_results_viewed(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("viewed", kwargs)
        )

    async def record_empty_search(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("empty", kwargs)
        )


class FakeResultFavorites:
    def __init__(self):
        self.calls = []
        self.saved = set()

    async def list_saved_professional_cabinet_ids(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return self.saved


def build_result_search_application(
    *,
    current_context=None,
    settings_error=None,
):
    geo_search = FakeResultGeoSearch()
    favorites = FakeResultFavorites()

    service = UserSearchService(
        object(),
        settings=FakeSettings(
            current_context,
            settings_error,
        ),
        repository=object(),
        selection=FakeSelection(),
        text_search=FakeTextSearch(),
        search_repository=object(),
        geo_search=geo_search,
        favorite_repository=object(),
        favorites=favorites,
    )

    return service, geo_search, favorites


@pytest.mark.asyncio
async def test_search_results_without_location():
    current = context()
    (
        service,
        geo_search,
        favorites,
    ) = build_result_search_application(
        current_context=current
    )
    geo_search.page_results = [
        search_result()
        for _ in range(6)
    ]
    geo_search.total_results = [
        search_result()
        for _ in range(8)
    ]
    favorites.saved = {
        geo_search.page_results[
            0
        ].professional_cabinet.id
    }

    page = await service.search_results(
        platform_user_id=123,
        data={
            "location_state": "without",
            "available_only": True,
        },
        page=0,
        page_size=5,
    )

    assert isinstance(page, UserSearchPage)
    assert len(page.visible_results) == 5
    assert page.total_count == 8
    assert page.has_next is True
    assert page.saved_professional_cabinet_ids == (
        frozenset(favorites.saved)
    )

    search_calls = [
        call
        for call in geo_search.calls
        if call[0] == "without"
    ]
    assert len(search_calls) == 2
    assert (
        search_calls[0][1]["limit"]
        == 6
    )
    assert (
        search_calls[1][1]["limit"]
        == 200
    )
    assert all(
        call[1]["available_only"]
        is True
        for call in search_calls
    )
    assert any(
        call[0] == "viewed"
        for call in geo_search.calls
    )
    assert favorites.calls


@pytest.mark.asyncio
async def test_search_results_by_radius():
    current = context()
    service, geo_search, _ = (
        build_result_search_application(
            current_context=current
        )
    )
    geo_search.page_results = [
        search_result()
    ]

    page = await service.search_results(
        platform_user_id=123,
        data={
            "latitude": "50.45",
            "longitude": "30.52",
            "radius_km": "20",
        },
        page=0,
    )

    method, kwargs = geo_search.calls[0]
    assert method == "radius"
    assert kwargs["latitude"] == 50.45
    assert kwargs["longitude"] == 30.52
    assert kwargs["radius_km"] == 20.0
    assert len(page.visible_results) == 1


@pytest.mark.asyncio
async def test_search_results_by_city():
    current = context()
    service, geo_search, _ = (
        build_result_search_application(
            current_context=current
        )
    )
    city_id = uuid4()
    geo_search.page_results = [
        search_result()
    ]

    await service.search_results(
        platform_user_id=123,
        data={
            "city_id": str(city_id),
            "location_state": "city",
        },
        page=0,
    )

    method, kwargs = geo_search.calls[0]
    assert method == "city"
    assert kwargs["city_id"] == city_id


@pytest.mark.asyncio
async def test_empty_search_is_recorded():
    current = context()
    service, geo_search, favorites = (
        build_result_search_application(
            current_context=current
        )
    )

    page = await service.search_results(
        platform_user_id=123,
        data={},
        page=0,
    )

    assert page.visible_results == ()
    assert page.total_count == 0
    assert page.has_next is False
    assert favorites.calls == []
    assert [
        call[0]
        for call in geo_search.calls
    ] == [
        "without",
        "viewed",
        "empty",
    ]


@pytest.mark.asyncio
async def test_search_results_require_actor():
    (
        service,
        geo_search,
        favorites,
    ) = build_result_search_application(
        settings_error=(
            UserSettingsNotFoundError()
        )
    )

    with pytest.raises(
        UserSearchAccessError,
        match="Registered",
    ):
        await service.search_results(
            platform_user_id=456,
            data={},
            page=0,
        )

    assert geo_search.calls == []
    assert favorites.calls == []



def test_render_results_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name == "render_results"
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
        "UserSearchService"
        in called_names
    )
    assert (
        "search_results"
        in called_methods
    )
    assert not (
        called_names
        & {
            "UUID",
            "FavoriteRepository",
            "FavoriteService",
            "GeoSearchService",
            (
                "SpecialistSearch"
                "Repository"
            ),
            "EmptySearchEvent",
            "SearchResultsViewedEvent",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert (
        "saved_professional_cabinet_ids"
        in block
    )
    assert (
        "last_search_result_message_ids"
        in block
    )
    assert (
        "result_specialist_ids"
        in block
    )


def test_complaint_target_summary_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "store_complaint_target_summary"
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

    assert "UserSearchService" in called_names
    assert "get_selected_card" in called_methods

    assert not (
        called_names
        & {
            "UUID",
            "GeoSearchService",
            "SpecialistSearchRepository",
            "get_requester_context",
        }
    )
