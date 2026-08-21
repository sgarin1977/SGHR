from types import SimpleNamespace
from uuid import uuid4

import ast
from pathlib import Path

import pytest

from services.user_settings import (
    UserSettingsNotFoundError,
)
from services.user_start import (
    UserStartService,
)


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


class FakeUsers:
    def __init__(
        self,
        *,
        role_context=None,
    ):
        self.role_context = role_context
        self.calls = []

    async def get_role_switch_context(
        self,
        platform_user_id,
        *,
        language="ru",
    ):
        self.calls.append(
            {
                "platform_user_id": (
                    platform_user_id
                ),
                "language": language,
            }
        )
        return self.role_context


def build_service(
    *,
    settings_context=None,
    settings_error=None,
    role_context=None,
):
    settings = FakeSettings(
        context=settings_context,
        error=settings_error,
    )
    users = FakeUsers(
        role_context=role_context,
    )
    service = UserStartService(
        SimpleNamespace(),
        users=users,
        settings=settings,
    )
    return service, settings, users


@pytest.mark.asyncio
async def test_missing_user_uses_fallback_language():
    service, settings, users = build_service(
        settings_error=(
            UserSettingsNotFoundError()
        ),
    )

    result = await service.get_context(
        platform_user_id=123,
        fallback_language="uk",
    )

    assert result.language == "uk"
    assert result.role_context is None
    assert settings.calls == [
        {
            "platform_user_id": 123,
        }
    ]
    assert users.calls == []


@pytest.mark.asyncio
async def test_context_uses_interface_language():
    role_context = SimpleNamespace(
        active_role="client",
        available_roles=[
            "client",
            "specialist",
        ],
    )
    service, _, users = build_service(
        settings_context=SimpleNamespace(
            interface_language="en",
        ),
        role_context=role_context,
    )

    result = await service.get_context(
        platform_user_id=456,
        fallback_language="pl",
    )

    assert result.language == "en"
    assert result.role_context is (
        role_context
    )
    assert users.calls == [
        {
            "platform_user_id": 456,
            "language": "en",
        }
    ]


@pytest.mark.asyncio
async def test_invalid_fallback_defaults_to_russian():
    service, _, _ = build_service(
        settings_error=(
            UserSettingsNotFoundError()
        ),
    )

    result = await service.get_context(
        platform_user_id=789,
        fallback_language="unsupported",
    )

    assert result.language == "ru"


@pytest.mark.asyncio
async def test_registered_user_may_have_no_role_context():
    service, _, users = build_service(
        settings_context=SimpleNamespace(
            interface_language="de",
        ),
        role_context=None,
    )

    result = await service.get_context(
        platform_user_id=900,
    )

    assert result.language == "de"
    assert result.role_context is None
    assert users.calls == [
        {
            "platform_user_id": 900,
            "language": "de",
        }
    ]


def test_user_start_service_has_no_handler_dependency():
    source = Path(
        "services/user_start.py"
    ).read_text(encoding="utf-8")

    assert "handlers." not in source



def test_start_read_handlers_use_application_service():
    import ast

    source = Path(
        "handlers/start.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    function_names = (
        "get_main_menu_keyboard_for_user",
        "send_global_main_menu",
        "show_role_switch",
    )

    for function_name in function_names:
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = (
            ast.get_source_segment(
                source,
                node,
            )
            or ""
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

        assert (
            "UserStartService"
            in called_names
        )
        assert "get_context" in called_methods
        assert "UserService(" not in block
        assert (
            "TranslationRepository("
            not in block
        )
        assert (
            "get_language_settings"
            not in block
        )


def test_global_menu_reuses_loaded_start_context():
    source = Path(
        "handlers/start.py"
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
        == "send_global_main_menu"
    )
    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )
    calls = [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
        and call.func.id
        == "get_main_menu_keyboard_for_user"
    ]

    assert len(calls) == 1

    keywords = {
        keyword.arg: keyword.value
        for keyword in calls[0].keywords
    }
    value = keywords.get(
        "start_context"
    )

    assert isinstance(
        value,
        ast.Name,
    )
    assert value.id == "start_context"



class FakeStartMutationUsers:
    def __init__(
        self,
        *,
        registration=None,
        role_context=None,
        switch_error=None,
    ):
        self.registration = registration
        self.role_context = role_context
        self.switch_error = switch_error
        self.register_calls = []
        self.role_calls = []
        self.switch_calls = []

    async def register_telegram_user(
        self,
        data,
    ):
        self.register_calls.append(data)
        return self.registration

    async def get_role_switch_context(
        self,
        platform_user_id,
        *,
        language="ru",
    ):
        self.role_calls.append(
            {
                "platform_user_id": (
                    platform_user_id
                ),
                "language": language,
            }
        )
        return self.role_context

    async def switch_active_role(
        self,
        platform_user_id,
        role,
    ):
        self.switch_calls.append(
            {
                "platform_user_id": (
                    platform_user_id
                ),
                "role": role,
            }
        )

        if self.switch_error:
            raise self.switch_error

        return self.role_context


@pytest.mark.asyncio
async def test_start_registration_returns_resolved_context():
    registration = SimpleNamespace(
        is_new=True,
        role="client",
    )
    role_context = SimpleNamespace(
        active_role="client",
        available_roles=["client"],
    )
    users = FakeStartMutationUsers(
        registration=registration,
        role_context=role_context,
    )
    settings = FakeSettings(
        context=SimpleNamespace(
            interface_language="uk",
        )
    )
    service = UserStartService(
        SimpleNamespace(),
        users=users,
        settings=settings,
    )

    result = await service.register_user(
        platform_user_id=123,
        username="tester",
        first_name="Test",
        last_name="User",
        language_code="UK",
    )

    assert result.registration is registration
    assert result.context.language == "uk"
    assert (
        result.context.role_context
        is role_context
    )

    data = users.register_calls[0]
    assert data.platform_user_id == "123"
    assert data.username == "tester"
    assert data.first_name == "Test"
    assert data.last_name == "User"
    assert data.language_code == "uk"


@pytest.mark.asyncio
async def test_start_role_switch_returns_language_context():
    role_context = SimpleNamespace(
        active_role="specialist",
        available_roles=[
            "client",
            "specialist",
        ],
    )
    users = FakeStartMutationUsers(
        role_context=role_context,
    )
    settings = FakeSettings(
        context=SimpleNamespace(
            interface_language="pl",
        )
    )
    service = UserStartService(
        SimpleNamespace(),
        users=users,
        settings=settings,
    )

    result = await service.switch_role(
        platform_user_id=456,
        role="specialist",
        fallback_language="en",
    )

    assert result.language == "pl"
    assert result.role_context is (
        role_context
    )
    assert users.switch_calls == [
        {
            "platform_user_id": 456,
            "role": "specialist",
        }
    ]


@pytest.mark.asyncio
async def test_start_role_switch_propagates_invalid_role():
    users = FakeStartMutationUsers(
        switch_error=ValueError(
            "Role is not active."
        ),
    )
    service = UserStartService(
        SimpleNamespace(),
        users=users,
        settings=FakeSettings(
            context=SimpleNamespace(
                interface_language="en",
            )
        ),
    )

    with pytest.raises(
        ValueError,
        match="Role is not active",
    ):
        await service.switch_role(
            platform_user_id=789,
            role="admin",
        )



def test_start_mutation_handlers_use_application_service():
    source = Path(
        "handlers/start.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected_methods = {
        "cmd_start": "register_user",
        "switch_active_role": (
            "switch_role"
        ),
    }

    for (
        function_name,
        service_method,
    ) in expected_methods.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = (
            ast.get_source_segment(
                source,
                node,
            )
            or ""
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

        assert (
            "UserStartService"
            in called_names
        )
        assert (
            service_method
            in called_methods
        )
        assert "UserService(" not in block
        assert (
            "TranslationRepository("
            not in block
        )
        assert (
            "TelegramUserData("
            not in block
        )


def test_start_handlers_keep_error_boundaries():
    source = Path(
        "handlers/start.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected_errors = {
        "cmd_start": "RateLimitError",
        "switch_active_role": "ValueError",
    }

    for (
        function_name,
        error_name,
    ) in expected_errors.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )

        handled_errors = {
            handler.type.id
            for item in ast.walk(node)
            if isinstance(item, ast.Try)
            for handler in item.handlers
            if isinstance(
                handler.type,
                ast.Name,
            )
        }

        assert error_name in handled_errors



class FakeStartSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeStartEvents:
    def __init__(
        self,
        *,
        error=None,
    ):
        self.error = error
        self.calls = []

    async def create_event(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error:
            raise self.error


@pytest.mark.asyncio
async def test_start_language_uses_settings():
    settings = FakeSettings(
        context=SimpleNamespace(
            interface_language="nl",
        )
    )
    service = UserStartService(
        SimpleNamespace(),
        users=FakeUsers(),
        settings=settings,
        events=FakeStartEvents(),
    )

    language = await service.get_language(
        platform_user_id=123,
        fallback_language="en",
    )

    assert language == "nl"


@pytest.mark.asyncio
async def test_start_language_keeps_fallback_for_missing_user():
    service = UserStartService(
        SimpleNamespace(),
        users=FakeUsers(),
        settings=FakeSettings(
            error=(
                UserSettingsNotFoundError()
            )
        ),
        events=FakeStartEvents(),
    )

    language = await service.get_language(
        platform_user_id=456,
        fallback_language="uk",
    )

    assert language == "uk"


@pytest.mark.asyncio
async def test_start_placeholder_records_event():
    context = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="pl",
    )
    session = FakeStartSession()
    events = FakeStartEvents()
    service = UserStartService(
        session,
        users=FakeUsers(),
        settings=FakeSettings(
            context=context,
        ),
        events=events,
    )

    language = await (
        service.record_placeholder_opened(
            platform_user_id=789,
            feature="jobs_remote",
            source="jobs_menu",
            fallback_language="en",
        )
    )

    assert language == "pl"
    assert session.commits == 1
    assert session.rollbacks == 0
    assert events.calls == [
        {
            "event_type": (
                "placeholder_opened"
            ),
            "tenant_id": (
                context.tenant_id
            ),
            "user_id": context.user_id,
            "entity_type": "feature",
            "entity_id": None,
            "payload": {
                "feature": "jobs_remote",
                "source": "jobs_menu",
            },
            "platform": "telegram",
        }
    ]


@pytest.mark.asyncio
async def test_start_placeholder_rolls_back_on_error():
    session = FakeStartSession()
    events = FakeStartEvents(
        error=RuntimeError("event failed")
    )
    service = UserStartService(
        session,
        users=FakeUsers(),
        settings=FakeSettings(
            context=SimpleNamespace(
                user_id=uuid4(),
                tenant_id=uuid4(),
                interface_language="en",
            )
        ),
        events=events,
    )

    with pytest.raises(
        RuntimeError,
        match="event failed",
    ):
        await (
            service
            .record_placeholder_opened(
                platform_user_id=900,
                feature="jobs",
                source="global_menu",
            )
        )

    assert session.commits == 0
    assert session.rollbacks == 1



def test_start_feature_handlers_use_application_service():
    source = Path(
        "handlers/start.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    expected_methods = {
        "open_jobs_menu": (
            "record_placeholder_opened"
        ),
        "open_jobs_placeholder": (
            "record_placeholder_opened"
        ),
        "open_all_services": (
            "get_language"
        ),
    }

    for (
        function_name,
        service_method,
    ) in expected_methods.items():
        node = next(
            item
            for item in tree.body
            if isinstance(
                item,
                ast.AsyncFunctionDef,
            )
            and item.name == function_name
        )
        block = (
            ast.get_source_segment(
                source,
                node,
            )
            or ""
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

        assert (
            "UserStartService"
            in called_names
        )
        assert (
            service_method
            in called_methods
        )
        assert "UserService(" not in block
        assert (
            "TranslationRepository("
            not in block
        )
        assert (
            "EventRepository("
            not in block
        )
        assert ".commit(" not in block
        assert ".rollback(" not in block



def test_start_handler_is_business_clean():
    source = Path(
        "handlers/start.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_imports = {
        (
            "database.repositories.event",
            "EventRepository",
        ),
        (
            "database.repositories.translation",
            "TranslationRepository",
        ),
        (
            "services.user",
            "TelegramUserData",
        ),
        (
            "services.user",
            "UserService",
        ),
    }

    imported = {
        (
            node.module or "",
            alias.name,
        )
        for node in tree.body
        if isinstance(
            node,
            ast.ImportFrom,
        )
        for alias in node.names
    }

    assert not (
        imported & forbidden_imports
    )

    forbidden_calls = {
        "EventRepository",
        "TranslationRepository",
        "TelegramUserData",
        "UserService",
    }

    for node in tree.body:
        if not isinstance(
            node,
            ast.AsyncFunctionDef,
        ):
            continue

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

        assert not (
            called_names & forbidden_calls
        )
        assert not (
            called_methods
            & {
                "commit",
                "rollback",
            }
        )

    assert (
        "from services.user_start import "
        "UserStartService"
        in source
    )



def test_main_cabinet_callback_has_single_owner():
    import ast
    from pathlib import Path

    sources = {
        name: Path(path).read_text(
            encoding="utf-8"
        )
        for name, path in {
            "start": "handlers/start.py",
            "billing": "handlers/billing.py",
        }.items()
    }

    owners = {}

    for name, source in sources.items():
        tree = ast.parse(source)
        owners[name] = [
            node.name
            for node in tree.body
            if isinstance(
                node,
                ast.AsyncFunctionDef,
            )
            and any(
                (
                    "callback_query"
                    in ast.unparse(
                        decorator
                    )
                    and "M_CABINET"
                    in ast.unparse(
                        decorator
                    )
                )
                for decorator
                in node.decorator_list
            )
        ]

    assert owners["start"] == [
        "open_current_role_cabinet"
    ]
    assert owners["billing"] == []

    billing_tree = ast.parse(
        sources["billing"]
    )
    imported_from_start = {
        alias.name
        for node in billing_tree.body
        if isinstance(
            node,
            ast.ImportFrom,
        )
        and node.module
        == "handlers.start"
        for alias in node.names
    }

    assert (
        "open_current_role_cabinet"
        not in imported_from_start
    )
