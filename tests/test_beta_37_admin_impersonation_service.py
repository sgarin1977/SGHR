from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_impersonation import (
    AdminImpersonationAccessError,
    AdminImpersonationService,
)


class FakeUsers:
    def __init__(self, actor):
        self.actor = actor
        self.targets = {}
        self.platform_calls = []
        self.target_calls = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.platform_calls.append(
            platform_user_id
        )
        return self.actor

    async def get_user_by_id(
        self,
        user_id,
    ):
        self.target_calls.append(user_id)
        return self.targets.get(user_id)


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
    target_user_id = uuid4()

    actor = SimpleNamespace(
        id=actor_id,
        tenant_id=tenant_id,
    )
    target = SimpleNamespace(
        id=target_user_id,
        tenant_id=tenant_id,
    )

    users = FakeUsers(actor)
    users.targets[target_user_id] = target

    moderation = FakeModeration()
    moderation.roles_by_user[actor_id] = set(
        roles
    )

    service = AdminImpersonationService(
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
        target_user_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor",
    [
        None,
        SimpleNamespace(
            id=uuid4(),
            tenant_id=None,
        ),
    ],
)
async def test_missing_actor_fails_closed(actor):
    users = FakeUsers(actor)
    moderation = FakeModeration()
    service = AdminImpersonationService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
    )

    with pytest.raises(
        AdminImpersonationAccessError,
        match="access denied",
    ):
        await service.start_view(
            platform_user_id=123,
            target_user_id=uuid4(),
            target_role="client",
            reason="test",
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
        target_user_id,
    ) = build_service(roles={"admin"})

    with pytest.raises(
        AdminImpersonationAccessError
    ):
        await service.start_view(
            platform_user_id=123,
            target_user_id=target_user_id,
            target_role="client",
            reason="test",
        )

    assert not moderation.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments",
    ),
    [
        (
            "start_view",
            {
                "target_role": "client",
                "reason": "start test",
            },
        ),
        (
            "stop_view",
            {
                "reason": "stop test",
            },
        ),
    ],
)
async def test_foreign_target_fails_closed(
    service_method,
    arguments,
):
    (
        service,
        users,
        moderation,
        _,
        _,
        target_user_id,
    ) = build_service(roles={"super_admin"})

    users.targets[target_user_id] = (
        SimpleNamespace(
            id=target_user_id,
            tenant_id=uuid4(),
        )
    )

    with pytest.raises(
        AdminImpersonationAccessError,
        match="target access denied",
    ):
        await getattr(
            service,
            service_method,
        )(
            platform_user_id=123,
            target_user_id=target_user_id,
            **arguments,
        )

    assert users.target_calls == [target_user_id]
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
            "start_view",
            {
                "target_role": "specialist",
                "reason": "start test",
            },
            "start_super_admin_impersonation_view",
        ),
        (
            "stop_view",
            {
                "reason": "stop test",
            },
            "stop_super_admin_impersonation_view",
        ),
    ],
)
async def test_impersonation_actions_are_tenant_bound(
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
        target_user_id,
    ) = build_service(roles={"super_admin"})

    action = await getattr(
        service,
        service_method,
    )(
        platform_user_id=987,
        target_user_id=target_user_id,
        **arguments,
    )

    assert action.actor.user_id == actor_id
    assert action.actor.tenant_id == tenant_id
    assert action.result == moderation_method
    assert users.platform_calls == [987]
    assert users.target_calls == [target_user_id]

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs["admin_user_id"] == actor_id
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["target_user_id"] == target_user_id

    for key, value in arguments.items():
        assert kwargs[key] == value

def test_impersonation_mutation_handlers_use_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_impersonation.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "super_admin_impersonation_role": (
            "start_view"
        ),
        "super_admin_impersonation_stop": (
            "stop_view"
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

        assert "AdminImpersonationService" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block

class FakeProfiles:
    def __init__(self):
        self.calls = []

    async def get_active_cabinet_profile(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return "specialist-profile"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments",
        "moderation_method",
    ),
    [
        (
            "get_client_cabinet",
            {"language": "uk"},
            "get_client_read_only_cabinet",
        ),
        (
            "list_specialist_cabinets",
            {"language": "pt"},
            (
                "list_specialist_"
                "read_only_cabinet_options"
            ),
        ),
        (
            "get_specialist_cabinet",
            {
                "language": "ru",
                "professional_cabinet_id": uuid4(),
            },
            "get_specialist_read_only_cabinet",
        ),
    ],
)
async def test_read_only_cabinets_are_tenant_bound(
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
        target_user_id,
    ) = build_service(roles={"super_admin"})

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=987,
        target_user_id=target_user_id,
        **arguments,
    )

    assert result == moderation_method
    assert users.target_calls == [target_user_id]

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs["admin_user_id"] == actor_id
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["target_user_id"] == target_user_id

    for key, value in arguments.items():
        assert kwargs[key] == value


@pytest.mark.asyncio
async def test_specialist_profile_is_tenant_bound():
    (
        service,
        users,
        _,
        _,
        tenant_id,
        target_user_id,
    ) = build_service(roles={"super_admin"})
    profiles = FakeProfiles()
    service.profiles = profiles

    specialist_id = uuid4()
    cabinet_id = uuid4()

    result = await service.get_specialist_profile(
        platform_user_id=123,
        target_user_id=target_user_id,
        specialist_id=specialist_id,
        language="de",
        professional_cabinet_id=cabinet_id,
    )

    assert result == "specialist-profile"
    assert users.target_calls == [target_user_id]
    assert profiles.calls == [
        {
            "tenant_id": tenant_id,
            "user_id": target_user_id,
            "specialist_id": specialist_id,
            "language": "de",
            "professional_cabinet_id": cabinet_id,
        }
    ]


@pytest.mark.asyncio
async def test_foreign_target_cannot_open_cabinet():
    (
        service,
        users,
        moderation,
        _,
        _,
        target_user_id,
    ) = build_service(roles={"super_admin"})

    users.targets[target_user_id] = SimpleNamespace(
        id=target_user_id,
        tenant_id=uuid4(),
    )

    with pytest.raises(
        AdminImpersonationAccessError
    ):
        await service.get_client_cabinet(
            platform_user_id=123,
            target_user_id=target_user_id,
            language="ru",
        )

    assert not moderation.calls

def test_client_specialist_preview_handlers_use_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_impersonation.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "show_super_admin_client_read_only_cabinet": (
            "get_client_cabinet"
        ),
        (
            "show_super_admin_specialist_"
            "read_only_cabinets"
        ): "list_specialist_cabinets",
        (
            "show_super_admin_specialist_"
            "read_only_cabinet"
        ): "get_specialist_cabinet",
        (
            "super_admin_read_only_"
            "specialist_profile"
        ): "get_specialist_profile",
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

        assert "AdminImpersonationService" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block
        assert "SpecialistRepository(" not in block
        assert "SpecialistService(" not in block

class FakeAdminSpecialists:
    def __init__(self):
        self.calls = []

    def __getattr__(self, method_name):
        async def method(**kwargs):
            self.calls.append(
                (method_name, kwargs)
            )
            return method_name

        return method


@pytest.mark.asyncio
async def test_admin_cabinet_is_tenant_bound():
    (
        service,
        _,
        moderation,
        _,
        tenant_id,
        target_user_id,
    ) = build_service(roles={"super_admin"})

    result = await service.open_admin_cabinet(
        platform_user_id=123,
        target_user_id=target_user_id,
    )

    assert result == "open_admin_menu"

    method, kwargs = moderation.calls[-1]

    assert method == "open_admin_menu"
    assert kwargs == {
        "admin_user_id": target_user_id,
        "tenant_id": tenant_id,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments_factory",
        "delegated_method",
    ),
    [
        (
            "require_moderator_preview",
            lambda target_id, cabinet_id: {
                "target_user_id": target_id,
                "target_role": "moderator",
            },
            "require_impersonated_actor",
        ),
        (
            "open_admin_specialists",
            lambda target_id, cabinet_id: {
                "effective_admin_user_id": target_id,
                "status": "all",
                "page": 2,
                "page_size": 5,
            },
            "open_impersonated_admin_specialists",
        ),
        (
            "open_moderator_queue",
            lambda target_id, cabinet_id: {
                "effective_moderator_user_id": target_id,
                "page": 1,
                "page_size": 5,
            },
            "open_impersonated_moderator_queue",
        ),
        (
            "get_admin_specialist",
            lambda target_id, cabinet_id: {
                "effective_admin_user_id": target_id,
                "professional_cabinet_id": cabinet_id,
            },
            "get_impersonated_admin_specialist_card",
        ),
        (
            "get_moderator_specialist",
            lambda target_id, cabinet_id: {
                "effective_moderator_user_id": target_id,
                "professional_cabinet_id": cabinet_id,
            },
            (
                "get_impersonated_"
                "moderator_specialist_card"
            ),
        ),
    ],
)
async def test_admin_moderator_operations_are_checked(
    service_method,
    arguments_factory,
    delegated_method,
):
    (
        service,
        users,
        _,
        _,
        _,
        target_user_id,
    ) = build_service(roles={"super_admin"})
    admin_specialists = FakeAdminSpecialists()
    service.admin_specialists = admin_specialists
    cabinet_id = uuid4()

    arguments = arguments_factory(
        target_user_id,
        cabinet_id,
    )

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=987,
        **arguments,
    )

    assert users.target_calls == [target_user_id]

    method, kwargs = (
        admin_specialists.calls[-1]
    )

    assert method == delegated_method
    assert kwargs["platform_user_id"] == 987

    if (
        service_method
        == "require_moderator_preview"
    ):
        assert result.user_id is not None
        assert kwargs["effective_user_id"] == (
            target_user_id
        )
        assert kwargs[
            "required_effective_roles"
        ] == {"moderator"}
    else:
        assert result == delegated_method

        for key, value in arguments.items():
            assert kwargs[key] == value

def test_admin_moderator_preview_handlers_use_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_impersonation.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "show_super_admin_moderator_read_only_cabinet": (
            "require_moderator_preview"
        ),
        "show_super_admin_admin_read_only_cabinet": (
            "open_admin_cabinet"
        ),
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
            item
            for item in ast.walk(tree)
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name == function_name
        )
        block = ast.get_source_segment(
            source,
            node,
        )

        assert "AdminImpersonationService" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block
        assert "AdminSpecialistsService(" not in block
