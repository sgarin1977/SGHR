from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_legal import (
    UserLegalAccessError,
    UserLegalService,
)
from services.user_settings import (
    UserSettingsNotFoundError,
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


class FakeLegal:
    def __init__(self):
        self.list_calls = []
        self.accept_calls = []
        self.documents = [
            SimpleNamespace(
                doc_type="terms"
            ),
            SimpleNamespace(
                doc_type="privacy"
            ),
        ]

    async def get_missing_specialist_consents(
        self,
        **kwargs,
    ):
        self.list_calls.append(kwargs)
        return self.documents

    async def accept_required_specialist_consents(
        self,
        **kwargs,
    ):
        self.accept_calls.append(kwargs)


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
    legal = FakeLegal()
    events = FakeEvents()
    service = UserLegalService(
        session,
        settings=settings,
        legal=legal,
        events=events,
    )
    return (
        service,
        session,
        settings,
        legal,
        events,
    )


@pytest.mark.asyncio
async def test_legal_requires_actor():
    (
        service,
        session,
        _,
        legal,
        events,
    ) = build_service(
        error=UserSettingsNotFoundError()
    )

    with pytest.raises(
        UserLegalAccessError,
    ):
        await service.start_specialist_gate(
            platform_user_id=123
        )

    assert legal.list_calls == []
    assert events.calls == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_start_context_uses_settings():
    context = actor_context()
    service, _, settings, _, _ = (
        build_service(context=context)
    )

    actor = await service.get_start_context(
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
async def test_start_gate_records_event_and_lists_documents():
    context = actor_context()
    (
        service,
        session,
        _,
        legal,
        events,
    ) = build_service(context=context)

    result = await (
        service.start_specialist_gate(
            platform_user_id=123
        )
    )

    assert result.actor.user_id == (
        context.user_id
    )
    assert result.documents == tuple(
        legal.documents
    )
    assert events.calls == [
        {
            "event_type": (
                "registration_started"
            ),
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "entity_type": (
                "specialist_registration"
            ),
            "entity_id": None,
            "payload": {
                "source": (
                    "specialist_start"
                ),
            },
            "platform": "telegram",
        }
    ]
    assert legal.list_calls == [
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "language": "uk",
        }
    ]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_document_list_has_no_start_event():
    context = actor_context()
    (
        service,
        session,
        _,
        legal,
        events,
    ) = build_service(context=context)

    result = await (
        service.list_specialist_documents(
            platform_user_id=123
        )
    )

    assert result.documents == tuple(
        legal.documents
    )
    assert events.calls == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_accept_gate_uses_actor_scope():
    context = actor_context()
    (
        service,
        _,
        _,
        legal,
        _,
    ) = build_service(context=context)

    actor = await (
        service.accept_specialist_gate(
            platform_user_id=123
        )
    )

    assert actor.user_id == (
        context.user_id
    )
    assert legal.accept_calls == [
        {
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "language": "uk",
            "platform": "telegram",
        }
    ]


def test_legal_entry_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/legal.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "specialist_registration_start_screen": (
            "get_start_context"
        ),
        "specialist_start_legal_gate": (
            "start_specialist_gate"
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

        assert "UserLegalService" in called_names
        assert method_name in called_methods

        assert not (
            called_names
            & {
                "UserService",
                "LegalRepository",
                "LegalService",
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


def test_legal_document_handlers_use_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/legal.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected = {
        "show_specialist_legal_documents": (
            "list_specialist_documents"
        ),
        "accept_specialist_legal_gate": (
            "accept_specialist_gate"
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

        assert "UserLegalService" in called_names
        assert method_name in called_methods

        assert not (
            called_names
            & {
                "UserService",
                "LegalRepository",
                "LegalService",
            }
        )


def test_legal_handler_layer_is_business_clean():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/legal.py"
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
                    != "services.user_legal"
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
