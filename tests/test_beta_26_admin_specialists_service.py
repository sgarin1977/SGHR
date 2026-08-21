from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_specialists import (
    AdminSpecialistsAccessError,
    AdminSpecialistsService,
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

    service = AdminSpecialistsService(
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
    service = AdminSpecialistsService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    with pytest.raises(
        AdminSpecialistsAccessError,
        match="access denied",
    ):
        await service.open_admin_specialists(
            platform_user_id=123,
        )

    assert not moderation.calls
    assert not moderation.role_calls


@pytest.mark.asyncio
async def test_unrelated_role_fails_closed():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"support"})

    with pytest.raises(
        AdminSpecialistsAccessError
    ):
        await service.open_pending_specialists(
            platform_user_id=123,
        )

    assert not moderation.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "roles",
        "service_method",
        "arguments",
        "moderation_method",
        "actor_argument",
    ),
    [
        (
            {"admin"},
            "open_admin_specialists",
            {
                "status": "hidden",
                "page": 2,
                "page_size": 4,
            },
            "open_admin_specialists",
            "admin_user_id",
        ),
        (
            {"moderator"},
            "open_pending_specialists",
            {
                "page": 1,
                "page_size": 6,
            },
            "open_pending_specialists_queue",
            "moderator_user_id",
        ),
        (
            {"moderator"},
            "get_specialist_card",
            {
                "specialist_id": uuid4(),
                "professional_cabinet_id": None,
            },
            "get_moderator_specialist_card",
            "moderator_user_id",
        ),
    ],
)
async def test_direct_operations_are_tenant_bound(
    roles,
    service_method,
    arguments,
    moderation_method,
    actor_argument,
):
    (
        service,
        users,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles=roles)

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=777,
        **arguments,
    )

    assert result == moderation_method
    assert users.requested_ids == [777]

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs[actor_argument] == actor_id
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

    with pytest.raises(
        AdminSpecialistsAccessError
    ):
        await (
            service.open_impersonated_admin_specialists(
                platform_user_id=123,
                effective_admin_user_id=uuid4(),
            )
        )

    assert not moderation.calls


@pytest.mark.asyncio
async def test_effective_role_is_required():
    (
        service,
        _,
        moderation,
        _,
        _,
    ) = build_service(roles={"super_admin"})

    effective_id = uuid4()
    moderation.roles_by_user[
        effective_id
    ] = {"support"}

    with pytest.raises(
        AdminSpecialistsAccessError,
        match="Impersonated",
    ):
        await (
            service.open_impersonated_moderator_queue(
                platform_user_id=123,
                effective_moderator_user_id=(
                    effective_id
                ),
            )
        )

    assert not moderation.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "effective_role",
        "service_method",
        "effective_argument",
        "arguments",
        "moderation_method",
        "actor_argument",
    ),
    [
        (
            "admin",
            "open_impersonated_admin_specialists",
            "effective_admin_user_id",
            {
                "status": "approved",
                "page": 1,
                "page_size": 3,
            },
            "open_admin_specialists",
            "admin_user_id",
        ),
        (
            "moderator",
            "open_impersonated_moderator_queue",
            "effective_moderator_user_id",
            {
                "page": 2,
                "page_size": 4,
            },
            "open_pending_specialists_queue",
            "moderator_user_id",
        ),
        (
            "admin",
            "get_impersonated_admin_specialist_card",
            "effective_admin_user_id",
            {
                "professional_cabinet_id": uuid4(),
            },
            "get_moderator_specialist_card",
            "moderator_user_id",
        ),
        (
            "moderator",
            "get_impersonated_moderator_specialist_card",
            "effective_moderator_user_id",
            {
                "professional_cabinet_id": uuid4(),
            },
            "get_moderator_specialist_card",
            "moderator_user_id",
        ),
    ],
)
async def test_impersonated_operations_use_effective_actor(
    effective_role,
    service_method,
    effective_argument,
    arguments,
    moderation_method,
    actor_argument,
):
    (
        service,
        _,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"super_admin"})

    effective_id = uuid4()
    moderation.roles_by_user[
        effective_id
    ] = {effective_role}

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=123,
        **{
            effective_argument: effective_id,
        },
        **arguments,
    )

    assert result == moderation_method
    assert moderation.role_calls == [
        {
            "user_id": actor_id,
            "tenant_id": tenant_id,
        },
        {
            "user_id": effective_id,
            "tenant_id": tenant_id,
        },
    ]

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs[actor_argument] == effective_id
    assert kwargs["tenant_id"] == tenant_id


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
            "approve_specialist",
            {
                "reason": "Approved",
                "professional_cabinet_id": uuid4(),
            },
            "approve_specialist",
            "admin_user_id",
        ),
        (
            "reject_specialist",
            {
                "reason": "Rejected",
                "specialist_id": uuid4(),
            },
            "reject_specialist",
            "admin_user_id",
        ),
        (
            "request_specialist_changes",
            {
                "reason": "Please update",
                "professional_cabinet_id": uuid4(),
            },
            "request_specialist_changes",
            "moderator_user_id",
        ),
        (
            "hide_professional_cabinet",
            {
                "reason": "Hidden",
                "professional_cabinet_id": uuid4(),
            },
            "hide_professional_cabinet",
            "admin_user_id",
        ),
        (
            "restore_professional_cabinet",
            {
                "reason": "Restored",
                "professional_cabinet_id": uuid4(),
            },
            "restore_professional_cabinet",
            "admin_user_id",
        ),
    ],
)
async def test_specialist_actions_are_tenant_bound(
    service_method,
    arguments,
    moderation_method,
    actor_argument,
):
    (
        service,
        _,
        moderation,
        actor_id,
        tenant_id,
    ) = build_service(roles={"moderator"})

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=123,
        **arguments,
    )

    assert result.result == moderation_method
    assert result.actor.user_id == actor_id
    assert result.actor.tenant_id == tenant_id

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs[actor_argument] == actor_id
    assert kwargs["tenant_id"] == tenant_id


def test_specialist_handlers_use_application_service():
    import ast
    from pathlib import Path

    sources = {
        "specialists": Path(
            "handlers/admin_specialists.py"
        ).read_text(encoding="utf-8"),
        "admin": Path(
            "handlers/admin.py"
        ).read_text(encoding="utf-8"),
    }
    trees = {
        name: ast.parse(source)
        for name, source in sources.items()
    }

    expected = {
        "open_admin_specialists_list": (
            "specialists",
            ("open_admin_specialists",),
        ),
        "open_admin_specialist_card": (
            "specialists",
            ("get_specialist_card",),
        ),
        "list_pending_profiles": (
            "specialists",
            ("open_pending_specialists",),
        ),
        "show_pending_specialist": (
            "specialists",
            ("get_specialist_card",),
        ),
        "ask_specialist_decision_reason": (
            "specialists",
            ("require_moderator_actor",),
        ),
        "ask_specialist_visibility_reason": (
            "specialists",
            ("require_moderator_actor",),
        ),
        "ask_specialist_changes_reason": (
            "specialists",
            ("require_moderator_actor",),
        ),
        "confirm_specialist_visibility": (
            "specialists",
            (
                "hide_professional_cabinet",
                "restore_professional_cabinet",
            ),
        ),
        "confirm_specialist_decision": (
            "specialists",
            (
                "approve_specialist",
                "reject_specialist",
            ),
        ),
        "confirm_specialist_changes": (
            "specialists",
            ("request_specialist_changes",),
        ),
    }

    for (
        function_name,
        (source_name, service_methods),
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

        assert "AdminSpecialistsService(" in block

        for service_method in service_methods:
            assert service_method in block

        assert "ModerationService(" not in block
        assert "ModerationRepository(" not in block
        assert "get_admin_user_context(" not in block


def test_impersonated_specialist_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_impersonation.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "super_admin_read_only_admin_specialists": (
            "open_admin_specialists"
        ),
        "super_admin_read_only_moderator_queue": (
            "open_moderator_queue"
        ),
        "super_admin_read_only_moderator_profile": (
            "get_moderator_specialist"
        ),
        "super_admin_read_only_admin_specialist_open": (
            "get_admin_specialist"
        ),
    }

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

        assert "AdminImpersonationService(" in block
        assert "AdminSpecialistsService(" not in block
        assert service_method in block
        assert "ModerationService(" not in block
        assert "ModerationRepository(" not in block


def test_specialist_filter_entry_uses_application_service():
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
        == "open_admin_specialist_filter"
    )
    block = ast.get_source_segment(
        source,
        node,
    )

    assert "AdminSpecialistsService(" in block
    assert "require_admin_actor" in block
    assert "get_admin_user_context(" not in block
    assert "ModerationRepository(" not in block
    assert "ModerationService(" not in block

    assert "replace_admin_callback_screen(" in block
    assert "admin_specialist_filter_keyboard(" in block
