from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_governance import (
    AdminGovernanceAccessError,
    AdminGovernanceService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.requested_ids = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.requested_ids.append(platform_user_id)
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
            self.roles_by_user.get(user_id, set())
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
    moderation.roles_by_user[actor_id] = set(roles)

    service = AdminGovernanceService(
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
    service = AdminGovernanceService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    with pytest.raises(
        AdminGovernanceAccessError,
        match="access denied",
    ):
        await service.list_permission_matrix(
            platform_user_id=123,
        )

    assert not moderation.role_calls
    assert not moderation.calls


@pytest.mark.asyncio
async def test_non_super_admin_fails_closed():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"admin"})

    with pytest.raises(AdminGovernanceAccessError):
        await service.list_role_scopes(
            platform_user_id=123,
            user_id=None,
            view="active",
            page=0,
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
            "list_role_scopes",
            {
                "user_id": uuid4(),
                "view": "history",
                "page": 2,
                "page_size": 7,
            },
            "open_super_admin_role_scopes",
        ),
        (
            "list_permission_matrix",
            {
                "query": "admin.users.read",
                "limit": 8,
            },
            "list_super_admin_permission_matrix",
        ),
        (
            "grant_role_permission",
            {
                "role": "admin",
                "permission_code": "admin.users.read",
                "reason": "governance test",
            },
            "grant_super_admin_permission",
        ),
        (
            "revoke_role_permission",
            {
                "role": "moderator",
                "permission_code": "moderation.review",
                "reason": "governance test",
            },
            "revoke_super_admin_permission",
        ),
    ],
)
async def test_governance_operations_are_tenant_bound(
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
    ) = build_service(roles={"super_admin"})

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=987,
        **arguments,
    )

    assert result == moderation_method
    assert users.requested_ids == [987]
    assert moderation.role_calls == [
        {
            "user_id": actor_id,
            "tenant_id": tenant_id,
        }
    ]

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs["admin_user_id"] == actor_id
    assert kwargs["tenant_id"] == tenant_id

    for key, value in arguments.items():
        assert kwargs[key] == value

def test_governance_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_governance.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "open_super_admin_role_scopes_queue": (
            "list_role_scopes"
        ),
        "show_super_admin_permissions": (
            "list_permission_matrix"
        ),
        "super_admin_permission_execute": (
            "grant_role_permission"
        ),
    }

    for function_name, service_method in expected.items():
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name == function_name
        )
        block = ast.get_source_segment(
            source,
            node,
        )

        assert "AdminGovernanceService" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationService(" not in block

    execute_node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == (
            "super_admin_permission_execute"
        )
    )
    execute_block = ast.get_source_segment(
        source,
        execute_node,
    )

    assert "revoke_role_permission" in execute_block

def test_permission_history_authorization_is_service_owned():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_governance.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == (
            "super_admin_permission_history"
        )
    )
    block = ast.get_source_segment(source, node)

    assert "AdminGovernanceService" in block
    assert "require_super_admin_actor" in block
    assert "get_admin_user_context(" not in block
    assert "ModerationService(" not in block


def test_governance_entry_handlers_use_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for function_name in (
        "super_admin_roles_entry",
        "admin_roles_panel",
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

        assert "AdminGovernanceService(" in block
        assert "require_super_admin_actor" in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block
