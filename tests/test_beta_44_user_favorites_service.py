from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_favorites import (
    UserFavoritesAccessError,
    UserFavoritesSelectionError,
    UserFavoritesService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
)


class FakeSettings:
    def __init__(self, context=None, error=None):
        self.context = context
        self.error = error
        self.calls = []

    async def get_context(self, **kwargs):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return self.context


class FakeFavorites:
    def __init__(self):
        self.calls = []
        self.page_result = SimpleNamespace(
            cards=["card"],
            page=2,
            has_next=True,
        )
        self.card_result = "card"
        self.remove_result = True
        self.toggle_result = True

    async def list_public_cards_page(
        self,
        **kwargs,
    ):
        self.calls.append(("list", kwargs))
        return self.page_result

    async def get_saved_public_card(
        self,
        **kwargs,
    ):
        self.calls.append(("get", kwargs))
        return self.card_result

    async def remove_professional_cabinet(
        self,
        **kwargs,
    ):
        self.calls.append(("remove", kwargs))
        return self.remove_result

    async def toggle_professional_cabinet(
        self,
        **kwargs,
    ):
        self.calls.append(("toggle", kwargs))
        return self.toggle_result


def build_service(*, context=None, error=None):
    settings = FakeSettings(context, error)
    favorites = FakeFavorites()
    service = UserFavoritesService(
        object(),
        settings=settings,
        favorites=favorites,
    )
    return service, settings, favorites


def actor_context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


@pytest.mark.asyncio
async def test_require_actor_uses_telegram_context():
    context = actor_context()
    service, settings, _ = build_service(
        context=context
    )

    actor = await service.require_actor(
        platform_user_id=123
    )

    assert actor.user_id == context.user_id
    assert actor.tenant_id == context.tenant_id
    assert actor.language == "uk"
    assert settings.calls == [
        {"platform_user_id": 123}
    ]


@pytest.mark.asyncio
async def test_missing_actor_is_denied():
    service, _, _ = build_service(
        error=UserSettingsNotFoundError()
    )

    with pytest.raises(UserFavoritesAccessError):
        await service.require_actor(
            platform_user_id=123
        )


@pytest.mark.asyncio
async def test_list_favorites_uses_actor_scope():
    context = actor_context()
    service, _, favorites = build_service(
        context=context
    )

    result = await service.list_favorites(
        platform_user_id=123,
        page=2,
        page_size=5,
    )

    assert result.cards == ["card"]
    assert result.page == 2
    assert result.has_next is True
    assert favorites.calls == [
        (
            "list",
            {
                "tenant_id": context.tenant_id,
                "user_id": context.user_id,
                "page": 2,
                "page_size": 5,
                "language": "uk",
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_favorite_card_uses_actor_scope():
    context = actor_context()
    cabinet_id = uuid4()
    service, _, favorites = build_service(
        context=context
    )

    result = await service.get_favorite_card(
        platform_user_id=123,
        professional_cabinet_id=str(cabinet_id),
    )

    assert result.result == "card"
    assert favorites.calls[0][0] == "get"
    assert (
        favorites.calls[0][1]
        ["professional_cabinet_id"]
        == cabinet_id
    )


@pytest.mark.asyncio
async def test_malformed_card_id_fails_closed():
    service, _, favorites = build_service(
        context=actor_context()
    )

    result = await service.get_favorite_card(
        platform_user_id=123,
        professional_cabinet_id="invalid",
    )

    assert result.result is None
    assert favorites.calls == []


@pytest.mark.asyncio
async def test_remove_favorite_keeps_source():
    context = actor_context()
    cabinet_id = uuid4()
    service, _, favorites = build_service(
        context=context
    )

    result = await service.remove_favorite(
        platform_user_id=123,
        professional_cabinet_id=cabinet_id,
    )

    assert result.result is True
    assert favorites.calls == [
        (
            "remove",
            {
                "tenant_id": context.tenant_id,
                "user_id": context.user_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "source": "favorites",
            },
        )
    ]


@pytest.mark.asyncio
async def test_malformed_remove_id_fails_closed():
    service, _, favorites = build_service(
        context=actor_context()
    )

    result = await service.remove_favorite(
        platform_user_id=123,
        professional_cabinet_id="invalid",
    )

    assert result.result is False
    assert favorites.calls == []


def test_favorites_handlers_use_application_service():
    import ast

    source = open(
        "handlers/user_favorites.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    for function_name in (
        "show_favorites",
        "show_favorite_card",
        "remove_favorite_from_cabinet",
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
            and isinstance(call.func, ast.Name)
        }

        assert "UserFavoritesService" in called_names
        assert "FavoriteRepository" not in called_names
        assert "FavoriteService" not in called_names
        assert "get_billing_user_context" not in called_names


def test_user_favorites_router_is_independent():
    import ast

    favorites_source = open(
        "handlers/user_favorites.py",
        encoding="utf-8",
    ).read()
    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    favorites_tree = ast.parse(favorites_source)
    billing_tree = ast.parse(billing_source)

    favorites_names = {
        getattr(node, "name", None)
        for node in favorites_tree.body
    }
    billing_names = {
        getattr(node, "name", None)
        for node in billing_tree.body
    }

    moved = {
        "show_favorites",
        "show_favorite_card",
        "remove_favorite_from_cabinet",
    }

    assert moved <= favorites_names
    assert not (moved & billing_names)
    assert (
        "user_favorites_router = Router()"
        in favorites_source
    )
    assert (
        "from handlers.billing import"
        not in favorites_source
    )
    assert (
        "dp.include_router(user_favorites_router)"
        in bot_source
    )




@pytest.mark.asyncio
async def test_toggle_favorite_uses_actor_scope():
    context = actor_context()
    cabinet_id = uuid4()
    service, _, favorites = build_service(
        context=context
    )

    result = await service.toggle_favorite(
        platform_user_id=123,
        professional_cabinet_id=(
            str(cabinet_id)
        ),
    )

    assert result.actor.user_id == context.user_id
    assert result.actor.language == "uk"
    assert result.result is True
    assert favorites.calls == [
        (
            "toggle",
            {
                "tenant_id": context.tenant_id,
                "user_id": context.user_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_toggle_invalid_id_fails_closed():
    service, _, favorites = build_service(
        context=actor_context()
    )

    with pytest.raises(
        UserFavoritesSelectionError,
        match="Invalid professional cabinet",
    ):
        await service.toggle_favorite(
            platform_user_id=123,
            professional_cabinet_id="invalid",
        )

    assert favorites.calls == []



def test_search_favorite_handler_uses_application_service():
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
        and item.name == "favorite_pending"
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
        "UserFavoritesService"
        in called_names
    )
    assert (
        "toggle_favorite"
        in called_methods
    )
    assert (
        "UserFavoritesAccessError"
        in caught_errors
    )
    assert not (
        called_names
        & {
            "UUID",
            "FavoriteRepository",
            "FavoriteService",
            "get_requester_context",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert (
        "store_post_auth_action"
        in block
    )
    assert (
        "selected_specialist_is_saved"
        in block
    )



def test_favorites_handlers_do_not_mutate_callback_query():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/user_favorites.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    assignments = [
        target
        for node in ast.walk(tree)
        if isinstance(
            node,
            (
                ast.Assign,
                ast.AnnAssign,
                ast.AugAssign,
            ),
        )
        for target in (
            node.targets
            if isinstance(
                node,
                ast.Assign,
            )
            else [node.target]
        )
        if isinstance(
            target,
            ast.Attribute,
        )
        and isinstance(
            target.value,
            ast.Name,
        )
        and target.value.id == "callback"
        and target.attr == "data"
    ]

    assert assignments == []


def test_favorite_removal_passes_page_explicitly():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/user_favorites.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "remove_favorite_from_cabinet"
    )
    calls = [
        call
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
        and call.func.id
        == "show_favorites"
    ]

    assert len(calls) == 1

    keywords = {
        keyword.arg: keyword.value
        for keyword
        in calls[0].keywords
    }
    requested_page = keywords.get(
        "requested_page"
    )

    assert isinstance(
        requested_page,
        ast.Name,
    )
    assert requested_page.id == "page"
