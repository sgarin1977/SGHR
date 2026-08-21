from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.translation import (
    TranslationSettingsView,
)
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
    UserSettingsValidationError,
)


def build_settings(
    *,
    interface_language: str = "uk",
) -> TranslationSettingsView:
    return TranslationSettingsView(
        interface_language=interface_language,
        message_language="en",
        translation_mode="detect",
        auto_translate_enabled=True,
        show_original_button=True,
    )


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.requested_platform_user_ids = []

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.requested_platform_user_ids.append(
            platform_user_id
        )
        return self.user


class FakeTranslation:
    def __init__(self):
        self.settings = build_settings()
        self.calls = []

    async def get_language_settings_view(
        self,
        *,
        user_id,
    ):
        self.calls.append(
            (
                "get_context",
                {
                    "user_id": user_id,
                },
            )
        )
        return self.settings

    async def update_interface_language(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("interface_language", kwargs)
        )
        return self.settings

    async def update_message_language(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("message_language", kwargs)
        )
        return self.settings

    async def update_translation_mode(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("translation_mode", kwargs)
        )
        return self.settings

    async def toggle_show_original(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("show_original", kwargs)
        )
        return self.settings


class FakePrivacy:
    def __init__(self):
        self.calls = []

    async def request_data_export(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("request_data_export", kwargs)
        )

    async def delete_geo_data(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("delete_geo_data", kwargs)
        )
        return 3

    async def schedule_profile_deletion(
        self,
        **kwargs,
    ):
        self.calls.append(
            (
                "schedule_profile_deletion",
                kwargs,
            )
        )


def build_service(*, user):
    users = FakeUsers(user)
    translation = FakeTranslation()
    privacy = FakePrivacy()

    service = UserSettingsService(
        SimpleNamespace(),
        users=users,
        translation=translation,
        privacy=privacy,
    )

    return (
        service,
        users,
        translation,
        privacy,
    )


@pytest.mark.asyncio
async def test_unknown_user_fails_closed():
    service, _, translation, privacy = (
        build_service(user=None)
    )

    with pytest.raises(
        UserSettingsNotFoundError
    ):
        await service.get_context(
            platform_user_id=123,
        )

    assert translation.calls == []
    assert privacy.calls == []


@pytest.mark.asyncio
async def test_user_without_tenant_fails_closed():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=None,
        language_code="ru",
    )
    service, _, translation, privacy = (
        build_service(user=user)
    )

    with pytest.raises(
        UserSettingsNotFoundError
    ):
        await service.update_translation_mode(
            platform_user_id=123,
            translation_mode="detect",
        )

    assert translation.calls == []
    assert privacy.calls == []


@pytest.mark.asyncio
async def test_context_uses_persisted_settings():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        language_code="ru",
    )
    service, users, translation, _ = (
        build_service(user=user)
    )

    context = await service.get_context(
        platform_user_id=456,
    )

    assert users.requested_platform_user_ids == [
        456
    ]
    assert context.user_id == user.id
    assert context.tenant_id == user.tenant_id
    assert context.interface_language == "uk"
    assert context.settings is translation.settings
    assert translation.calls == [
        (
            "get_context",
            {
                "user_id": user.id,
            },
        )
    ]


@pytest.mark.asyncio
async def test_translation_updates_are_delegated():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        language_code="ru",
    )
    service, _, translation, _ = (
        build_service(user=user)
    )

    await service.update_interface_language(
        platform_user_id=1,
        language_code="uk",
    )
    await service.update_message_language(
        platform_user_id=1,
        language_code="en",
    )
    await service.update_translation_mode(
        platform_user_id=1,
        translation_mode="detect",
    )
    await service.toggle_show_original(
        platform_user_id=1,
    )

    common = {
        "tenant_id": user.tenant_id,
        "user_id": user.id,
        "source": "client_settings",
    }

    assert translation.calls == [
        (
            "interface_language",
            {
                **common,
                "language_code": "uk",
            },
        ),
        (
            "message_language",
            {
                **common,
                "language_code": "en",
            },
        ),
        (
            "translation_mode",
            {
                **common,
                "translation_mode": "detect",
            },
        ),
        (
            "show_original",
            common,
        ),
    ]


@pytest.mark.asyncio
async def test_privacy_actions_are_delegated():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        language_code="ru",
    )
    service, _, _, privacy = (
        build_service(user=user)
    )

    await service.request_data_export(
        platform_user_id=10,
    )
    deleted_count = (
        await service.delete_geo_data(
            platform_user_id=10,
        )
    )
    await service.schedule_profile_deletion(
        platform_user_id=10,
    )

    identity = {
        "tenant_id": user.tenant_id,
        "user_id": user.id,
    }

    assert deleted_count == 3
    assert privacy.calls == [
        (
            "request_data_export",
            identity,
        ),
        (
            "delete_geo_data",
            identity,
        ),
        (
            "schedule_profile_deletion",
            identity,
        ),
    ]


@pytest.mark.asyncio
async def test_invalid_translation_values_fail_closed():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        language_code="ru",
    )
    service, users, translation, _ = (
        build_service(user=user)
    )

    with pytest.raises(
        UserSettingsValidationError
    ):
        await service.update_message_language(
            platform_user_id=1,
            language_code="invalid",
        )

    with pytest.raises(
        UserSettingsValidationError
    ):
        await service.update_translation_mode(
            platform_user_id=1,
            translation_mode="invalid",
        )

    assert (
        users.requested_platform_user_ids
        == []
    )
    assert translation.calls == []


def test_translation_values_are_normalized():
    assert (
        UserSettingsService
        .validate_language_code(" UK ")
        == "uk"
    )
    assert (
        UserSettingsService
        .validate_translation_mode(
            " DETECT "
        )
        == "detect"
    )


def test_settings_handler_has_no_direct_data_access():
    source = open(
        "handlers/settings.py",
        encoding="utf-8",
    ).read()

    assert "UserSettingsService" in source
    assert "TranslationRepository" not in source
    assert "PrivacyRepository" not in source
    assert "EventRepository" not in source
    assert "UserService" not in source
    assert "PrivacyService" not in source
    assert "session.commit()" not in source
    assert "session.rollback()" not in source
