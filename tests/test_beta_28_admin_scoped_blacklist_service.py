from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_scoped_blacklist import (
    AdminScopedBlacklistAccessError,
    AdminScopedBlacklistService,
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
    users = FakeUsers(
        SimpleNamespace(
            id=actor_id,
            tenant_id=tenant_id,
        )
    )
    moderation = FakeModeration()
    moderation.roles_by_user[
        actor_id
    ] = set(roles)

    service = AdminScopedBlacklistService(
        SimpleNamespace(),
        users=users,
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
    service = AdminScopedBlacklistService(
        SimpleNamespace(),
        users=FakeUsers(user),
        moderation=moderation,
    )

    with pytest.raises(
        AdminScopedBlacklistAccessError,
        match="access denied",
    ):
        await service.open_queue(
            platform_user_id=123,
            view="active",
        )

    assert not moderation.role_calls
    assert not moderation.calls


@pytest.mark.asyncio
async def test_unrelated_role_fails_closed():
    (
        service,
        moderation,
        _,
        _,
    ) = build_service(roles={"support"})

    with pytest.raises(
        AdminScopedBlacklistAccessError
    ):
        await service.open_queue(
            platform_user_id=123,
            view="active",
        )

    assert not moderation.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        "admin",
        "moderator",
        "super_admin",
    ],
)
async def test_moderation_roles_are_allowed(role):
    (
        service,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={role})

    result = await service.open_queue(
        platform_user_id=123,
        view="revoked",
        page=2,
        page_size=4,
    )

    assert result == (
        "open_scoped_blacklist_queue"
    )
    assert moderation.role_calls == [
        {
            "user_id": actor_id,
            "tenant_id": tenant_id,
        }
    ]
    assert moderation.calls == [
        (
            "open_scoped_blacklist_queue",
            {
                "moderator_user_id": actor_id,
                "tenant_id": tenant_id,
                "view": "revoked",
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
        "actor_argument",
    ),
    [
        (
            "add_by_telegram_id",
            {
                "telegram_id": "123456",
                "reason": "Manual tenant block",
            },
            "add_scoped_blacklist_by_telegram_id",
            "moderator_user_id",
        ),
        (
            "add_specialist_owner",
            {
                "specialist_id": uuid4(),
                "reason": "Specialist violation",
                "comment": "Cabinet moderation",
            },
            "add_specialist_owner_scoped_blacklist",
            "moderator_user_id",
        ),
        (
            "add_complaint_target",
            {
                "complaint_id": uuid4(),
                "reason": "Complaint confirmed",
            },
            "add_complaint_target_scoped_blacklist",
            "moderator_user_id",
        ),
        (
            "revoke",
            {
                "blacklist_id": uuid4(),
                "reason": "Restriction removed",
            },
            "revoke_scoped_blacklist",
            "moderator_user_id",
        ),
    ],
)
async def test_actions_are_tenant_bound(
    service_method,
    arguments,
    moderation_method,
    actor_argument,
):
    (
        service,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"moderator"})

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
    assert kwargs[actor_argument] == actor_id
    assert kwargs["tenant_id"] == tenant_id

    for key, value in arguments.items():
        assert kwargs[key] == value


@pytest.mark.asyncio
async def test_impersonation_requires_super_admin():
    (
        service,
        moderation,
        _,
        _,
    ) = build_service(roles={"admin"})

    with pytest.raises(
        AdminScopedBlacklistAccessError
    ):
        await service.open_impersonated_queue(
            platform_user_id=123,
            effective_user_id=uuid4(),
            view="active",
        )

    assert not moderation.calls


@pytest.mark.asyncio
async def test_impersonation_rejects_unrelated_target():
    (
        service,
        moderation,
        _,
        _,
    ) = build_service(roles={"super_admin"})
    effective_user_id = uuid4()
    moderation.roles_by_user[
        effective_user_id
    ] = {"support"}

    with pytest.raises(
        AdminScopedBlacklistAccessError,
        match="Impersonated",
    ):
        await service.open_impersonated_queue(
            platform_user_id=123,
            effective_user_id=effective_user_id,
            view="active",
        )

    assert not moderation.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effective_role",
    [
        "admin",
        "moderator",
    ],
)
async def test_impersonated_queue_is_tenant_bound(
    effective_role,
):
    (
        service,
        moderation,
        _,
        tenant_id,
    ) = build_service(roles={"super_admin"})
    effective_user_id = uuid4()
    moderation.roles_by_user[
        effective_user_id
    ] = {effective_role}

    result = await service.open_impersonated_queue(
        platform_user_id=123,
        effective_user_id=effective_user_id,
        view="revoked",
        page=3,
        page_size=5,
    )

    assert result == (
        "open_scoped_blacklist_queue"
    )
    assert moderation.role_calls[-1] == {
        "user_id": effective_user_id,
        "tenant_id": tenant_id,
    }
    assert moderation.calls == [
        (
            "open_scoped_blacklist_queue",
            {
                "moderator_user_id": (
                    effective_user_id
                ),
                "tenant_id": tenant_id,
                "view": "revoked",
                "page": 3,
                "page_size": 5,
            },
        )
    ]


def test_scoped_blacklist_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_scoped_blacklist.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "open_scoped_blacklist_queue": (
            "open_queue"
        ),
        "super_admin_read_only_moderator_blacklist": (
            "open_impersonated_queue"
        ),
        "confirm_blacklist_add": (
            "add_by_telegram_id"
        ),
        "confirm_specialist_scoped_block": (
            "add_specialist_owner"
        ),
        "confirm_scoped_blacklist_revoke": (
            "revoke"
        ),
        "confirm_complaint_scoped_block": (
            "add_complaint_target"
        ),
        "ask_blacklist_add_user": (
            "require_moderator_actor"
        ),
        "ask_specialist_scoped_block_reason": (
            "require_moderator_actor"
        ),
        "ask_complaint_scoped_block_reason": (
            "require_moderator_actor"
        ),
        "ask_scoped_blacklist_revoke_reason": (
            "require_moderator_actor"
        ),
    }

    for function_name, service_method in (
        expected.items()
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
        block = "\n".join(
            lines[
                node.lineno - 1:
                node.end_lineno
            ]
        )

        assert (
            "AdminScopedBlacklistService("
            in block
        )
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block
