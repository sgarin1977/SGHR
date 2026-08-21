from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_search_portfolio import (
    UserSearchPortfolioAccessError,
    UserSearchPortfolioSelectionError,
    UserSearchPortfolioService,
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


class FakePortfolio:
    def __init__(self):
        self.calls = []
        self.items = [
            "first",
            "second",
            "third",
        ]

    async def list_active_items_for_viewer(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return self.items


def actor_context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


def build_service(
    *,
    context=None,
    error=None,
):
    settings = FakeSettings(
        context,
        error,
    )
    portfolio = FakePortfolio()
    service = UserSearchPortfolioService(
        object(),
        settings=settings,
        portfolio=portfolio,
    )
    return service, settings, portfolio


@pytest.mark.asyncio
async def test_portfolio_requires_actor():
    service, _, portfolio = build_service(
        error=UserSettingsNotFoundError()
    )

    with pytest.raises(
        UserSearchPortfolioAccessError,
        match="viewer",
    ):
        await service.open_portfolio(
            platform_user_id=123,
            specialist_id=uuid4(),
        )

    assert portfolio.calls == []


@pytest.mark.asyncio
async def test_portfolio_uses_actor_scope():
    context = actor_context()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    service, settings, portfolio = (
        build_service(
            context=context
        )
    )

    result = await service.open_portfolio(
        platform_user_id=123,
        specialist_id=str(specialist_id),
        professional_cabinet_id=(
            str(cabinet_id)
        ),
        page=1,
    )

    assert result.actor.user_id == context.user_id
    assert result.actor.language == "uk"
    assert result.items == (
        "first",
        "second",
        "third",
    )
    assert result.page == 1
    assert result.selected == "second"
    assert settings.calls == [
        {
            "platform_user_id": 123,
        }
    ]
    assert portfolio.calls == [
        {
            "tenant_id": context.tenant_id,
            "specialist_id": specialist_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "viewer_user_id": (
                context.user_id
            ),
            "page": 1,
        }
    ]


@pytest.mark.asyncio
async def test_portfolio_page_is_clamped():
    service, _, _ = build_service(
        context=actor_context()
    )

    result = await service.open_portfolio(
        platform_user_id=123,
        specialist_id=uuid4(),
        page=99,
    )

    assert result.page == 2
    assert result.selected == "third"


@pytest.mark.asyncio
async def test_empty_portfolio_has_no_selection():
    service, _, portfolio = build_service(
        context=actor_context()
    )
    portfolio.items = []

    result = await service.open_portfolio(
        platform_user_id=123,
        specialist_id=uuid4(),
        page=4,
    )

    assert result.items == ()
    assert result.page == 0
    assert result.selected is None


@pytest.mark.asyncio
async def test_invalid_portfolio_id_fails_closed():
    service, _, portfolio = build_service(
        context=actor_context()
    )

    with pytest.raises(
        UserSearchPortfolioSelectionError,
        match="Invalid specialist",
    ):
        await service.open_portfolio(
            platform_user_id=123,
            specialist_id="invalid",
        )

    assert portfolio.calls == []



def test_public_portfolio_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = (
        (
            "resume_public_portfolio_"
            "after_auth"
        ),
        "render_public_portfolio",
    )

    for function_name in function_names:
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
            "UserSearchPortfolioService"
            in called_names
        )
        assert (
            "open_portfolio"
            in called_methods
        )
        assert not (
            called_names
            & {
                "UUID",
                "PortfolioRepository",
                "PortfolioService",
                "get_requester_context",
            }
        )

    callback_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "render_public_portfolio"
    )
    callback_block = ast.get_source_segment(
        source,
        callback_node,
    ) or ""

    assert (
        "store_post_auth_action"
        in callback_block
    )
