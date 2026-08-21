from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.super_admin_global_blacklist import (
    SuperAdminGlobalBlacklistAccessError,
    SuperAdminGlobalBlacklistService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
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

    def __getattr__(self, name):
        async def method(**kwargs):
            self.calls.append(
                (name, kwargs)
            )
            return name

        return method


def build_service(*, roles):
    actor_id = uuid4()
    tenant_id = uuid4()
    moderation = FakeModeration()
    moderation.roles_by_user[
        actor_id
    ] = set(roles)

    service = SuperAdminGlobalBlacklistService(
        SimpleNamespace(),
        users=FakeUsers(
            SimpleNamespace(
                id=actor_id,
                tenant_id=tenant_id,
            )
        ),
        moderation=moderation,
    )

    return (
        service,
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
    moderation = FakeModeration()
    service = SuperAdminGlobalBlacklistService(
        SimpleNamespace(),
        users=FakeUsers(user),
        moderation=moderation,
    )

    with pytest.raises(
        SuperAdminGlobalBlacklistAccessError,
        match="access denied",
    ):
        await service.open_queue(
            platform_user_id=123,
            view="active",
        )

    assert not moderation.role_calls
    assert not moderation.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles",
    [
        {"admin"},
        {"moderator"},
        {"finance_admin"},
    ],
)
async def test_non_super_admin_fails_closed(
    roles,
):
    (
        service,
        moderation,
        _,
        _,
    ) = build_service(roles=roles)

    with pytest.raises(
        SuperAdminGlobalBlacklistAccessError
    ):
        await service.open_queue(
            platform_user_id=123,
            view="active",
        )

    assert not moderation.calls


@pytest.mark.asyncio
async def test_global_queue_requires_tenant_bound_role():
    (
        service,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(
        roles={"admin", "super_admin"}
    )

    result = await service.open_queue(
        platform_user_id=123,
        view="history",
        page=2,
        page_size=4,
    )

    assert result == (
        "open_super_admin_global_blacklist_queue"
    )
    assert moderation.role_calls == [
        {
            "user_id": actor_id,
            "tenant_id": tenant_id,
        }
    ]
    assert moderation.calls == [
        (
            "open_super_admin_global_blacklist_queue",
            {
                "admin_user_id": actor_id,
                "view": "history",
                "page": 2,
                "page_size": 4,
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments",
        "moderation_method",
    ),
    [
        (
            "block_user",
            {
                "user_id": uuid4(),
                "reason": "Confirmed abuse",
                "comment": "Global restriction",
            },
            "block_user",
        ),
        (
            "unblock_user",
            {
                "user_id": uuid4(),
                "reason": "Restriction removed",
            },
            "unblock_user",
        ),
    ],
)
async def test_global_actions_use_real_super_admin(
    service_method,
    arguments,
    moderation_method,
):
    (
        service,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"super_admin"})

    action = await getattr(
        service,
        service_method,
    )(
        platform_user_id=123,
        **arguments,
    )

    assert action.actor.user_id == actor_id
    assert action.actor.tenant_id == tenant_id
    assert action.result == moderation_method

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs["admin_user_id"] == actor_id

    for key, value in arguments.items():
        assert kwargs[key] == value


def test_global_blacklist_has_no_impersonation_api():
    assert not hasattr(
        SuperAdminGlobalBlacklistService,
        "open_impersonated_queue",
    )
    assert not hasattr(
        SuperAdminGlobalBlacklistService,
        "require_impersonated_actor",
    )

def test_super_admin_global_blacklist_handlers_use_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/super_admin_global_blacklist.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "ask_super_admin_global_blacklist_add": (
            "require_actor"
        ),
        "execute_super_admin_global_blacklist_add": (
            "block_user"
        ),
        "open_super_admin_global_blacklist_queue": (
            "open_queue"
        ),
        "ask_super_admin_global_blacklist_revoke": (
            "require_actor"
        ),
        "execute_super_admin_global_blacklist_revoke": (
            "unblock_user"
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

        assert (
            "SuperAdminGlobalBlacklistService"
            in block
        )
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block

def test_super_admin_global_blacklist_router_is_independent():
    from pathlib import Path

    handler_source = Path(
        "handlers/super_admin_global_blacklist.py"
    ).read_text(encoding="utf-8")
    admin_source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    bot_source = Path(
        "bot.py"
    ).read_text(encoding="utf-8")

    assert (
        "super_admin_global_blacklist_router = Router()"
        in handler_source
    )
    assert (
        "class SuperAdminGlobalBlacklistFSM"
        in handler_source
    )
    assert (
        "SuperAdminGlobalBlacklistService"
        in handler_source
    )

    forbidden = (
        "from handlers.admin import",
        "ModerationRepository(",
        "ModerationService(",
        "get_admin_user_context(",
        "AdminModerationFSM",
    )

    for marker in forbidden:
        assert marker not in handler_source

    moved_functions = (
        "format_global_blacklist_card",
        "format_super_admin_global_blacklist_screen",
        "super_admin_global_blacklist_card_keyboard",
        "super_admin_global_blacklist_queue_keyboard",
        "super_admin_global_blacklist_screen_keyboard",
        "open_super_admin_global_blacklist",
        "change_super_admin_global_blacklist_queue",
        "ask_super_admin_global_blacklist_add",
        "receive_super_admin_global_blacklist_add",
        "confirm_super_admin_global_blacklist_add_first",
        "execute_super_admin_global_blacklist_add",
        "cancel_super_admin_global_blacklist_add",
        "open_super_admin_global_blacklist_queue",
        "ask_super_admin_global_blacklist_revoke",
        "receive_super_admin_global_blacklist_revoke_reason",
        "confirm_super_admin_global_blacklist_revoke_first",
        "execute_super_admin_global_blacklist_revoke",
        "cancel_super_admin_global_blacklist_revoke",
        "super_admin_user_global_blacklist_alias",
    )

    for function_name in moved_functions:
        assert (
            f"def {function_name}("
            in handler_source
        )
        assert (
            f"def {function_name}("
            not in admin_source
        )

    assert (
        "from handlers.super_admin_global_blacklist "
        "import ("
        in bot_source
    )
    assert (
        "dp.include_router(\n"
        "        super_admin_global_blacklist_router\n"
        "    )"
        in bot_source
    )
