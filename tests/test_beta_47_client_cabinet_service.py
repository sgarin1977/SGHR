from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.client_cabinet import (
    ClientCabinetNotFoundError,
    ClientCabinetService,
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
        cabinet="cabinet",
        profile="profile",
    ):
        self.cabinet = cabinet
        self.profile = profile
        self.calls = []

    async def open_client_cabinet(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("cabinet", kwargs)
        )
        return self.cabinet

    async def get_client_profile(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("profile", kwargs)
        )
        return self.profile


def context():
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        interface_language="uk",
    )


@pytest.mark.asyncio
async def test_open_cabinet_uses_settings_language():
    settings = FakeSettings(context())
    users = FakeUsers()
    service = ClientCabinetService(
        object(),
        settings=settings,
        users=users,
    )

    result = await service.open_cabinet(
        platform_user_id=123
    )

    assert result.language == "uk"
    assert result.result == "cabinet"
    assert users.calls == [
        (
            "cabinet",
            {
                "telegram_id": 123,
                "language": "uk",
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_profile_uses_settings_language():
    settings = FakeSettings(context())
    users = FakeUsers()
    service = ClientCabinetService(
        object(),
        settings=settings,
        users=users,
    )

    result = await service.get_profile(
        platform_user_id=456
    )

    assert result.language == "uk"
    assert result.result == "profile"
    assert users.calls == [
        (
            "profile",
            {
                "telegram_id": 456,
                "language": "uk",
            },
        )
    ]


@pytest.mark.asyncio
async def test_missing_settings_context_fails_closed():
    users = FakeUsers()
    service = ClientCabinetService(
        object(),
        settings=FakeSettings(
            error=UserSettingsNotFoundError()
        ),
        users=users,
    )

    with pytest.raises(
        ClientCabinetNotFoundError
    ):
        await service.open_cabinet(
            platform_user_id=789
        )

    assert users.calls == []


@pytest.mark.asyncio
async def test_missing_domain_result_fails_closed():
    service = ClientCabinetService(
        object(),
        settings=FakeSettings(context()),
        users=FakeUsers(
            cabinet=None,
            profile=None,
        ),
    )

    with pytest.raises(
        ClientCabinetNotFoundError
    ):
        await service.open_cabinet(
            platform_user_id=101
        )

    with pytest.raises(
        ClientCabinetNotFoundError
    ):
        await service.get_profile(
            platform_user_id=101
        )

def test_client_cabinet_handlers_use_application_service():
    import ast

    source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)

    expected = {
        "show_client_user_profile": (
            "get_profile"
        ),
        "show_client_cabinet": (
            "open_cabinet"
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

        assert "ClientCabinetService" in (
            called_names
        )
        assert service_method in called_methods
        assert "UserService" not in (
            called_names
        )
        assert (
            "get_billing_interface_language"
            not in called_names
        )

    cabinet_node = next(
        item
        for item in tree.body
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name == "show_client_cabinet"
    )
    cabinet_block = ast.get_source_segment(
        source,
        cabinet_node,
    )

    assert "if not callback_answered" in (
        cabinet_block
    )
    assert "await state.clear()" in (
        cabinet_block
    )
    assert (
        "edit_or_replace_menu_message("
        in cabinet_block
    )
