from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_users import (
    AdminUsersAccessError,
    AdminUsersService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.requested_ids = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.requested_ids.append(
            platform_user_id
        )
        return self.user


class FakeModeration:
    def __init__(self):
        self.roles_by_user = {}
        self.role_calls = []
        self.calls = []

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

    def __getattr__(self, method_name):
        async def method(**kwargs):
            self.calls.append(
                (method_name, kwargs)
            )
            return method_name

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
    moderation.roles_by_user[actor_id] = set(
        roles
    )

    service = AdminUsersService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    return (
        service,
        users,
        moderation,
        actor_id,
        tenant_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user"),
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
    service = AdminUsersService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    with pytest.raises(
        AdminUsersAccessError,
        match="access denied",
    ):
        await service.search_regional_users(
            platform_user_id=123,
            query="test",
        )

    assert not moderation.calls
    assert not moderation.role_calls


@pytest.mark.asyncio
async def test_non_admin_role_fails_closed():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"support"})

    with pytest.raises(AdminUsersAccessError):
        await service.search_regional_users(
            platform_user_id=123,
            query="test",
        )

    assert not moderation.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments",
        "moderation_method",
    ),
    [
        (
            "search_regional_users",
            {"query": "search"},
            "search_admin_users",
        ),
        (
            "get_regional_user_details",
            {"target_user_id": uuid4()},
            "get_admin_user_details",
        ),
        (
            "list_regional_user_history",
            {
                "target_user_id": uuid4(),
                "limit": 7,
            },
            "list_admin_user_history",
        ),
    ],
)
async def test_regional_operations_are_tenant_bound(
    service_method,
    arguments,
    moderation_method,
):
    (
        service,
        users,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"admin"})

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=987,
        **arguments,
    )

    assert result == moderation_method
    assert users.requested_ids == [987]

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs["admin_user_id"] == actor_id
    assert kwargs["tenant_id"] == tenant_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments",
        "moderation_method",
    ),
    [
        (
            "search_super_admin_users",
            {"query": "search"},
            "search_super_admin_users",
        ),
        (
            "get_super_admin_user_details",
            {"target_user_id": uuid4()},
            "get_super_admin_user_details",
        ),
        (
            "list_super_admin_user_roles",
            {"target_user_id": uuid4()},
            "list_super_admin_user_roles",
        ),
    ],
)
async def test_super_admin_operations_require_super_admin(
    service_method,
    arguments,
    moderation_method,
):
    (
        service,
        _,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"super_admin"})

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=456,
        **arguments,
    )

    assert result == moderation_method

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs["admin_user_id"] == actor_id
    assert kwargs["tenant_id"] == tenant_id


@pytest.mark.asyncio
async def test_regular_admin_cannot_impersonate():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"admin"})

    with pytest.raises(AdminUsersAccessError):
        await service.search_impersonated_admin_users(
            platform_user_id=123,
            effective_admin_user_id=uuid4(),
            query="test",
        )

    assert not moderation.calls


@pytest.mark.asyncio
async def test_impersonation_requires_effective_admin():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"super_admin"})

    effective_admin_id = uuid4()
    moderation.roles_by_user[
        effective_admin_id
    ] = {"moderator"}

    with pytest.raises(
        AdminUsersAccessError,
        match="Impersonated Admin",
    ):
        await service.get_impersonated_admin_user_details(
            platform_user_id=123,
            effective_admin_user_id=(
                effective_admin_id
            ),
            target_user_id=uuid4(),
        )

    assert not moderation.calls


@pytest.mark.asyncio
async def test_impersonated_query_uses_effective_admin():
    (
        service,
        _,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"super_admin"})

    effective_admin_id = uuid4()
    moderation.roles_by_user[
        effective_admin_id
    ] = {"admin"}

    result = await (
        service.search_impersonated_admin_users(
            platform_user_id=123,
            effective_admin_user_id=(
                effective_admin_id
            ),
            query="regional",
        )
    )

    assert result == "search_admin_users"
    assert moderation.role_calls == [
        {
            "user_id": actor_id,
            "tenant_id": tenant_id,
        },
        {
            "user_id": effective_admin_id,
            "tenant_id": tenant_id,
        },
    ]
    assert moderation.calls == [
        (
            "search_admin_users",
            {
                "admin_user_id": (
                    effective_admin_id
                ),
                "tenant_id": tenant_id,
                "query": "regional",
            },
        )
    ]


def test_regional_user_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_users.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "receive_admin_user_search": (
            "search_regional_users"
        ),
        "open_admin_user_details": (
            "get_regional_user_details"
        ),
        "open_admin_user_roles": (
            "get_regional_user_details"
        ),
        "open_admin_user_history": (
            "list_regional_user_history"
        ),
    }

    for function_name, service_method in expected.items():
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = ast.get_source_segment(
            source,
            node,
        )

        assert "AdminUsersService(" in block
        assert service_method in block
        assert "ModerationService(" not in block
        assert "ModerationRepository(" not in block
        assert "get_admin_user_context(" not in block


def test_super_admin_user_handlers_use_application_service():
    import ast
    from pathlib import Path

    sources = {
        "users": Path(
            "handlers/admin_users.py"
        ).read_text(encoding="utf-8"),
        "global_blacklist": Path(
            "handlers/"
            "super_admin_global_blacklist.py"
        ).read_text(encoding="utf-8"),
    }

    trees = {
        name: ast.parse(source)
        for name, source in sources.items()
    }

    expected = {
        "super_admin_users_start": (
            "users",
            "require_super_admin_actor",
        ),
        "super_admin_user_search_message": (
            "users",
            "search_super_admin_users",
        ),
        "super_admin_open_user_card": (
            "users",
            "get_super_admin_user_details",
        ),
        "super_admin_user_roles": (
            "users",
            "list_super_admin_user_roles",
        ),
        "super_admin_user_profile_alias": (
            "users",
            "get_super_admin_user_details",
        ),
        "receive_super_admin_global_blacklist_add": (
            "global_blacklist",
            "search_super_admin_users",
        ),
    }

    for (
        function_name,
        (source_name, service_method),
    ) in expected.items():
        source = sources[source_name]
        tree = trees[source_name]

        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        source_lines = source.splitlines()
        block = "\n".join(
            source_lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        assert "AdminUsersService(" in block
        assert service_method in block
        assert "ModerationService(" not in block
        assert "ModerationRepository(" not in block
        assert "get_admin_user_context(" not in block


def test_impersonated_user_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_users.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "super_admin_read_only_admin_users_receive": (
            "search_impersonated_admin_users"
        ),
        "super_admin_read_only_admin_user_open": (
            "get_impersonated_admin_user_details"
        ),
        "super_admin_read_only_admin_user_roles": (
            "get_impersonated_admin_user_details"
        ),
        "super_admin_read_only_admin_user_history": (
            "list_impersonated_admin_user_history"
        ),
    }

    lines = source.splitlines()

    for function_name, service_method in expected.items():
        node = next(
            item for item in ast.walk(tree)
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        assert "AdminUsersService(" in block
        assert service_method in block
        assert "ModerationService(" not in block
        assert "ModerationRepository(" not in block
        assert "get_admin_user_context(" not in block



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_method", "roles"),
    [
        (
            "search_regional_users",
            {"admin"},
        ),
        (
            "search_super_admin_users",
            {"super_admin"},
        ),
    ],
)
@pytest.mark.parametrize(
    "query",
    [
        "",
        " ",
        "@",
        "@a",
    ],
)
async def test_admin_user_search_rejects_broad_queries(
    service_method,
    roles,
    query,
):
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles=roles)

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=123,
        query=query,
    )

    assert result == []
    assert moderation.calls == []


@pytest.mark.asyncio
async def test_super_admin_search_normalizes_username_prefix():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(
        roles={"super_admin"}
    )

    result = await (
        service.search_super_admin_users(
            platform_user_id=123,
            query=" @target_user ",
        )
    )

    assert result == (
        "search_super_admin_users"
    )
    assert (
        moderation.calls[-1][1]["query"]
        == "target_user"
    )
