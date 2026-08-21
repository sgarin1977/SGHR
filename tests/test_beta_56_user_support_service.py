from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_settings import (
    UserSettingsNotFoundError,
)
from services.user_support import (
    UserSupportAccessError,
    UserSupportSelectionError,
    UserSupportService,
)


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeSettings:
    def __init__(
        self,
        *,
        context=None,
        error=None,
    ):
        self.context = context
        self.error = error
        self.calls = []

    async def get_context(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return self.context


class FakeEvents:
    def __init__(self):
        self.calls = []

    async def create_event(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)


def actor_context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


def build_service(
    *,
    context=None,
    error=None,
):
    session = FakeSession()
    settings = FakeSettings(
        context=context,
        error=error,
    )
    events = FakeEvents()
    service = UserSupportService(
        session,
        settings=settings,
        events=events,
    )
    return (
        service,
        session,
        settings,
        events,
    )


@pytest.mark.asyncio
async def test_support_requires_actor():
    (
        service,
        session,
        _,
        events,
    ) = build_service(
        error=UserSettingsNotFoundError()
    )

    with pytest.raises(
        UserSupportAccessError,
        match="Support user",
    ):
        await service.open_menu(
            platform_user_id=123
        )

    assert events.calls == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_support_actor_uses_settings():
    context = actor_context()
    service, _, settings, _ = (
        build_service(context=context)
    )

    actor = await service.require_actor(
        platform_user_id=456
    )

    assert actor.user_id == context.user_id
    assert actor.tenant_id == (
        context.tenant_id
    )
    assert actor.language == "uk"
    assert settings.calls == [
        {
            "platform_user_id": 456,
        }
    ]


@pytest.mark.asyncio
async def test_open_menu_records_event():
    context = actor_context()
    (
        service,
        session,
        _,
        events,
    ) = build_service(context=context)

    actor = await service.open_menu(
        platform_user_id=123
    )

    assert actor.user_id == context.user_id
    assert events.calls == [
        {
            "event_type": (
                "support_opened"
            ),
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "entity_type": "support",
            "entity_id": None,
            "payload": {
                "source": "support_menu",
            },
            "platform": "telegram",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_category_records_event():
    context = actor_context()
    (
        service,
        session,
        _,
        events,
    ) = build_service(context=context)

    await service.select_category(
        platform_user_id=123,
        category="other",
    )

    assert events.calls == [
        {
            "event_type": (
                "ticket_category"
            ),
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "entity_type": (
                "support_ticket"
            ),
            "entity_id": None,
            "payload": {
                "category": "other",
            },
            "platform": "telegram",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_invalid_category_fails_closed():
    (
        service,
        session,
        settings,
        events,
    ) = build_service(
        context=actor_context()
    )

    with pytest.raises(
        UserSupportSelectionError,
        match="Invalid support category",
    ):
        await service.select_category(
            platform_user_id=123,
            category="invalid",
        )

    assert settings.calls == []
    assert events.calls == []
    assert session.commits == 0


def test_support_entry_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/support.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "get_support_user_context": (
            "require_actor"
        ),
        "open_support_menu": "open_menu",
        "select_support_category": (
            "select_category"
        ),
    }

    for function_name, method_name in expected.items():
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

        assert "UserSupportService" in called_names
        assert method_name in called_methods

        assert not (
            called_names
            & {
                "TranslationRepository",
                "UserService",
                "EventRepository",
            }
        )

        assert not (
            called_methods
            & {
                "commit",
                "rollback",
                "flush",
            }
        )


@pytest.mark.asyncio
async def test_create_ticket_uses_actor_scope():
    from types import SimpleNamespace

    context = actor_context()
    ticket = SimpleNamespace(id=uuid4())

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def create_ticket(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return ticket

    session = FakeSession()
    settings = FakeSettings(
        context=context
    )
    events = FakeEvents()
    support = FakeSupport()

    service = UserSupportService(
        session,
        settings=settings,
        events=events,
        support=support,
    )

    action = await service.create_ticket(
        platform_user_id=123,
        category="other",
        priority="P3",
        message_text="Please help me.",
    )

    assert action.actor.user_id == (
        context.user_id
    )
    assert action.result is ticket
    assert support.calls == [
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "subject": None,
            "priority": "P3",
            "category": "other",
            "message_text": (
                "Please help me."
            ),
        }
    ]
    assert events.calls == [
        {
            "event_type": (
                "ticket_created"
            ),
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "entity_type": (
                "support_ticket"
            ),
            "entity_id": ticket.id,
            "payload": {
                "category": "other",
                "priority": "P3",
            },
            "platform": "telegram",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_create_ticket_requires_actor_before_write():
    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def create_ticket(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    support = FakeSupport()
    session = FakeSession()
    events = FakeEvents()

    service = UserSupportService(
        session,
        settings=FakeSettings(
            error=(
                UserSettingsNotFoundError()
            )
        ),
        events=events,
        support=support,
    )

    with pytest.raises(
        UserSupportAccessError,
    ):
        await service.create_ticket(
            platform_user_id=123,
            category="other",
            priority="P3",
            message_text="Please help me.",
        )

    assert support.calls == []
    assert events.calls == []
    assert session.commits == 0


def test_send_support_ticket_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/support.py"
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
        == "send_support_ticket"
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

    assert "UserSupportService" in called_names
    assert "create_ticket" in called_methods

    assert not (
        called_names
        & {
            "UserService",
            "SupportRepository",
            "SupportService",
            "EventRepository",
        }
    )
    assert not (
        called_methods
        & {
            "commit",
            "rollback",
            "flush",
        }
    )


@pytest.mark.asyncio
async def test_ticket_list_uses_actor_scope():
    context = actor_context()
    tickets = [
        SimpleNamespace(id=uuid4())
        for _ in range(6)
    ]

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def list_user_tickets(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return tickets

    session = FakeSession()
    events = FakeEvents()
    support = FakeSupport()

    service = UserSupportService(
        session,
        settings=FakeSettings(
            context=context
        ),
        events=events,
        support=support,
    )

    page = await service.list_tickets(
        platform_user_id=123,
        view="active",
        page=2,
        page_size=5,
    )

    assert page.actor.user_id == (
        context.user_id
    )
    assert page.items == tuple(
        tickets[:5]
    )
    assert page.view == "active"
    assert page.page == 2
    assert page.has_next is True
    assert support.calls == [
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "statuses": {
                "open",
                "in_progress",
            },
            "limit": 6,
            "offset": 10,
        }
    ]
    assert events.calls == [
        {
            "event_type": "ticket_list",
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "entity_type": (
                "support_ticket"
            ),
            "entity_id": None,
            "payload": {
                "view": "active",
                "page": 2,
                "count": 5,
                "has_next": True,
            },
            "platform": "telegram",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_resolved_ticket_list_normalizes_view():
    context = actor_context()

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def list_user_tickets(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return []

    support = FakeSupport()
    service = UserSupportService(
        FakeSession(),
        settings=FakeSettings(
            context=context
        ),
        events=FakeEvents(),
        support=support,
    )

    page = await service.list_tickets(
        platform_user_id=123,
        view="resolved",
        page=-4,
    )

    assert page.items == ()
    assert page.page == 0
    assert page.has_next is False
    assert support.calls[0]["statuses"] == {
        "resolved",
        "closed",
        "rejected",
    }
    assert support.calls[0]["offset"] == 0


def test_ticket_list_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/support.py"
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
        == "list_my_support_tickets"
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

    assert "UserSupportService" in called_names
    assert "list_tickets" in called_methods

    assert not (
        called_names
        & {
            "UserService",
            "SupportRepository",
            "SupportService",
            "EventRepository",
            "get_support_user_context",
        }
    )
    assert not (
        called_methods
        & {
            "commit",
            "rollback",
            "flush",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "ticket_page.items" in block
    assert "ticket_page.has_next" in block


@pytest.mark.asyncio
async def test_get_ticket_uses_actor_scope():
    context = actor_context()
    ticket_id = uuid4()
    view = SimpleNamespace(
        can_reply=True
    )

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def get_user_ticket_view(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return view

    support = FakeSupport()
    service = UserSupportService(
        FakeSession(),
        settings=FakeSettings(
            context=context
        ),
        events=FakeEvents(),
        support=support,
    )

    action = await service.get_ticket(
        platform_user_id=123,
        ticket_id=str(ticket_id),
    )

    assert action.actor.user_id == (
        context.user_id
    )
    assert action.result is view
    assert support.calls == [
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "ticket_id": ticket_id,
        }
    ]


@pytest.mark.asyncio
async def test_get_ticket_rejects_invalid_id():
    context = actor_context()

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def get_user_ticket_view(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    support = FakeSupport()
    service = UserSupportService(
        FakeSession(),
        settings=FakeSettings(
            context=context
        ),
        events=FakeEvents(),
        support=support,
    )

    with pytest.raises(
        UserSupportSelectionError,
        match="Invalid support ticket",
    ):
        await service.get_ticket(
            platform_user_id=123,
            ticket_id="invalid",
        )

    assert support.calls == []


def test_ticket_view_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/support.py"
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
        == "view_my_support_ticket"
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

    assert "UserSupportService" in called_names
    assert "get_ticket" in called_methods

    assert not (
        called_names
        & {
            "UUID",
            "UserService",
            "SupportRepository",
            "SupportService",
            "get_support_user_context",
        }
    )
    assert not (
        called_methods
        & {
            "commit",
            "rollback",
            "flush",
        }
    )


@pytest.mark.asyncio
async def test_close_ticket_uses_actor_scope():
    context = actor_context()
    ticket_id = uuid4()
    ticket = SimpleNamespace(id=ticket_id)

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def close_user_ticket(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return ticket

    session = FakeSession()
    events = FakeEvents()
    support = FakeSupport()

    service = UserSupportService(
        session,
        settings=FakeSettings(
            context=context
        ),
        events=events,
        support=support,
    )

    action = await service.close_ticket(
        platform_user_id=123,
        ticket_id=str(ticket_id),
    )

    assert action.actor.user_id == (
        context.user_id
    )
    assert action.result is ticket
    assert support.calls == [
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "ticket_id": ticket_id,
        }
    ]
    assert events.calls == [
        {
            "event_type": "closed",
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "entity_type": (
                "support_ticket"
            ),
            "entity_id": ticket_id,
            "payload": {
                "source": (
                    "user_support_ticket"
                ),
                "status": "closed",
            },
            "platform": "telegram",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_close_ticket_rejects_invalid_id():
    context = actor_context()

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def close_user_ticket(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    support = FakeSupport()
    events = FakeEvents()

    service = UserSupportService(
        FakeSession(),
        settings=FakeSettings(
            context=context
        ),
        events=events,
        support=support,
    )

    with pytest.raises(
        UserSupportSelectionError,
        match="Invalid support ticket",
    ):
        await service.close_ticket(
            platform_user_id=123,
            ticket_id="invalid",
        )

    assert support.calls == []
    assert events.calls == []


def test_ticket_close_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/support.py"
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
        == "close_my_support_ticket"
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

    assert "UserSupportService" in called_names
    assert "close_ticket" in called_methods

    assert not (
        called_names
        & {
            "UUID",
            "UserService",
            "SupportRepository",
            "SupportService",
            "EventRepository",
            "get_support_user_context",
        }
    )
    assert not (
        called_methods
        & {
            "commit",
            "rollback",
            "flush",
        }
    )

    block = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert (
        "support_ticket_already_closed"
        in block
    )


@pytest.mark.asyncio
async def test_reply_to_ticket_uses_actor_scope():
    context = actor_context()
    ticket_id = uuid4()
    support_message = SimpleNamespace(
        id=uuid4()
    )

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def add_user_message(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)
            return support_message

    session = FakeSession()
    events = FakeEvents()
    support = FakeSupport()

    service = UserSupportService(
        session,
        settings=FakeSettings(
            context=context
        ),
        events=events,
        support=support,
    )

    action = await service.reply_to_ticket(
        platform_user_id=123,
        ticket_id=str(ticket_id),
        message_text="Additional details.",
    )

    assert action.actor.user_id == (
        context.user_id
    )
    assert action.result is support_message
    assert support.calls == [
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "ticket_id": ticket_id,
            "message_text": (
                "Additional details."
            ),
        }
    ]
    assert events.calls == [
        {
            "event_type": (
                "ticket_message"
            ),
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "entity_type": (
                "support_ticket"
            ),
            "entity_id": ticket_id,
            "payload": {
                "sender_role": "user",
            },
            "platform": "telegram",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_reply_rejects_invalid_ticket_id():
    context = actor_context()

    class FakeSupport:
        def __init__(self):
            self.calls = []

        async def add_user_message(
            self,
            **kwargs,
        ):
            self.calls.append(kwargs)

    support = FakeSupport()
    events = FakeEvents()

    service = UserSupportService(
        FakeSession(),
        settings=FakeSettings(
            context=context
        ),
        events=events,
        support=support,
    )

    with pytest.raises(
        UserSupportSelectionError,
        match="Invalid support ticket",
    ):
        await service.reply_to_ticket(
            platform_user_id=123,
            ticket_id="invalid",
            message_text="Additional details.",
        )

    assert support.calls == []
    assert events.calls == []


def test_ticket_reply_handler_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/support.py"
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
        == "receive_user_support_reply"
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

    assert "UserSupportService" in called_names
    assert "reply_to_ticket" in called_methods

    assert not (
        called_names
        & {
            "UUID",
            "UserService",
            "SupportRepository",
            "SupportService",
            "EventRepository",
            "get_support_user_context",
        }
    )
    assert not (
        called_methods
        & {
            "commit",
            "rollback",
            "flush",
        }
    )


def test_support_handler_layer_is_business_clean():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/support.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imports = {}

    for node in tree.body:
        if isinstance(
            node,
            ast.ImportFrom,
        ):
            module = node.module or ""

            for alias in node.names:
                imports[
                    alias.asname or alias.name
                ] = (
                    f"{module}.{alias.name}"
                )

    forbidden_calls = []
    transactions = []

    for function in (
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    ):
        for call in ast.walk(function):
            if not isinstance(
                call,
                ast.Call,
            ):
                continue

            if isinstance(
                call.func,
                ast.Name,
            ):
                name = call.func.id
                origin = imports.get(
                    name,
                    "",
                )
                module = origin.rsplit(
                    ".",
                    1,
                )[0]

                if origin.startswith(
                    "database.repositories."
                ):
                    forbidden_calls.append(
                        (
                            function.name,
                            name,
                        )
                    )

                if (
                    origin.startswith(
                        "services."
                    )
                    and name.endswith(
                        "Service"
                    )
                    and module
                    != "services.user_support"
                ):
                    forbidden_calls.append(
                        (
                            function.name,
                            name,
                        )
                    )

            if (
                isinstance(
                    call.func,
                    ast.Attribute,
                )
                and call.func.attr
                in {
                    "commit",
                    "rollback",
                    "flush",
                }
            ):
                transactions.append(
                    (
                        function.name,
                        call.func.attr,
                    )
                )

    assert forbidden_calls == []
    assert transactions == []

    definitions = {
        node.name
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }

    assert "get_support_user_context" in definitions
