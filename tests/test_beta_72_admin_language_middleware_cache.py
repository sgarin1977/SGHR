from types import SimpleNamespace

import pytest

from handlers import admin_common


@pytest.mark.asyncio
async def test_admin_language_is_loaded_once_per_update(
    monkeypatch,
):
    service_calls = []
    fake_session = object()

    class FakeSessionContext:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            return False

    class FakeUserSettingsService:
        def __init__(self, session):
            assert session is fake_session

        async def get_context(
            self,
            *,
            platform_user_id,
        ):
            service_calls.append(
                platform_user_id
            )
            return SimpleNamespace(
                interface_language="uk",
            )

    monkeypatch.setattr(
        admin_common,
        "get_session",
        lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(
        admin_common,
        "UserSettingsService",
        FakeUserSettingsService,
    )

    event = SimpleNamespace(
        from_user=SimpleNamespace(
            id=123456,
            language_code="en",
        )
    )
    data = {}
    observed_languages = []

    async def handler(event, data):
        observed_languages.append(
            admin_common
            .normalize_admin_language(
                event.from_user.language_code
            )
        )
        return "handled"

    middleware = (
        admin_common
        .AdminInterfaceLanguageMiddleware()
    )

    assert await middleware(
        handler,
        event,
        data,
    ) == "handled"
    assert await middleware(
        handler,
        event,
        data,
    ) == "handled"

    assert observed_languages == [
        "uk",
        "uk",
    ]
    assert service_calls == [123456]
