from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_portfolio import (
    AdminPortfolioAccessError,
    AdminPortfolioDecisionError,
    AdminPortfolioService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.calls = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.calls.append(platform_user_id)
        return self.user


class FakeModeration:
    def __init__(self):
        self.roles_by_user = {}
        self.role_calls = []

    async def get_admin_roles(
        self,
        user_id,
        *,
        tenant_id,
    ):
        self.role_calls.append(
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            }
        )
        return set(
            self.roles_by_user.get(
                user_id,
                set(),
            )
        )


class FakePortfolio:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def __getattr__(self, name):
        async def method(**kwargs):
            self.calls.append((name, kwargs))
            return self.responses.get(name, name)

        return method


def build_service(*, roles):
    actor_id = uuid4()
    tenant_id = uuid4()
    user = SimpleNamespace(
        id=actor_id,
        tenant_id=tenant_id,
    )
    users = FakeUsers(user)
    moderation = FakeModeration()
    portfolio = FakePortfolio()

    moderation.roles_by_user[actor_id] = set(
        roles
    )

    service = AdminPortfolioService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
        portfolio=portfolio,
    )

    return (
        service,
        users,
        moderation,
        portfolio,
        actor_id,
        tenant_id,
    )


def portfolio_view(item_id):
    return SimpleNamespace(
        item=SimpleNamespace(id=item_id)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        None,
        SimpleNamespace(
            id=uuid4(),
            tenant_id=None,
        ),
    ],
)
async def test_missing_actor_fails_closed(user):
    users = FakeUsers(user)
    moderation = FakeModeration()
    portfolio = FakePortfolio()
    service = AdminPortfolioService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
        portfolio=portfolio,
    )

    with pytest.raises(
        AdminPortfolioAccessError,
        match="access denied",
    ):
        await service.list_pending_items(
            platform_user_id=123,
        )

    assert not moderation.role_calls
    assert not portfolio.calls


@pytest.mark.asyncio
async def test_unrelated_role_fails_closed():
    (
        service,
        _,
        _,
        portfolio,
        _,
        _,
    ) = build_service(roles={"support"})

    with pytest.raises(
        AdminPortfolioAccessError
    ):
        await service.list_rejected_items(
            platform_user_id=123,
        )

    assert not portfolio.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles",
    [
        {"admin"},
        {"moderator"},
        {"super_admin"},
    ],
)
async def test_moderation_roles_are_allowed(
    roles,
):
    (
        service,
        _,
        moderation,
        portfolio,
        actor_id,
        tenant_id,
    ) = build_service(roles=roles)

    expected = [object()]
    portfolio.responses[
        "list_pending_items"
    ] = expected

    result = await service.list_pending_items(
        platform_user_id=123,
        page=2,
        page_size=4,
        language="uk",
    )

    assert result is expected
    assert moderation.role_calls == [
        {
            "user_id": actor_id,
            "tenant_id": tenant_id,
        }
    ]
    assert portfolio.calls == [
        (
            "list_pending_items",
            {
                "tenant_id": tenant_id,
                "moderator_user_id": actor_id,
                "page": 2,
                "page_size": 4,
                "language": "uk",
            },
        )
    ]


@pytest.mark.asyncio
async def test_pending_item_is_selected_from_page():
    (
        service,
        _,
        _,
        portfolio,
        _,
        _,
    ) = build_service(roles={"moderator"})

    first_id = uuid4()
    target_id = uuid4()
    outside_page_id = uuid4()

    first = portfolio_view(first_id)
    target = portfolio_view(target_id)
    outside_page = portfolio_view(
        outside_page_id
    )

    portfolio.responses[
        "list_pending_items"
    ] = [
        first,
        target,
        outside_page,
    ]

    result = await service.get_pending_item(
        platform_user_id=123,
        item_id=target_id,
        page=1,
        page_size=2,
        language="en",
    )
    hidden = await service.get_pending_item(
        platform_user_id=123,
        item_id=outside_page_id,
        page=1,
        page_size=2,
        language="en",
    )

    assert result is target
    assert hidden is None


@pytest.mark.asyncio
async def test_rejected_items_are_tenant_bound():
    (
        service,
        _,
        _,
        portfolio,
        actor_id,
        tenant_id,
    ) = build_service(roles={"admin"})

    expected = [object()]
    portfolio.responses[
        "list_rejected_items"
    ] = expected

    result = await service.list_rejected_items(
        platform_user_id=123,
        limit=40,
        language="pt",
    )

    assert result is expected
    assert portfolio.calls == [
        (
            "list_rejected_items",
            {
                "tenant_id": tenant_id,
                "moderator_user_id": actor_id,
                "limit": 40,
                "language": "pt",
            },
        )
    ]


@pytest.mark.asyncio
async def test_rejected_item_is_selected():
    (
        service,
        _,
        _,
        portfolio,
        _,
        _,
    ) = build_service(roles={"moderator"})

    target_id = uuid4()
    target = portfolio_view(target_id)

    portfolio.responses[
        "list_rejected_items"
    ] = [
        portfolio_view(uuid4()),
        target,
    ]

    result = await service.get_rejected_item(
        platform_user_id=123,
        item_id=target_id,
    )

    assert result is target


@pytest.mark.asyncio
async def test_restore_is_tenant_bound():
    (
        service,
        _,
        _,
        portfolio,
        actor_id,
        tenant_id,
    ) = build_service(roles={"moderator"})

    item_id = uuid4()
    restored = SimpleNamespace(
        id=item_id,
        status="pending",
    )
    portfolio.responses[
        "restore_rejected_item"
    ] = restored

    action = await service.restore_rejected_item(
        platform_user_id=123,
        item_id=item_id,
    )

    assert action.actor.user_id == actor_id
    assert action.actor.tenant_id == tenant_id
    assert action.result is restored
    assert portfolio.calls == [
        (
            "restore_rejected_item",
            {
                "tenant_id": tenant_id,
                "moderator_user_id": actor_id,
                "item_id": item_id,
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "decision",
        "portfolio_method",
    ),
    [
        ("approved", "approve_item"),
        ("rejected", "reject_item"),
        (
            "forbidden",
            "reject_forbidden_item",
        ),
    ],
)
async def test_moderation_decisions_dispatch(
    decision,
    portfolio_method,
):
    (
        service,
        _,
        _,
        portfolio,
        actor_id,
        tenant_id,
    ) = build_service(roles={"moderator"})

    item_id = uuid4()
    expected = SimpleNamespace(
        id=item_id,
        status=decision,
    )
    portfolio.responses[
        portfolio_method
    ] = expected

    action = await service.moderate_item(
        platform_user_id=123,
        item_id=item_id,
        decision=decision,
        reason="Valid moderation reason",
    )

    assert action.actor.user_id == actor_id
    assert action.result is expected
    assert portfolio.calls == [
        (
            portfolio_method,
            {
                "tenant_id": tenant_id,
                "moderator_user_id": actor_id,
                "item_id": item_id,
                "reason": (
                    "Valid moderation reason"
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_unknown_decision_is_rejected():
    (
        service,
        _,
        _,
        portfolio,
        _,
        _,
    ) = build_service(roles={"moderator"})

    with pytest.raises(
        AdminPortfolioDecisionError,
        match="Unsupported",
    ):
        await service.moderate_item(
            platform_user_id=123,
            item_id=uuid4(),
            decision="unknown",
            reason="Valid reason",
        )

    assert not portfolio.calls


@pytest.mark.asyncio
async def test_impersonation_requires_super_admin():
    (
        service,
        _,
        moderation,
        portfolio,
        _,
        _,
    ) = build_service(roles={"admin"})

    with pytest.raises(
        AdminPortfolioAccessError
    ):
        await (
            service
            .list_impersonated_pending_items(
                platform_user_id=123,
                effective_moderator_user_id=(
                    uuid4()
                ),
            )
        )

    assert len(moderation.role_calls) == 1
    assert not portfolio.calls


@pytest.mark.asyncio
async def test_impersonation_rejects_wrong_target():
    (
        service,
        _,
        moderation,
        portfolio,
        _,
        _,
    ) = build_service(roles={"super_admin"})

    target_id = uuid4()
    moderation.roles_by_user[target_id] = {
        "support"
    }

    with pytest.raises(
        AdminPortfolioAccessError
    ):
        await (
            service
            .list_impersonated_pending_items(
                platform_user_id=123,
                effective_moderator_user_id=(
                    target_id
                ),
            )
        )

    assert len(moderation.role_calls) == 2
    assert not portfolio.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_role",
    [
        "admin",
        "moderator",
    ],
)
async def test_impersonated_queue_is_tenant_bound(
    target_role,
):
    (
        service,
        _,
        moderation,
        portfolio,
        _,
        tenant_id,
    ) = build_service(roles={"super_admin"})

    target_id = uuid4()
    moderation.roles_by_user[target_id] = {
        target_role
    }
    expected = [object()]
    portfolio.responses[
        "list_pending_items"
    ] = expected

    result = (
        await service
        .list_impersonated_pending_items(
            platform_user_id=123,
            effective_moderator_user_id=(
                target_id
            ),
            page=3,
            page_size=5,
            language="de",
        )
    )

    assert result is expected
    assert portfolio.calls == [
        (
            "list_pending_items",
            {
                "tenant_id": tenant_id,
                "moderator_user_id": target_id,
                "page": 3,
                "page_size": 5,
                "language": "de",
            },
        )
    ]


@pytest.mark.asyncio
async def test_impersonated_item_is_selected():
    (
        service,
        _,
        moderation,
        portfolio,
        _,
        _,
    ) = build_service(roles={"super_admin"})

    target_user_id = uuid4()
    target_item_id = uuid4()
    target_view = portfolio_view(
        target_item_id
    )

    moderation.roles_by_user[
        target_user_id
    ] = {"moderator"}
    portfolio.responses[
        "list_pending_items"
    ] = [
        portfolio_view(uuid4()),
        target_view,
    ]

    result = (
        await service
        .get_impersonated_pending_item(
            platform_user_id=123,
            effective_moderator_user_id=(
                target_user_id
            ),
            item_id=target_item_id,
        )
    )

    assert result is target_view


def test_portfolio_read_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_portfolio.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "list_pending_portfolio": (
            "list_pending_items"
        ),
        "show_pending_portfolio_item": (
            "get_pending_item"
        ),
        "list_rejected_portfolio": (
            "list_rejected_items"
        ),
        "show_rejected_portfolio_item": (
            "get_rejected_item"
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

        function_source = ast.get_source_segment(
            source,
            node,
        )
        called_names = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(
                child.func,
                ast.Name,
            )
        }

        assert (
            "AdminPortfolioService"
            in called_names
        )
        assert service_method in function_source
        assert (
            "get_admin_user_context"
            not in called_names
        )
        assert (
            "PortfolioRepository"
            not in called_names
        )
        assert (
            "PortfolioService"
            not in called_names
        )


def test_impersonated_portfolio_read_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_portfolio.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        (
            "super_admin_read_only_"
            "moderator_portfolio"
        ): "list_impersonated_pending_items",
        (
            "show_super_admin_read_only_"
            "portfolio_item"
        ): "get_impersonated_pending_item",
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

        function_source = ast.get_source_segment(
            source,
            node,
        )
        called_names = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(
                child.func,
                ast.Name,
            )
        }

        assert (
            "AdminPortfolioService"
            in called_names
        )
        assert service_method in function_source
        assert (
            "get_admin_user_context"
            not in called_names
        )
        assert (
            "PortfolioRepository"
            not in called_names
        )
        assert (
            "PortfolioService"
            not in called_names
        )


def test_portfolio_mutation_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_portfolio.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "restore_rejected_portfolio_item": (
            "restore_rejected_item"
        ),
        "confirm_portfolio_moderation": (
            "moderate_item"
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

        function_source = ast.get_source_segment(
            source,
            node,
        )
        called_names = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(
                child.func,
                ast.Name,
            )
        }

        assert (
            "AdminPortfolioService"
            in called_names
        )
        assert service_method in function_source
        assert (
            "get_admin_user_context"
            not in called_names
        )
        assert (
            "PortfolioRepository"
            not in called_names
        )
        assert (
            "PortfolioService"
            not in called_names
        )


def test_all_portfolio_handlers_are_application_service_owned():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_portfolio.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    portfolio_functions = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and "portfolio" in node.name.lower()
    ]

    assert portfolio_functions

    forbidden_calls = {
        "get_admin_user_context",
        "PortfolioRepository",
        "PortfolioService",
        "ModerationRepository",
        "ModerationService",
        "UserService",
    }

    for node in portfolio_functions:
        called_names = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(
                child.func,
                ast.Name,
            )
        }

        found = called_names & forbidden_calls

        assert not found, (
            node.name,
            sorted(found),
        )
