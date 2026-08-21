from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.admin_dialogs import (
    AdminDialogsAccessError,
    AdminDialogsService,
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


class FakeContacts:
    def __init__(self):
        self.calls = []
        self.thread_items = [
            SimpleNamespace(number=index)
            for index in range(6)
        ]

    async def list_client_threads(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("list_client_threads", kwargs)
        )
        return list(self.thread_items)

    async def list_specialist_threads(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("list_specialist_threads", kwargs)
        )
        return list(self.thread_items)

    async def get_thread_detail(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("get_thread_detail", kwargs)
        )
        return "thread-detail"


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
    contacts = FakeContacts()

    service = AdminDialogsService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
        contacts=contacts,
    )

    return (
        service,
        users,
        moderation,
        contacts,
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
    contacts = FakeContacts()

    service = AdminDialogsService(
        SimpleNamespace(),
        users=users,
        moderation=moderation,
        contacts=contacts,
    )

    with pytest.raises(
        AdminDialogsAccessError,
        match="access denied",
    ):
        await service.list_admin_contexts(
            platform_user_id=123
        )

    assert not moderation.role_calls
    assert not moderation.calls
    assert not contacts.calls


@pytest.mark.asyncio
async def test_non_dialog_role_fails_closed():
    (
        service,
        _,
        moderation,
        contacts,
        _,
        _,
        _,
    ) = build_service(roles={"support"})

    with pytest.raises(AdminDialogsAccessError):
        await service.list_admin_contexts(
            platform_user_id=123
        )

    assert not moderation.calls
    assert not contacts.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_method",
        "arguments",
        "moderation_method",
    ),
    [
        (
            "list_admin_contexts",
            {},
            "open_admin_thread_contexts",
        ),
        (
            "get_admin_thread_messages",
            {"thread_id": uuid4()},
            "open_admin_thread_messages",
        ),
    ],
)
async def test_admin_dialog_reads_are_tenant_bound(
    service_method,
    arguments,
    moderation_method,
):
    (
        service,
        users,
        moderation,
        contacts,
        actor_id,
        tenant_id,
        _,
    ) = build_service(roles={"admin"})

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=987,
        **arguments,
    )

    assert result == moderation_method
    assert users.platform_calls == [987]
    assert not contacts.calls

    called_method, kwargs = moderation.calls[-1]

    assert called_method == moderation_method
    assert kwargs["admin_user_id"] == actor_id
    assert kwargs["tenant_id"] == tenant_id

    for key, value in arguments.items():
        assert kwargs[key] == value


@pytest.mark.asyncio
async def test_foreign_tenant_target_fails_closed():
    (
        service,
        users,
        _,
        contacts,
        _,
        _,
        target_user_id,
    ) = build_service(roles={"super_admin"})

    users.targets[target_user_id] = SimpleNamespace(
        id=target_user_id,
        tenant_id=uuid4(),
    )

    with pytest.raises(
        AdminDialogsAccessError,
        match="Impersonated",
    ):
        await service.list_impersonated_client_threads(
            platform_user_id=123,
            target_user_id=target_user_id,
            page=0,
        )

    assert users.target_calls == [target_user_id]
    assert not contacts.calls


@pytest.mark.asyncio
async def test_client_thread_page_is_bounded():
    (
        service,
        _,
        _,
        contacts,
        _,
        _,
        target_user_id,
    ) = build_service(roles={"super_admin"})

    result = (
        await service.list_impersonated_client_threads(
            platform_user_id=123,
            target_user_id=target_user_id,
            page=2,
            page_size=5,
            language="uk",
        )
    )

    assert len(result.items) == 5
    assert result.page == 2
    assert result.has_next is True

    method, kwargs = contacts.calls[-1]

    assert method == "list_client_threads"
    assert kwargs == {
        "user_id": target_user_id,
        "view": "active",
        "limit": 6,
        "offset": 10,
        "language": "uk",
    }


@pytest.mark.asyncio
async def test_specialist_thread_page_keeps_cabinet():
    (
        service,
        _,
        _,
        contacts,
        _,
        _,
        target_user_id,
    ) = build_service(roles={"super_admin"})
    cabinet_id = uuid4()

    result = await (
        service.list_impersonated_specialist_threads(
            platform_user_id=123,
            target_user_id=target_user_id,
            professional_cabinet_id=cabinet_id,
            page=1,
            page_size=5,
            language="ru",
        )
    )

    assert len(result.items) == 5
    assert result.page == 1
    assert result.has_next is True

    method, kwargs = contacts.calls[-1]

    assert method == "list_specialist_threads"
    assert kwargs[
        "professional_cabinet_id"
    ] == cabinet_id
    assert kwargs["offset"] == 5
    assert kwargs["limit"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_method",
    [
        "get_impersonated_client_thread",
        "get_impersonated_specialist_thread",
    ],
)
async def test_impersonated_thread_detail_is_checked(
    service_method,
):
    (
        service,
        users,
        _,
        contacts,
        _,
        _,
        target_user_id,
    ) = build_service(roles={"super_admin"})
    thread_id = uuid4()

    result = await getattr(
        service,
        service_method,
    )(
        platform_user_id=321,
        target_user_id=target_user_id,
        thread_id=thread_id,
        language="pt",
    )

    assert result == "thread-detail"
    assert users.target_calls == [target_user_id]

    method, kwargs = contacts.calls[-1]

    assert method == "get_thread_detail"
    assert kwargs == {
        "thread_id": thread_id,
        "user_id": target_user_id,
        "language": "pt",
    }

def test_regular_admin_dialog_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "show_admin_dialog_contexts": (
            "list_admin_contexts"
        ),
        "open_admin_dialog_thread": (
            "get_admin_thread_messages"
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

        assert "AdminDialogsService" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ModerationRepository(" not in block
        assert "ModerationService(" not in block

def test_impersonated_client_dialog_handlers_use_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "super_admin_read_only_client_dialogs": (
            "list_impersonated_client_threads"
        ),
        "super_admin_read_only_client_dialog_open": (
            "get_impersonated_client_thread"
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

        assert "AdminDialogsService" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ContactChatRepository(" not in block
        assert "ContactChatService(" not in block

def test_impersonated_specialist_dialog_handlers_use_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "super_admin_read_only_specialist_dialogs": (
            "list_impersonated_specialist_threads"
        ),
        "super_admin_read_only_specialist_dialog_open": (
            "get_impersonated_specialist_thread"
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

        assert "AdminDialogsService" in block
        assert service_method in block
        assert "get_admin_user_context(" not in block
        assert "ContactChatRepository(" not in block
        assert "ContactChatService(" not in block
