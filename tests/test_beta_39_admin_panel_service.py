from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_panel import (
    AdminPanelAccessError,
    AdminPanelService,
)


class FakeUsers:
    def __init__(
        self,
        user,
        *,
        active_role=None,
        available_roles=(),
    ):
        self.user = user
        self.active_role = active_role
        self.available_roles = tuple(
            available_roles
        )
        self.user_calls = []
        self.context_calls = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.user_calls.append(platform_user_id)
        return self.user

    async def get_role_switch_context(
        self,
        platform_user_id,
    ):
        self.context_calls.append(platform_user_id)

        return SimpleNamespace(
            active_role=self.active_role,
            available_roles=self.available_roles,
        )


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

    def __getattr__(self, method_name):
        async def method(**kwargs):
            self.calls.append(
                (method_name, kwargs)
            )
            return method_name

        return method


class FakeSupport:
    def __init__(self):
        self.calls = []

    async def open_staff_menu(
        self,
        *,
        platform_user_id,
    ):
        self.calls.append(platform_user_id)

        return SimpleNamespace(
            counts="support-counts",
            show_role_switch=True,
        )


def build_service(
    roles,
    *,
    active_role=None,
):
    user_id = uuid4()
    tenant_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        tenant_id=tenant_id,
        language_code="uk",
    )
    users = FakeUsers(
        user,
        active_role=active_role,
        available_roles=roles,
    )
    moderation = FakeModeration(roles)
    support = FakeSupport()
    service = AdminPanelService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
        support=support,
    )

    return (
        service,
        users,
        moderation,
        support,
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
            language_code="ru",
        ),
    ],
)
async def test_missing_actor_fails_closed(user):
    service = AdminPanelService(
        SimpleNamespace(),
        users=FakeUsers(user),
        moderation=FakeModeration(
            {"super_admin"}
        ),
        support=FakeSupport(),
    )

    with pytest.raises(
        AdminPanelAccessError,
        match="access denied",
    ):
        await service.open_panel(
            platform_user_id=123
        )


@pytest.mark.asyncio
async def test_empty_roles_fail_closed():
    service, _, _, _, _, _ = (
        build_service(set())
    )

    with pytest.raises(
        AdminPanelAccessError
    ):
        await service.open_panel(
            platform_user_id=123
        )


@pytest.mark.asyncio
async def test_role_lookup_is_tenant_bound():
    (
        service,
        _,
        moderation,
        _,
        user_id,
        tenant_id,
    ) = build_service(
        {"admin"},
        active_role="admin",
    )

    await service.open_panel(
        platform_user_id=123
    )

    assert moderation.role_calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "role",
        "panel_type",
        "method_name",
        "actor_key",
    ),
    [
        (
            "super_admin",
            "super_admin",
            "open_super_admin_menu",
            "admin_user_id",
        ),
        (
            "admin",
            "admin",
            "open_admin_menu",
            "admin_user_id",
        ),
        (
            "moderator",
            "moderator",
            "open_moderator_menu",
            "moderator_user_id",
        ),
    ],
)
async def test_staff_panel_branch(
    role,
    panel_type,
    method_name,
    actor_key,
):
    (
        service,
        _,
        moderation,
        _,
        user_id,
        tenant_id,
    ) = build_service(
        {role},
        active_role=role,
    )

    result = await service.open_panel(
        platform_user_id=123
    )

    assert result.panel_type == panel_type
    assert result.payload == method_name
    assert result.language_code == "uk"
    assert moderation.calls == [
        (
            method_name,
            {
                actor_key: user_id,
                "tenant_id": tenant_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_support_panel_branch():
    (
        service,
        _,
        moderation,
        support,
        _,
        _,
    ) = build_service(
        {"support", "client"},
        active_role="support",
    )

    result = await service.open_panel(
        platform_user_id=123
    )

    assert result.panel_type == "support"
    assert result.payload.counts == (
        "support-counts"
    )
    assert result.show_role_switch is True
    assert support.calls == [123]
    assert not moderation.calls


@pytest.mark.asyncio
async def test_generic_panel_uses_active_role_only():
    service, _, _, _, _, _ = build_service(
        {"finance_admin", "support"},
        active_role="finance_admin",
    )

    result = await service.open_panel(
        platform_user_id=123
    )

    assert result.panel_type == "generic"
    assert result.active_role == "finance_admin"
    assert result.panel_roles == frozenset(
        {"finance_admin"}
    )
    assert result.payload is None


def test_show_admin_panel_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == "show_admin_panel"
    )
    block = ast.get_source_segment(
        source,
        node,
    )

    assert "AdminPanelService(" in block
    assert "open_panel" in block
    assert "get_admin_user_context(" not in block
    assert "UserService(" not in block
    assert "ModerationRepository(" not in block
    assert "ModerationService(" not in block
    assert "AdminSupportService(" not in block

    assert "edit_or_replace_menu_message(" in block
    assert "delete_telegram_messages(" in block
    assert "await state.clear()" in block
    assert "last_menu_message_id" in block


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        "admin",
        "super_admin",
    ],
)
async def test_opens_admin_moderation_menu(role):
    (
        service,
        _,
        moderation,
        _,
        user_id,
        tenant_id,
    ) = build_service(
        {role},
        active_role=role,
    )

    result = await service.open_moderation_menu(
        platform_user_id=123
    )

    assert result.user_id == user_id
    assert result.tenant_id == tenant_id
    assert result.roles == frozenset({role})
    assert result.summary == (
        "open_moderator_menu"
    )
    assert moderation.calls == [
        (
            "open_moderator_menu",
            {
                "moderator_user_id": user_id,
                "tenant_id": tenant_id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_moderation_menu_fails_closed():
    (
        service,
        _,
        moderation,
        _,
        _,
        _,
    ) = build_service(
        {"finance_admin"},
        active_role="finance_admin",
    )

    with pytest.raises(
        AdminPanelAccessError
    ):
        await service.open_moderation_menu(
            platform_user_id=123
        )

    assert not moderation.calls


def test_moderation_menu_handler_uses_panel_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item for item in ast.walk(tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "open_admin_moderation_menu"
    )
    block = ast.get_source_segment(
        source,
        node,
    )

    assert "AdminPanelService(" in block
    assert "open_moderation_menu" in block
    assert "get_admin_user_context(" not in block
    assert "ModerationRepository(" not in block
    assert "ModerationService(" not in block

    assert "await state.clear()" in block
    assert "replace_admin_callback_screen(" in block
    assert "moderator_menu_keyboard(" in block
