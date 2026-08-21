from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_dialogs import (
    UserDialogsAccessError,
    UserDialogsService,
    UserDialogsThreadError,
)
from services.user_settings import (
    UserSettingsNotFoundError,
)


class FakeSettings:
    def __init__(
        self,
        context=None,
        error=None,
    ):
        self.context = context
        self.error = error

    async def get_context(self, **kwargs):
        if self.error:
            raise self.error
        return self.context


class FakeUsers:
    def __init__(self, roles=None):
        self.roles = roles or ["client"]
        self.calls = []

    async def get_role_switch_context(
        self,
        platform_user_id,
    ):
        self.calls.append(platform_user_id)
        return SimpleNamespace(
            available_roles=self.roles
        )


class FakeChats:
    def __init__(self):
        self.calls = []
        self.specialist_items = list(
            range(6)
        )
        self.client_items = list(range(5))

    async def list_specialist_threads(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("specialist_list", kwargs)
        )
        return self.specialist_items

    async def list_client_threads(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("client_list", kwargs)
        )
        return self.client_items

    async def count_unread_messages(
        self,
        **kwargs,
    ):
        self.calls.append(("unread", kwargs))
        return 4

    async def record_messages_opened(
        self,
        **kwargs,
    ):
        self.calls.append(("opened", kwargs))

    async def get_thread_detail_for_viewer(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("specialist_detail", kwargs)
        )
        return "specialist-detail"

    async def get_thread_detail(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("client_detail", kwargs)
        )
        return "client-detail"

    async def mark_thread_read(
        self,
        **kwargs,
    ):
        self.calls.append(("read", kwargs))


def context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


def build_service(
    *,
    current_context=None,
    settings_error=None,
    roles=None,
):
    chats = FakeChats()
    users = FakeUsers(roles)
    service = UserDialogsService(
        object(),
        settings=FakeSettings(
            current_context,
            settings_error,
        ),
        users=users,
        chats=chats,
    )
    return service, users, chats


@pytest.mark.asyncio
async def test_missing_actor_fails_closed():
    service, _, chats = build_service(
        settings_error=UserSettingsNotFoundError()
    )

    with pytest.raises(
        UserDialogsAccessError
    ):
        await service.list_client_dialogs(
            platform_user_id=123
        )

    assert chats.calls == []


@pytest.mark.asyncio
async def test_specialist_dialog_list_uses_actor_scope():
    current = context()
    service, _, chats = build_service(
        current_context=current
    )

    result = await service.list_specialist_dialogs(
        platform_user_id=123,
        view="archive",
        page=2,
        page_size=5,
        search_query="query",
    )

    assert result.items == list(range(5))
    assert result.has_next is True
    assert result.unread_messages == 4
    assert result.actor.user_id == current.user_id

    assert chats.calls[0] == (
        "specialist_list",
        {
            "user_id": current.user_id,
            "view": "archive",
            "limit": 6,
            "offset": 10,
            "language": "uk",
            "search_query": "query",
        },
    )
    assert chats.calls[2][1] == {
        "tenant_id": current.tenant_id,
        "user_id": current.user_id,
        "participant_role": "specialist",
        "view": "archive",
        "page": 2,
    }


@pytest.mark.asyncio
async def test_client_dialog_list_uses_actor_scope():
    current = context()
    service, users, chats = build_service(
        current_context=current,
        roles=["client", "specialist"],
    )

    result = await service.list_client_dialogs(
        platform_user_id=456,
        view="completed",
        page=1,
        page_size=5,
    )

    assert result.items == list(range(5))
    assert result.has_next is True
    assert result.show_role_switch is True
    assert users.calls == [456]

    assert chats.calls[0][1] == {
        "user_id": current.user_id,
        "view": "completed",
        "limit": 5,
        "offset": 5,
        "language": "uk",
        "search_query": None,
    }
    assert chats.calls[2][1][
        "items_count"
    ] == 5


@pytest.mark.asyncio
async def test_specialist_detail_validates_actor():
    current = context()
    service, _, chats = build_service(
        current_context=current
    )
    thread_id = uuid4()

    result = await service.get_specialist_dialog(
        platform_user_id=123,
        thread_id=str(thread_id),
    )

    assert result.detail == "specialist-detail"
    assert chats.calls[0][1] == {
        "tenant_id": current.tenant_id,
        "thread_id": thread_id,
        "user_id": current.user_id,
        "participant_role": "specialist",
        "language": "uk",
    }
    assert chats.calls[1][1] == {
        "thread_id": thread_id,
        "user_id": current.user_id,
    }


@pytest.mark.asyncio
async def test_client_detail_validates_actor():
    current = context()
    service, _, chats = build_service(
        current_context=current
    )
    thread_id = uuid4()

    result = await service.get_client_dialog(
        platform_user_id=456,
        thread_id=thread_id,
    )

    assert result.detail == "client-detail"
    assert chats.calls[0][1] == {
        "thread_id": thread_id,
        "user_id": current.user_id,
        "language": "uk",
    }


@pytest.mark.asyncio
async def test_invalid_thread_id_fails_before_read():
    service, _, chats = build_service(
        current_context=context()
    )

    with pytest.raises(
        UserDialogsThreadError
    ):
        await service.get_client_dialog(
            platform_user_id=123,
            thread_id="invalid",
        )

    assert chats.calls == []

def test_dialog_list_handlers_use_application_service():
    import ast

    source = open(
        "handlers/user_dialogs.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    expected = {
        "show_specialist_dialogs": (
            "list_specialist_dialogs"
        ),
        "show_client_dialogs": (
            "list_client_dialogs"
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

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Name,
            )
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert "UserDialogsService" in (
            called_names
        )
        assert service_method in called_methods
        assert (
            "ContactChatRepository"
            not in called_names
        )
        assert (
            "ContactChatService"
            not in called_names
        )
        assert "UserService" not in (
            called_names
        )
        assert (
            "get_billing_user_context"
            not in called_names
        )
        assert (
            "get_billing_interface_language"
            not in called_names
        )


def test_dialog_detail_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/user_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "send_specialist_thread_detail": (
            "get_specialist_dialog"
        ),
        "send_client_thread_detail": (
            "get_client_dialog"
        ),
    }

    for function_name, service_method in (
        expected.items()
    ):
        matches = [
            node
            for node in tree.body
            if isinstance(
                node,
                ast.AsyncFunctionDef,
            )
            and node.name == function_name
        ]
        assert len(matches) == 1

        node = matches[0]
        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "UserDialogsService"
            in called_names
        )
        assert service_method in called_methods

        forbidden_calls = {
            "ContactChatRepository",
            "ContactChatService",
            "get_billing_user_context",
        }
        assert not (
            called_names & forbidden_calls
        )

        attributes = {
            item.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Attribute)
        }
        assert "actor" in attributes
        assert "detail" in attributes
        assert "language" in attributes


@pytest.mark.asyncio
async def test_finish_dialog_uses_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogCompletion,
        UserDialogsActor,
        UserDialogsService,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="en",
    )
    thread_id = uuid4()
    finish_result = SimpleNamespace(
        thread_id=thread_id,
        action="requested",
    )

    class FakeChats:
        def __init__(self):
            self.calls = []

        async def finish_thread(self, **kwargs):
            self.calls.append(kwargs)
            return finish_result

    chats = FakeChats()
    service = object.__new__(
        UserDialogsService
    )
    service.chats = chats

    async def require_actor(
        *,
        platform_user_id,
    ):
        assert platform_user_id == 456
        return actor

    service.require_actor = require_actor

    result = await service.finish_dialog(
        platform_user_id=456,
        thread_id=str(thread_id),
    )

    assert isinstance(
        result,
        UserDialogCompletion,
    )
    assert result.actor is actor
    assert result.result is finish_result
    assert chats.calls == [
        {
            "tenant_id": actor.tenant_id,
            "thread_id": thread_id,
            "actor_user_id": actor.user_id,
        }
    ]


@pytest.mark.asyncio
async def test_finish_dialog_resolves_notification_recipient():
    current = context()
    receiver_user_id = uuid4()
    thread_id = uuid4()
    account = SimpleNamespace(
        platform_user_id="987654"
    )
    finish_result = SimpleNamespace(
        thread_id=thread_id,
        action="requested",
        requested_for_user_id=(
            receiver_user_id
        ),
    )

    class CompletionChats(FakeChats):
        async def finish_thread(
            self,
            **kwargs,
        ):
            self.calls.append(
                ("finish", kwargs)
            )
            return finish_result

    class FakeUserRepository:
        def __init__(self):
            self.calls = []

        async def get_telegram_account_by_user_id(
            self,
            user_id,
        ):
            self.calls.append(
                ("account", user_id)
            )
            return account

        async def get_language_code(
            self,
            user_id,
        ):
            self.calls.append(
                ("language", user_id)
            )
            return "uk"

    chats = CompletionChats()
    user_repository = FakeUserRepository()

    service = UserDialogsService(
        object(),
        settings=FakeSettings(current),
        users=FakeUsers(),
        chats=chats,
        user_repository=user_repository,
    )

    completion = await service.finish_dialog(
        platform_user_id=123,
        thread_id=str(thread_id),
    )

    assert completion.actor.user_id == (
        current.user_id
    )
    assert completion.result is finish_result
    assert completion.receiver_chat_id == (
        "987654"
    )
    assert completion.receiver_language == "uk"

    assert chats.calls[-1] == (
        "finish",
        {
            "tenant_id": current.tenant_id,
            "thread_id": thread_id,
            "actor_user_id": current.user_id,
        },
    )
    assert user_repository.calls == [
        ("account", receiver_user_id),
        ("language", receiver_user_id),
    ]


def test_dialog_completion_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/user_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = (
        "finish_thread_from_chat",
        (
            "confirm_thread_completion_"
            "from_notification"
        ),
    )

    for function_name in function_names:
        matches = [
            node
            for node in tree.body
            if isinstance(
                node,
                ast.AsyncFunctionDef,
            )
            and node.name == function_name
        ]
        assert len(matches) == 1
        node = matches[0]

        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
        }
        called_methods = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(
                call.func,
                ast.Attribute,
            )
        }

        assert (
            "UserDialogsService"
            in called_names
        )
        assert "finish_dialog" in called_methods

        forbidden = {
            "ContactChatRepository",
            "ContactChatService",
            "UserRepository",
            "get_billing_user_context",
        }
        assert not (
            called_names & forbidden
        )

        block = ast.get_source_segment(
            source,
            node,
        ) or ""
        assert "completion.result" in block
        assert "completion.actor.language" in block

    finish_node = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "finish_thread_from_chat"
    )
    finish_block = ast.get_source_segment(
        source,
        finish_node,
    ) or ""

    assert (
        "completion.receiver_chat_id"
        in finish_block
    )
    assert (
        "completion.receiver_language"
        in finish_block
    )


@pytest.mark.asyncio
async def test_resolve_complaint_target_uses_actor_scope():
    current = context()
    thread_id = uuid4()
    target_id = uuid4()
    conversation_thread_id = uuid4()

    class FakeModeration:
        def __init__(self):
            self.calls = []

        async def resolve_thread_complaint_target(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return (
                "user",
                target_id,
                conversation_thread_id,
            )

    moderation = FakeModeration()

    service = UserDialogsService(
        object(),
        settings=FakeSettings(current),
        users=FakeUsers(),
        chats=FakeChats(),
        moderation=moderation,
    )

    result = await (
        service.resolve_complaint_target(
            platform_user_id=321,
            thread_id=str(thread_id),
        )
    )

    assert result.actor.user_id == (
        current.user_id
    )
    assert result.target_type == "user"
    assert result.target_id == target_id
    assert (
        result.conversation_thread_id
        == conversation_thread_id
    )

    assert moderation.calls == [
        {
            "tenant_id": current.tenant_id,
            "reporter_user_id": (
                current.user_id
            ),
            "thread_id": thread_id,
        }
    ]


def test_dialog_report_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/user_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    matches = [
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "report_specialist_thread"
    ]
    assert len(matches) == 1
    node = matches[0]

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert "UserDialogsService" in called_names
    assert (
        "resolve_complaint_target"
        in called_methods
    )

    forbidden = {
        "ModerationRepository",
        "ModerationService",
        "get_billing_user_context",
        "UUID",
    }
    assert not (
        called_names & forbidden
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "target.actor.language" in block
    assert "target.target_type" in block
    assert "target.target_id" in block
    assert (
        "target.conversation_thread_id"
        in block
    )


@pytest.mark.asyncio
async def test_client_dialog_search_uses_actor_scope():
    current = context()
    service, _, chats = build_service(
        current_context=current
    )

    result = await service.search_dialogs(
        platform_user_id=123,
        role="client",
        view="archive",
        search_query="  hello  ",
        page_size=5,
    )

    assert result.actor.user_id == (
        current.user_id
    )
    assert result.role == "client"
    assert result.view == "archive"
    assert result.items == list(range(5))
    assert result.unread_messages == 4
    assert result.has_next is False

    assert chats.calls == [
        (
            "client_list",
            {
                "user_id": current.user_id,
                "view": "archive",
                "limit": 5,
                "offset": 0,
                "language": "uk",
                "search_query": "hello",
            },
        ),
        (
            "unread",
            {
                "user_id": current.user_id,
                "participant_role": "client",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_specialist_dialog_search_uses_lookahead():
    current = context()
    service, _, chats = build_service(
        current_context=current
    )

    result = await service.search_dialogs(
        platform_user_id=456,
        role="specialist",
        view="active",
        search_query="query",
        page_size=5,
    )

    assert result.role == "specialist"
    assert result.items == list(range(5))
    assert result.has_next is True

    assert chats.calls == [
        (
            "specialist_list",
            {
                "user_id": current.user_id,
                "view": "active",
                "limit": 6,
                "offset": 0,
                "language": "uk",
                "search_query": "query",
            },
        ),
        (
            "unread",
            {
                "user_id": current.user_id,
                "participant_role": (
                    "specialist"
                ),
            },
        ),
    ]


def test_dialog_search_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/user_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    matches = [
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "receive_messages_search"
    ]
    assert len(matches) == 1
    node = matches[0]

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert "UserDialogsService" in called_names
    assert "search_dialogs" in called_methods

    forbidden = {
        "ContactChatRepository",
        "ContactChatService",
        "get_billing_user_context",
    }
    assert not (
        called_names & forbidden
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "search_result.actor.language" in block
    assert "search_result.items" in block
    assert (
        "search_result.unread_messages"
        in block
    )
    assert "search_result.has_next" in block

    assert "items[:5]" not in block
    assert "len(items) > 5" not in block


def test_user_dialogs_owns_search_fsm():
    import ast
    from pathlib import Path

    dialogs_source = Path(
        "handlers/user_dialogs.py"
    ).read_text(encoding="utf-8")
    billing_source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")

    dialogs_tree = ast.parse(
        dialogs_source
    )
    billing_tree = ast.parse(
        billing_source
    )

    dialog_classes = {
        node.name: node
        for node in dialogs_tree.body
        if isinstance(node, ast.ClassDef)
    }
    billing_classes = {
        node.name: node
        for node in billing_tree.body
        if isinstance(node, ast.ClassDef)
    }

    assert "UserDialogsFSM" in (
        dialog_classes
    )
    assert "SpecialistCabinetFSM" in (
        billing_classes
    )

    def assigned_names(class_node):
        return {
            target.id
            for node in class_node.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }

    assert (
        "entering_messages_search"
        in assigned_names(
            dialog_classes[
                "UserDialogsFSM"
            ]
        )
    )
    assert (
        "entering_messages_search"
        not in assigned_names(
            billing_classes[
                "SpecialistCabinetFSM"
            ]
        )
    )

    references = [
        item
        for item in ast.walk(dialogs_tree)
        if isinstance(item, ast.Attribute)
        and item.attr
        == "entering_messages_search"
    ]
    assert len(references) == 2

    for reference in references:
        assert isinstance(
            reference.value,
            ast.Name,
        )
        assert (
            reference.value.id
            == "UserDialogsFSM"
        )


def test_dialog_language_is_application_service_owned():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/user_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
    }

    helper = functions[
        "get_user_dialog_language"
    ]

    helper_calls = {
        call.func.id
        for call in ast.walk(helper)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
    }
    helper_methods = {
        call.func.attr
        for call in ast.walk(helper)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert (
        "UserDialogsService"
        in helper_calls
    )
    assert "require_actor" in helper_methods
    assert "UserService" not in helper_calls
    assert (
        "get_billing_user_context"
        not in helper_calls
    )

    target_names = {
        (
            "confirm_thread_completion_"
            "from_notification"
        ),
        "finish_thread_from_chat",
        "open_client_dialog",
        "open_messages_search_prompt",
        "open_specialist_dialog",
        "receive_messages_search",
        "report_specialist_thread",
    }

    for name in target_names:
        node = functions[name]
        called_names = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
        }

        assert (
            "get_user_dialog_language"
            in called_names
        )
        assert (
            "get_billing_interface_language"
            not in called_names
        )


def test_user_dialogs_router_imports_independently():
    import ast
    from pathlib import Path

    from handlers.user_dialogs import (
        user_dialogs_router,
    )

    source = Path(
        "handlers/user_dialogs.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert (
        "user_dialogs_router = Router()"
        in source
    )
    assert (
        "from handlers.billing import"
        not in source
    )

    called_names = {
        call.func.id
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
    }

    forbidden = {
        "ContactChatRepository",
        "ContactChatService",
        "ModerationRepository",
        "ModerationService",
        "UserRepository",
        "UserService",
        "get_billing_user_context",
        "get_billing_interface_language",
    }
    assert not (
        called_names & forbidden
    )

    assert len(
        user_dialogs_router
        .callback_query.handlers
    ) == 11
    assert len(
        user_dialogs_router
        .message.handlers
    ) == 1


def test_user_dialogs_router_cutover():
    import ast
    from pathlib import Path

    billing_source = Path(
        "handlers/billing.py"
    ).read_text(encoding="utf-8")
    dialogs_source = Path(
        "handlers/user_dialogs.py"
    ).read_text(encoding="utf-8")
    bot_source = Path(
        "bot.py"
    ).read_text(encoding="utf-8")

    billing_tree = ast.parse(
        billing_source
    )
    dialogs_tree = ast.parse(
        dialogs_source
    )

    moved = {
        "UserDialogsFSM",
        "show_specialist_dialogs",
        "send_specialist_thread_detail",
        "finish_thread_from_chat",
        "report_specialist_thread",
        "receive_messages_search",
        "show_client_dialogs",
        "send_client_thread_detail",
        "open_client_dialog",
    }

    billing_definitions = {
        node.name
        for node in billing_tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }
    dialogs_definitions = {
        node.name
        for node in dialogs_tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }

    assert not (
        moved & billing_definitions
    )
    assert moved <= dialogs_definitions

    assert (
        bot_source.index(
            "dp.include_router("
            "user_dialogs_router)"
        )
        < bot_source.index(
            "dp.include_router("
            "billing_router)"
        )
    )


@pytest.mark.asyncio
async def test_notification_translation_is_service_owned():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsService,
    )

    message_id = uuid4()
    receiver_user_id = uuid4()
    expected = SimpleNamespace(
        display_text="translated",
        used_translation=True,
        translation_status="translated",
    )

    class FakeTranslation:
        def __init__(self):
            self.calls = []

        async def translate_notification_message(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return expected

    translation = FakeTranslation()
    service = object.__new__(
        UserDialogsService
    )
    service.translation = translation

    result = await (
        service.translate_notification_message(
            message_id=message_id,
            receiver_user_id=receiver_user_id,
        )
    )

    assert result is expected
    assert translation.calls == [
        {
            "message_id": message_id,
            "receiver_user_id": (
                receiver_user_id
            ),
        }
    ]


@pytest.mark.asyncio
async def test_open_contact_uses_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsActor,
        UserDialogsService,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )
    specialist_id = uuid4()
    profession_id = uuid4()
    chat = SimpleNamespace(
        thread_id=uuid4(),
        contact_request_id=uuid4(),
    )

    class FakeChats:
        def __init__(self):
            self.calls = []

        async def open_contact_chat(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return chat

    chats = FakeChats()
    service = object.__new__(
        UserDialogsService
    )
    service.chats = chats

    async def require_actor(
        *,
        platform_user_id,
    ):
        assert platform_user_id == 456
        return actor

    service.require_actor = require_actor

    result = await service.open_contact(
        platform_user_id=456,
        specialist_id=str(specialist_id),
        profession_id=str(profession_id),
        system_message="Hello",
        original_language="uk",
    )

    assert result.actor is actor
    assert result.chat is chat
    assert chats.calls == [
        {
            "tenant_id": actor.tenant_id,
            "from_user_id": actor.user_id,
            "specialist_id": specialist_id,
            "profession_id": profession_id,
            "system_message": "Hello",
            "original_language": "uk",
        }
    ]


@pytest.mark.asyncio
async def test_open_contact_rejects_invalid_ids():
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsActor,
        UserDialogsSelectionError,
        UserDialogsService,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )

    class FakeChats:
        def __init__(self):
            self.calls = []

        async def open_contact_chat(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    chats = FakeChats()
    service = object.__new__(
        UserDialogsService
    )
    service.chats = chats

    async def require_actor(
        *,
        platform_user_id,
    ):
        return actor

    service.require_actor = require_actor

    with pytest.raises(
        UserDialogsSelectionError,
        match="Invalid specialist",
    ):
        await service.open_contact(
            platform_user_id=123,
            specialist_id="invalid",
            profession_id=None,
            system_message="Hello",
            original_language="uk",
        )

    assert chats.calls == []


def test_contact_start_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name == "contact_start"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert "UserDialogsService" in called_names
    assert "open_contact" in called_methods
    assert not (
        called_names
        & {
            "UUID",
            "ContactChatRepository",
            "ContactChatService",
            "get_requester_context",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "store_post_auth_action" in block
    assert 'action="contact"' in block


@pytest.mark.asyncio
async def test_contact_chat_detail_uses_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsActor,
        UserDialogsService,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )
    thread_id = uuid4()
    detail = SimpleNamespace(
        messages=[]
    )

    class FakeChats:
        def __init__(self):
            self.detail_calls = []
            self.read_calls = []

        async def get_thread_detail_for_viewer(
            self,
            **kwargs,
        ):
            self.detail_calls.append(kwargs)
            return detail

        async def mark_thread_read(
            self,
            **kwargs,
        ):
            self.read_calls.append(kwargs)

    chats = FakeChats()
    service = object.__new__(
        UserDialogsService
    )
    service.chats = chats

    async def require_actor(
        *,
        platform_user_id,
    ):
        assert platform_user_id == 456
        return actor

    service.require_actor = require_actor

    result = await service.get_contact_chat(
        platform_user_id=456,
        thread_id=str(thread_id),
        viewer_role="client",
    )

    assert result.actor is actor
    assert result.detail is detail
    assert chats.detail_calls == [
        {
            "tenant_id": actor.tenant_id,
            "thread_id": thread_id,
            "user_id": actor.user_id,
            "participant_role": "client",
            "language": "uk",
        }
    ]
    assert chats.read_calls == [
        {
            "thread_id": thread_id,
            "user_id": actor.user_id,
        }
    ]


@pytest.mark.asyncio
async def test_contact_chat_detail_rejects_invalid_thread():
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsActor,
        UserDialogsService,
        UserDialogsThreadError,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )

    class FakeChats:
        def __init__(self):
            self.detail_calls = []
            self.read_calls = []

        async def get_thread_detail_for_viewer(
            self,
            **kwargs,
        ):
            self.detail_calls.append(kwargs)

        async def mark_thread_read(
            self,
            **kwargs,
        ):
            self.read_calls.append(kwargs)

    chats = FakeChats()
    service = object.__new__(
        UserDialogsService
    )
    service.chats = chats

    async def require_actor(
        *,
        platform_user_id,
    ):
        return actor

    service.require_actor = require_actor

    with pytest.raises(
        UserDialogsThreadError,
        match="Invalid dialog thread",
    ):
        await service.get_contact_chat(
            platform_user_id=123,
            thread_id="invalid",
            viewer_role="client",
        )

    assert chats.detail_calls == []
    assert chats.read_calls == []


def test_show_contact_chat_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "show_contact_chat"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert "UserDialogsService" in called_names
    assert "get_contact_chat" in called_methods

    assert not (
        called_names
        & {
            "UUID",
            "ContactChatRepository",
            "ContactChatService",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "platform_user_id" in block
    assert "message.from_user.id" in block
    assert "user_id=user_id" not in block


@pytest.mark.asyncio
async def test_thread_notification_switches_specialist_context():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsActor,
        UserDialogsService,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )
    thread_id = uuid4()
    context = SimpleNamespace(
        receiver_role="specialist",
        specialist_id=uuid4(),
        professional_cabinet_id=uuid4(),
    )

    class FakeChats:
        def __init__(self):
            self.calls = []

        async def get_thread_notification_context(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return context

    class FakeSpecialists:
        def __init__(self):
            self.calls = []

        async def switch_active_professional_cabinet(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    class FakeUsers:
        def __init__(self):
            self.calls = []

        async def switch_active_role(
            self,
            *args,
        ):
            self.calls.append(args)

    chats = FakeChats()
    specialists = FakeSpecialists()
    users = FakeUsers()

    service = object.__new__(
        UserDialogsService
    )
    service.chats = chats
    service.specialists = specialists
    service.users = users

    async def require_actor(
        *,
        platform_user_id,
    ):
        assert platform_user_id == 456
        return actor

    service.require_actor = require_actor

    result = await (
        service.open_thread_notification(
            platform_user_id=456,
            thread_id=str(thread_id),
        )
    )

    assert result.actor is actor
    assert result.context is context
    assert chats.calls == [
        {
            "thread_id": thread_id,
            "receiver_user_id": (
                actor.user_id
            ),
            "language": "uk",
        }
    ]
    assert specialists.calls == [
        {
            "tenant_id": actor.tenant_id,
            "user_id": actor.user_id,
            "specialist_id": (
                context.specialist_id
            ),
            "professional_cabinet_id": (
                context
                .professional_cabinet_id
            ),
        }
    ]
    assert users.calls == [
        (
            456,
            "specialist",
        )
    ]


@pytest.mark.asyncio
async def test_client_notification_skips_cabinet_switch():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsActor,
        UserDialogsService,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )
    context = SimpleNamespace(
        receiver_role="client",
    )

    class FakeChats:
        async def get_thread_notification_context(
            self,
            **kwargs,
        ):
            return context

    class FakeSpecialists:
        def __init__(self):
            self.calls = []

        async def switch_active_professional_cabinet(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    class FakeUsers:
        def __init__(self):
            self.calls = []

        async def switch_active_role(
            self,
            *args,
        ):
            self.calls.append(args)

    specialists = FakeSpecialists()
    users = FakeUsers()

    service = object.__new__(
        UserDialogsService
    )
    service.chats = FakeChats()
    service.specialists = specialists
    service.users = users

    async def require_actor(
        *,
        platform_user_id,
    ):
        return actor

    service.require_actor = require_actor

    await service.open_thread_notification(
        platform_user_id=123,
        thread_id=uuid4(),
    )

    assert specialists.calls == []
    assert users.calls == [
        (
            123,
            "client",
        )
    ]


def test_notification_open_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "open_contact_thread_from_notification"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert "UserDialogsService" in called_names
    assert (
        "open_thread_notification"
        in called_methods
    )

    assert not (
        called_names
        & {
            "UUID",
            "ContactChatRepository",
            "ContactChatService",
            "SpecialistRepository",
            "SpecialistService",
            "UserService",
            "get_requester_context",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "show_contact_chat" in block
    assert (
        "notification.actor.user_id"
        in block
    )


@pytest.mark.asyncio
async def test_contact_message_send_uses_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.user_dialogs import (
        UserDialogsActor,
        UserDialogsService,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )
    thread_id = uuid4()
    receiver_user_id = uuid4()
    result = SimpleNamespace(
        thread_id=thread_id,
        message_id=uuid4(),
        receiver_user_id=receiver_user_id,
    )
    delivery = SimpleNamespace(
        platform_user_id=987654,
        language_code="en",
    )
    context = SimpleNamespace(
        receiver_role="specialist",
    )
    translation_result = SimpleNamespace(
        display_text="Translated",
        used_translation=True,
        translation_status="translated",
    )

    class FakeChats:
        def __init__(self):
            self.send_calls = []
            self.context_calls = []

        async def send_thread_message(
            self,
            **kwargs,
        ):
            self.send_calls.append(kwargs)
            return result

        async def get_thread_notification_context(
            self,
            **kwargs,
        ):
            self.context_calls.append(kwargs)
            return context

    class FakeUsers:
        def __init__(self):
            self.calls = []

        async def get_telegram_delivery_context(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return delivery

    class FakeTranslation:
        def __init__(self):
            self.calls = []

        async def translate_notification_message(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return translation_result

    chats = FakeChats()
    users = FakeUsers()
    translation = FakeTranslation()

    service = object.__new__(
        UserDialogsService
    )
    service.chats = chats
    service.users = users
    service.translation = translation

    async def require_actor(
        *,
        platform_user_id,
    ):
        assert platform_user_id == 456
        return actor

    service.require_actor = require_actor

    attachment = {
        "type": "photo",
        "file_id": "photo-id",
    }

    action = await service.send_contact_message(
        platform_user_id=456,
        thread_id=str(thread_id),
        text="Hello",
        original_language="uk",
        attachment=attachment,
    )

    assert action.actor is actor
    assert action.result is result
    assert (
        action.receiver_platform_user_id
        == 987654
    )
    assert action.receiver_language == "en"
    assert (
        action.receiver_notification_context
        is context
    )
    assert (
        action.receiver_notification_message
        == "Translated"
    )
    assert action.receiver_used_translation is True
    assert (
        action.receiver_translation_status
        == "translated"
    )

    assert chats.send_calls == [
        {
            "thread_id": thread_id,
            "sender_user_id": actor.user_id,
            "text": "Hello",
            "original_language": "uk",
            "attachment": attachment,
        }
    ]
    assert chats.context_calls == [
        {
            "thread_id": thread_id,
            "receiver_user_id": (
                receiver_user_id
            ),
            "language": "en",
        }
    ]
    assert users.calls == [
        {
            "user_id": receiver_user_id,
        }
    ]
    assert translation.calls == [
        {
            "message_id": result.message_id,
            "receiver_user_id": (
                receiver_user_id
            ),
        }
    ]


@pytest.mark.asyncio
async def test_contact_message_tolerates_missing_notification_context():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.contact_chat import (
        ContactChatError,
    )
    from services.user_dialogs import (
        UserDialogsActor,
        UserDialogsService,
    )

    actor = UserDialogsActor(
        user_id=uuid4(),
        tenant_id=uuid4(),
        language="uk",
    )
    result = SimpleNamespace(
        thread_id=uuid4(),
        message_id=uuid4(),
        receiver_user_id=uuid4(),
    )

    class FakeChats:
        async def send_thread_message(
            self,
            **kwargs,
        ):
            return result

        async def get_thread_notification_context(
            self,
            **kwargs,
        ):
            raise ContactChatError(
                "Context unavailable."
            )

    class FakeUsers:
        async def get_telegram_delivery_context(
            self,
            **kwargs,
        ):
            return SimpleNamespace(
                platform_user_id=None,
                language_code=None,
            )

    class FakeTranslation:
        async def translate_notification_message(
            self,
            **kwargs,
        ):
            return SimpleNamespace(
                display_text="Original",
                used_translation=False,
                translation_status=(
                    "not_needed"
                ),
            )

    service = object.__new__(
        UserDialogsService
    )
    service.chats = FakeChats()
    service.users = FakeUsers()
    service.translation = FakeTranslation()

    async def require_actor(
        *,
        platform_user_id,
    ):
        return actor

    service.require_actor = require_actor

    action = await service.send_contact_message(
        platform_user_id=123,
        thread_id=result.thread_id,
        text="Hello",
        original_language="uk",
    )

    assert (
        action.receiver_notification_context
        is None
    )
    assert action.receiver_language == "uk"
    assert (
        action.receiver_notification_message
        == "Original"
    )


def test_receive_thread_message_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/search.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "receive_thread_message"
    )

    called_names = {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert "UserDialogsService" in called_names
    assert (
        "send_contact_message"
        in called_methods
    )

    assert not (
        called_names
        & {
            "UUID",
            "ContactChatRepository",
            "ContactChatService",
            "UserService",
            "get_requester_context",
            "translate_message_for_notification",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "message_action.actor.user_id" in block
    assert "receiver_notification_message" in block
    assert "send_contact_notification" in called_names
    assert "message.bot.send_photo" not in block
    assert "message.bot.send_document" not in block
    assert "show_contact_chat" in block
