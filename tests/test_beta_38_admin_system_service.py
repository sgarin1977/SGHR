from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_system import (
    AdminSystemAccessError,
    AdminSystemService,
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
    def __init__(self, roles):
        self.roles = set(roles)
        self.role_calls = []
        self.calls = []

    async def get_admin_roles(
        self,
        user_id,
        *,
        tenant_id=None,
    ):
        self.role_calls.append(
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            }
        )
        return set(self.roles)

    def list_super_admin_smoke_definitions(
        self,
    ):
        self.calls.append(
            ("definitions", {})
        )
        return ("smoke-definition",)

    async def run_super_admin_smoke_tests(
        self,
        **kwargs,
    ):
        self.calls.append(("run", kwargs))
        return "smoke-run"

    async def list_super_admin_smoke_history(
        self,
        **kwargs,
    ):
        self.calls.append(("history", kwargs))
        return ("history-item",)

    async def open_super_admin_system_status(
        self,
        **kwargs,
    ):
        self.calls.append(("status", kwargs))
        return "system-status"


def build_service(*, roles={"super_admin"}):
    user_id = uuid4()
    tenant_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
    )
    users = FakeUsers(user)
    moderation = FakeModeration(roles)
    service = AdminSystemService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    return (
        service,
        users,
        moderation,
        user_id,
        tenant_id,
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
    moderation = FakeModeration(
        {"super_admin"}
    )
    service = AdminSystemService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    with pytest.raises(
        AdminSystemAccessError,
        match="access denied",
    ):
        await service.get_system_status(
            platform_user_id=123
        )

    assert not moderation.calls


@pytest.mark.asyncio
async def test_non_super_admin_fails_closed():
    service, _, moderation, _, _ = (
        build_service(roles={"admin"})
    )

    with pytest.raises(
        AdminSystemAccessError
    ):
        await service.list_smoke_definitions(
            platform_user_id=123
        )

    assert not moderation.calls


@pytest.mark.asyncio
async def test_role_lookup_is_tenant_bound():
    (
        service,
        _,
        moderation,
        user_id,
        tenant_id,
    ) = build_service()

    actor = await (
        service.require_super_admin_actor(
            platform_user_id=123
        )
    )

    assert actor.user_id == user_id
    assert actor.tenant_id == tenant_id
    assert moderation.role_calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
        }
    ]


@pytest.mark.asyncio
async def test_lists_smoke_definitions():
    service, _, moderation, _, _ = (
        build_service()
    )

    result = await service.list_smoke_definitions(
        platform_user_id=123
    )

    assert result == ("smoke-definition",)
    assert moderation.calls == [
        ("definitions", {})
    ]


@pytest.mark.asyncio
async def test_runs_smoke_tests_in_actor_tenant():
    (
        service,
        _,
        moderation,
        user_id,
        tenant_id,
    ) = build_service()

    result = await service.run_smoke_tests(
        platform_user_id=123,
        selected_code="search",
    )

    assert result == "smoke-run"
    assert moderation.calls == [
        (
            "run",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "selected_code": "search",
            },
        )
    ]


@pytest.mark.asyncio
async def test_lists_actor_smoke_history():
    (
        service,
        _,
        moderation,
        user_id,
        tenant_id,
    ) = build_service()

    result = await service.list_smoke_history(
        platform_user_id=123,
        limit=3,
    )

    assert result == ("history-item",)
    assert moderation.calls == [
        (
            "history",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
                "limit": 3,
            },
        )
    ]


@pytest.mark.asyncio
async def test_gets_actor_system_status():
    (
        service,
        _,
        moderation,
        user_id,
        tenant_id,
    ) = build_service()

    result = await service.get_system_status(
        platform_user_id=123
    )

    assert result == "system-status"
    assert moderation.calls == [
        (
            "status",
            {
                "admin_user_id": user_id,
                "tenant_id": tenant_id,
            },
        )
    ]


def test_smoke_entry_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for function_name in (
        "super_admin_smoke_panel",
        "super_admin_smoke_select",
    ):
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

        assert "AdminSystemService(" in block
        assert "list_smoke_definitions" in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block


def test_smoke_execution_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "super_admin_smoke_run_all":
            "run_smoke_tests",
        "super_admin_smoke_run_selected":
            "run_smoke_tests",
        "super_admin_smoke_history":
            "list_smoke_history",
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

        assert "AdminSystemService(" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block


def test_smoke_history_is_tenant_bound():
    from pathlib import Path

    application_source = Path(
        "services/admin_system.py"
    ).read_text(encoding="utf-8")
    domain_source = Path(
        "services/moderation.py"
    ).read_text(encoding="utf-8")

    assert (
        "tenant_id=actor.tenant_id"
        in application_source
    )
    assert (
        "AND tenant_id = :tenant_id"
        in domain_source
    )


def test_system_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for function_name in (
        "super_admin_system_panel",
        "super_admin_system_detail",
    ):
        node = next(
            item for item in ast.walk(tree)
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

        assert "AdminSystemService(" in block
        assert "get_system_status" in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block


def test_system_status_is_tenant_bound():
    import ast
    from pathlib import Path

    files = {
        "application": Path(
            "services/admin_system.py"
        ).read_text(encoding="utf-8"),
        "domain": Path(
            "services/moderation.py"
        ).read_text(encoding="utf-8"),
        "repository": Path(
            "database/repositories/moderation.py"
        ).read_text(encoding="utf-8"),
    }

    trees = {
        name: ast.parse(source)
        for name, source in files.items()
    }

    methods = {
        "application": "get_system_status",
        "domain": "open_super_admin_system_status",
        "repository": "get_super_admin_system_status",
    }

    blocks = {}

    for owner, method_name in methods.items():
        node = next(
            item
            for item in ast.walk(trees[owner])
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == method_name
        )
        blocks[owner] = ast.get_source_segment(
            files[owner],
            node,
        )

    assert (
        "tenant_id=actor.tenant_id"
        in blocks["application"]
    )
    assert (
        "tenant_id=tenant_id"
        in blocks["domain"]
    )
    assert (
        "tenant_id=tenant_id"
        in blocks["repository"]
    )
