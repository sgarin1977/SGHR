from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.user_settings import (
    SpecialistProfileNotFoundError,
    UserSettingsService,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user

    async def get_user_by_telegram_id(
        self,
        platform_user_id,
    ):
        self.platform_user_id = platform_user_id
        return self.user


class FakeLegal:
    def __init__(self, consents=None):
        self.consents = consents or []
        self.calls = []

    async def list_user_consent_views(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)
        return self.consents


class FakeSpecialists:
    def __init__(self, specialist):
        self.specialist = specialist
        self.user_ids = []

    async def get_by_user_id(self, user_id):
        self.user_ids.append(user_id)
        return self.specialist


class FakePrivacy:
    def __init__(self):
        self.calls = []

    async def schedule_profile_deletion(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)


def build_service(
    *,
    user,
    specialist=None,
    consents=None,
):
    legal = FakeLegal(consents)
    specialists = FakeSpecialists(specialist)
    privacy = FakePrivacy()

    service = UserSettingsService(
        object(),
        users=FakeUsers(user),
        translation=object(),
        privacy=privacy,
        legal=legal,
        specialists=specialists,
    )

    return service, legal, specialists, privacy


@pytest.mark.asyncio
async def test_list_consents_uses_verified_user():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    consent = SimpleNamespace(
        consent_type="translation",
        version="1",
        is_revoked=False,
    )
    service, legal, _, _ = build_service(
        user=user,
        consents=[consent],
    )

    result = await service.list_consents(
        platform_user_id=123,
    )

    assert result == [consent]
    assert legal.calls == [
        {
            "tenant_id": user.tenant_id,
            "user_id": user.id,
        }
    ]


@pytest.mark.asyncio
async def test_specialist_deletion_keeps_audit_context():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    specialist = SimpleNamespace(id=uuid4())
    (
        service,
        _,
        specialists,
        privacy,
    ) = build_service(
        user=user,
        specialist=specialist,
    )

    await service.schedule_specialist_profile_deletion(
        platform_user_id=456,
    )

    assert specialists.user_ids == [user.id]
    assert privacy.calls == [
        {
            "tenant_id": user.tenant_id,
            "user_id": user.id,
            "specialist_id": specialist.id,
            "source": "specialist_cabinet",
        }
    ]


@pytest.mark.asyncio
async def test_specialist_deletion_requires_profile():
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
    )
    service, _, _, privacy = build_service(
        user=user,
        specialist=None,
    )

    with pytest.raises(
        SpecialistProfileNotFoundError,
        match="Specialist profile",
    ):
        await (
            service
            .schedule_specialist_profile_deletion(
                platform_user_id=789,
            )
        )

    assert privacy.calls == []


def test_specialist_settings_read_handlers_use_application_service():
    import ast

    source = open(
        "handlers/specialist_settings.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    for function_name in (
        "render_specialist_interface_language",
        "render_specialist_language_settings",
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
        block = "\n".join(
            lines[node.lineno - 1:node.end_lineno]
        )

        assert "UserSettingsService" in block
        assert ".get_context(" in block
        assert "TranslationRepository" not in block
        assert "TranslationService(" not in block
        assert "UserService(" not in block


def test_specialist_settings_mutations_use_application_service():
    import ast

    source = open(
        "handlers/specialist_settings.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "set_specialist_interface_language": (
            "update_interface_language"
        ),
        "set_specialist_translation_mode": (
            "update_translation_mode"
        ),
        "set_specialist_message_language": (
            "update_message_language"
        ),
        "toggle_specialist_show_original": (
            "toggle_show_original"
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
        block = "\n".join(
            lines[node.lineno - 1:node.end_lineno]
        )

        assert "UserSettingsService" in block
        assert f".{method_name}(" in block
        assert "TranslationRepository" not in block
        assert "TranslationService(" not in block
        assert "UserService(" not in block


def test_specialist_consent_and_deletion_use_application_service():
    import ast

    source = open(
        "handlers/specialist_settings.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(source)
    lines = source.splitlines()

    expected = {
        "specialist_settings_consents": (
            "list_consents"
        ),
        "schedule_specialist_profile_delete": (
            "schedule_specialist_profile_deletion"
        ),
    }

    forbidden = {
        "LegalRepository",
        "LegalService(",
        "PrivacyRepository",
        "PrivacyService(",
        "get_billing_user_context",
        "get_current_specialist_for_telegram",
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
        block = "\n".join(
            lines[node.lineno - 1:node.end_lineno]
        )

        assert "UserSettingsService" in block
        assert f".{method_name}(" in block

        for marker in forbidden:
            assert marker not in block


def test_specialist_settings_router_is_independent():
    import ast

    settings_source = open(
        "handlers/specialist_settings.py",
        encoding="utf-8",
    ).read()
    billing_source = open(
        "handlers/billing.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    settings_tree = ast.parse(settings_source)
    billing_tree = ast.parse(billing_source)

    settings_names = {
        getattr(node, "name", None)
        for node in settings_tree.body
    }
    billing_names = {
        getattr(node, "name", None)
        for node in billing_tree.body
    }

    moved = {
        "render_specialist_interface_language",
        "render_specialist_language_settings",
        "set_specialist_interface_language",
        "set_specialist_translation_mode",
        "set_specialist_message_language",
        "toggle_specialist_show_original",
        "specialist_settings_consents",
        "schedule_specialist_profile_delete",
    }

    assert moved <= settings_names
    assert not (moved & billing_names)
    assert (
        "specialist_settings_router = Router()"
        in settings_source
    )
    assert (
        "from handlers.billing import"
        not in settings_source
    )
    assert (
        "dp.include_router("
        "specialist_settings_router)"
        in bot_source
    )
