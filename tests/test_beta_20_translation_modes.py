from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from database.models import Message
from database.repositories.contact import (
    ContactChatRepository,
)
from database.repositories.translation import (
    normalize_translation_mode,
)
from services.translation import (
    LibreTranslateProvider,
    TranslationProviderError,
    TranslationService,
)


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def flush(self):
        self.flushes += 1


class FakeTranslationRepository:
    def __init__(
        self,
        *,
        mode,
        source="ru",
        target="en",
        text="Test message",
    ):
        self.session = FakeSession()
        self.mode = mode
        self.source = source
        self.target = target
        self.logs = []
        self.claim_available = True
        self.job = (
            SimpleNamespace(
                id=uuid4(),
                source_language=source,
                status="pending",
                retry_count=0,
                max_retries=3,
                error_message=None,
            )
            if (
                mode == "detect"
                or (
                    mode == "standard"
                    and source != target
                )
            )
            else None
        )
        self.message = SimpleNamespace(
            id=uuid4(),
            tenant_id=uuid4(),
            receiver_user_id=uuid4(),
            original_text=text,
            original_language=source,
            translated_text=None,
            translated_language=None,
            translation_status="pending",
        )

    async def get_message(self, message_id):
        assert message_id == self.message.id
        return self.message

    async def get_translation_mode(
        self,
        user_id,
    ):
        assert (
            user_id
            == self.message.receiver_user_id
        )
        return self.mode

    async def get_user_message_language(
        self,
        user_id,
    ):
        return self.target

    async def claim_pending_job_for_message(
        self,
        message_id,
    ):
        assert message_id == self.message.id

        if not self.claim_available:
            return None

        return self.job

    async def get_cached_translation(
        self,
        **values,
    ):
        return None

    async def mark_message_not_needed(
        self,
        message,
    ):
        message.translation_status = "not_needed"
        message.translated_text = None
        message.translated_language = None
        return message

    async def mark_message_translated(
        self,
        *,
        message,
        translated_text,
        target_language,
    ):
        message.translation_status = "translated"
        message.translated_text = translated_text
        message.translated_language = (
            target_language
        )
        return message

    async def mark_message_failed(
        self,
        message,
    ):
        message.translation_status = "failed"
        return message

    async def mark_job_translated(
        self,
        job,
    ):
        job.status = "translated"
        job.error_message = None
        return job

    async def mark_job_failed(
        self,
        *,
        job,
        error_message,
    ):
        job.retry_count += 1
        job.status = "retry"
        job.error_message = error_message
        return job

    async def log_translation(
        self,
        **values,
    ):
        self.logs.append(values)

    async def save_cached_translation(
        self,
        **values,
    ):
        return None


class FakeProvider:
    provider_name = "fake"

    def __init__(
        self,
        detected_language="uk",
    ):
        self.detected_language = (
            detected_language
        )
        self.detect_calls = []
        self.translate_calls = []

    async def detect_language(
        self,
        *,
        text,
    ):
        self.detect_calls.append(text)
        return self.detected_language

    async def translate(
        self,
        *,
        text,
        source_language,
        target_language,
    ):
        self.translate_calls.append(
            (
                text,
                source_language,
                target_language,
            )
        )
        return f"translated:{text}"


def build_service(
    *,
    mode,
    source="ru",
    target="en",
    detected="uk",
):
    repository = FakeTranslationRepository(
        mode=mode,
        source=source,
        target=target,
    )
    provider = FakeProvider(
        detected_language=detected,
    )
    service = TranslationService(
        repository,
        provider,
        cache_enabled=False,
    )
    return repository, provider, service


def test_dialog_preview_translation_sql():
    user_id = uuid4()

    translated_expression = (
        ContactChatRepository
        ._last_message_display_expression(
            user_id=user_id,
            translation_mode="detect",
            message_language="en",
        )
    )
    translated_sql = str(
        select(
            translated_expression
        ).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "CASE" in translated_sql
    assert "messages.translated_text" in (
        translated_sql
    )
    assert "messages.receiver_user_id" in (
        translated_sql
    )
    assert (
        "messages.translated_language = 'en'"
        in translated_sql
    )

    original_expression = (
        ContactChatRepository
        ._last_message_display_expression(
            user_id=user_id,
            translation_mode="off",
            message_language="en",
        )
    )
    original_sql = str(
        select(
            original_expression
        ).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "CASE" not in original_sql
    assert "messages.original_text" in (
        original_sql
    )


def test_translation_mode_normalization():
    assert normalize_translation_mode(
        "off"
    ) == "off"
    assert normalize_translation_mode(
        "standard"
    ) == "standard"
    assert normalize_translation_mode(
        "detect"
    ) == "detect"
    assert normalize_translation_mode(
        None
    ) == "standard"
    assert normalize_translation_mode(
        "invalid"
    ) == "standard"


@pytest.mark.asyncio
async def test_off_returns_original_without_provider():
    repository, provider, service = (
        build_service(
            mode="off",
        )
    )

    result = await service.translate_message(
        repository.message.id
    )

    assert result.display_text == "Test message"
    assert result.translation_status == (
        "not_needed"
    )
    assert result.used_translation is False
    assert provider.detect_calls == []
    assert provider.translate_calls == []
    assert repository.session.commits == 1


@pytest.mark.asyncio
async def test_standard_uses_stored_source_language():
    repository, provider, service = (
        build_service(
            mode="standard",
            source="ru",
            target="en",
            detected="uk",
        )
    )

    result = await service.translate_message(
        repository.message.id
    )

    assert provider.detect_calls == []
    assert provider.translate_calls == [
        (
            "Test message",
            "ru",
            "en",
        )
    ]
    assert result.used_translation is True
    assert result.original_language == "ru"
    assert result.display_language == "en"


@pytest.mark.asyncio
async def test_detect_uses_actual_text_language():
    repository, provider, service = (
        build_service(
            mode="detect",
            source="ru",
            target="en",
            detected="uk",
        )
    )

    result = await service.translate_message(
        repository.message.id
    )

    assert provider.detect_calls == [
        "Test message"
    ]
    assert provider.translate_calls == [
        (
            "Test message",
            "uk",
            "en",
        )
    ]
    assert (
        repository.message.original_language
        == "uk"
    )
    assert result.original_language == "uk"
    assert result.used_translation is True


@pytest.mark.asyncio
async def test_detect_skips_same_language():
    repository, provider, service = (
        build_service(
            mode="detect",
            source="ru",
            target="uk",
            detected="uk",
        )
    )

    result = await service.translate_message(
        repository.message.id
    )

    assert provider.detect_calls == [
        "Test message"
    ]
    assert provider.translate_calls == []
    assert result.translation_status == (
        "not_needed"
    )
    assert result.used_translation is False


@pytest.mark.asyncio
async def test_translation_for_old_target_is_hidden():
    repository, provider, service = (
        build_service(
            mode="standard",
            source="uk",
            target="de",
        )
    )
    repository.message.translation_status = (
        "translated"
    )
    repository.message.translated_text = (
        "Old English translation"
    )
    repository.message.translated_language = (
        "en"
    )

    result = (
        await service.get_message_for_receiver(
            message_id=repository.message.id,
            receiver_user_id=(
                repository.message.receiver_user_id
            ),
        )
    )

    assert result.display_text == "Test message"
    assert result.display_language == "uk"
    assert result.used_translation is False
    assert provider.detect_calls == []
    assert provider.translate_calls == []


@pytest.mark.asyncio
async def test_locked_job_is_not_processed_twice():
    repository, provider, service = (
        build_service(
            mode="detect",
            source="ru",
            target="en",
            detected="uk",
        )
    )
    repository.claim_available = False

    result = await service.translate_message(
        repository.message.id
    )

    assert result.display_text == "Test message"
    assert result.translation_status == "pending"
    assert result.used_translation is False
    assert provider.detect_calls == []
    assert provider.translate_calls == []
    assert repository.session.commits == 0


@pytest.mark.asyncio
async def test_off_hides_existing_translation():
    repository, provider, service = (
        build_service(
            mode="off",
            source="en",
            target="ru",
        )
    )
    repository.message.translation_status = (
        "translated"
    )
    repository.message.translated_text = (
        "Переведённый текст"
    )
    repository.message.translated_language = (
        "ru"
    )

    result = (
        await service.get_message_for_receiver(
            message_id=repository.message.id,
            receiver_user_id=(
                repository.message.receiver_user_id
            ),
        )
    )

    assert result.display_text == "Test message"
    assert result.display_language == "en"
    assert result.used_translation is False
    assert provider.detect_calls == []
    assert provider.translate_calls == []


class FakeResponse:
    def __init__(
        self,
        data,
        *,
        json_error=None,
    ):
        self.data = data
        self.json_error = json_error

    def raise_for_status(self):
        return None

    def json(self):
        if self.json_error:
            raise self.json_error

        return self.data


class FakeHttpClient:
    def __init__(
        self,
        data,
        *,
        json_error=None,
    ):
        self.data = data
        self.json_error = json_error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    async def post(self, url, *, json):
        self.calls.append(
            {
                "url": url,
                "json": json,
            }
        )
        return FakeResponse(
            self.data,
            json_error=self.json_error,
        )


@pytest.mark.asyncio
async def test_libretranslate_detect_contract():
    client = FakeHttpClient(
        [
            {
                "language": "ru",
                "confidence": 20,
            },
            {
                "language": "uk",
                "confidence": 95,
            },
        ]
    )
    provider = LibreTranslateProvider(
        base_url="http://translate.test",
        api_key="test-key",
    )

    with patch(
        "services.translation."
        "httpx.AsyncClient",
        return_value=client,
    ):
        detected = await provider.detect_language(
            text="Добрий день",
        )

    assert detected == "uk"
    assert client.calls == [
        {
            "url": (
                "http://translate.test/detect"
            ),
            "json": {
                "q": "Добрий день",
                "api_key": "test-key",
            },
        }
    ]


@pytest.mark.asyncio
async def test_empty_detection_fails_closed():
    client = FakeHttpClient([])
    provider = LibreTranslateProvider(
        base_url="http://translate.test",
    )

    with patch(
        "services.translation."
        "httpx.AsyncClient",
        return_value=client,
    ):
        with pytest.raises(
            TranslationProviderError
        ):
            await provider.detect_language(
                text="Unknown",
            )


class FakeContactSession:
    def __init__(self, message):
        self.message = message
        self.added = []
        self.flushes = 0

    async def get(self, model, entity_id):
        if (
            model is Message
            and entity_id == self.message.id
        ):
            return self.message
        return None

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1


class ContactTranslationSettings:
    mode = "standard"
    target = "en"

    def __init__(self, session):
        self.session = session

    async def get_translation_mode(
        self,
        user_id,
    ):
        return self.mode

    async def get_user_message_language(
        self,
        user_id,
    ):
        return self.target

    async def get_pending_job_for_message(
        self,
        message_id,
    ):
        return None


async def create_job_for_mode(
    *,
    mode,
    source,
    target,
):
    tenant_id = uuid4()
    receiver_user_id = uuid4()
    message = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        receiver_user_id=receiver_user_id,
        translation_status="pending",
        translated_text=None,
        translated_language=None,
    )
    session = FakeContactSession(message)
    repository = ContactChatRepository(
        session
    )

    ContactTranslationSettings.mode = mode
    ContactTranslationSettings.target = (
        target
    )

    with patch(
        "database.repositories.contact."
        "TranslationRepository",
        ContactTranslationSettings,
    ):
        job = await repository._create_translation_job_if_needed(
            tenant_id=tenant_id,
            message_id=message.id,
            source_language=source,
            receiver_user_id=receiver_user_id,
        )

    return message, session, job


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "mode",
        "source",
        "target",
        "job_expected",
    ),
    [
        ("off", "ru", "en", False),
        ("standard", "ru", "ru", False),
        ("standard", "ru", "en", True),
        ("detect", "ru", "ru", True),
    ],
)
async def test_translation_job_creation_by_mode(
    mode,
    source,
    target,
    job_expected,
):
    message, session, job = (
        await create_job_for_mode(
            mode=mode,
            source=source,
            target=target,
        )
    )

    assert (job is not None) is job_expected
    assert bool(session.added) is job_expected

    if job_expected:
        assert (
            message.translation_status
            == "pending"
        )
        assert job.target_language == target
    else:
        assert (
            message.translation_status
            == "not_needed"
        )

def test_translation_callback_is_bound_to_thread():
    from handlers.search import (
        build_contact_translation_callback,
        parse_contact_translation_callback,
    )

    thread_id = uuid4()

    for action in (
        "original",
        "translation",
    ):
        for role in (
            "client",
            "specialist",
        ):
            callback_data = (
                build_contact_translation_callback(
                    action=action,
                    thread_id=thread_id,
                    role=role,
                )
            )

            assert len(
                callback_data.encode("utf-8")
            ) <= 64
            assert (
                parse_contact_translation_callback(
                    callback_data,
                    action=action,
                )
                == (str(thread_id), role)
            )


def test_translation_callback_rejects_stale_data():
    from handlers.search import (
        parse_contact_translation_callback,
    )

    thread_id = uuid4()

    invalid_values = (
        "contact_show_original",
        (
            "contact_show_original:"
            f"{thread_id}:invalid"
        ),
        "contact_show_original:not-a-uuid:c",
    )

    for value in invalid_values:
        assert (
            parse_contact_translation_callback(
                value,
                action="original",
            )
            is None
        )

    assert (
        parse_contact_translation_callback(
            (
                "contact_show_translation:"
                f"{thread_id}:c"
            ),
            action="original",
        )
        is None
    )


def test_billing_keyboard_binds_original_to_thread():
    from handlers.billing import (
        message_thread_keyboard,
    )
    from handlers.search import (
        parse_contact_translation_callback,
    )

    thread_id = uuid4()

    for role in (
        "client",
        "specialist",
    ):
        keyboard = message_thread_keyboard(
            "en",
            role=role,
            show_original=True,
            thread_id=thread_id,
        )
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        original_callback = next(
            value
            for value in callbacks
            if value.startswith(
                "contact_show_original:"
            )
        )

        assert (
            parse_contact_translation_callback(
                original_callback,
                action="original",
            )
            == (str(thread_id), role)
        )

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    (
        "detect",
        "translate",
    ),
)
async def test_libretranslate_rejects_invalid_json(
    operation,
):
    client = FakeHttpClient(
        None,
        json_error=ValueError(
            "Invalid JSON"
        ),
    )
    provider = LibreTranslateProvider(
        base_url="http://translate.test",
    )

    with patch(
        "services.translation."
        "httpx.AsyncClient",
        return_value=client,
    ):
        with pytest.raises(
            TranslationProviderError
        ):
            if operation == "detect":
                await provider.detect_language(
                    text="Test message",
                )
            else:
                await provider.translate(
                    text="Test message",
                    source_language="en",
                    target_language="ru",
                )


@pytest.mark.asyncio
async def test_detection_ignores_invalid_confidence():
    client = FakeHttpClient(
        [
            {
                "language": "ru",
                "confidence": "invalid",
            },
            {
                "language": "uk",
                "confidence": 90,
            },
        ]
    )
    provider = LibreTranslateProvider(
        base_url="http://translate.test",
    )

    with patch(
        "services.translation."
        "httpx.AsyncClient",
        return_value=client,
    ):
        detected = await provider.detect_language(
            text="Добрий день",
        )

    assert detected == "uk"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_data",
    (
        [],
        None,
        {"translatedText": None},
        {"translatedText": ""},
        {"translatedText": "   "},
        {"translatedText": 123},
    ),
)
async def test_translation_rejects_malformed_response(
    response_data,
):
    client = FakeHttpClient(response_data)
    provider = LibreTranslateProvider(
        base_url="http://translate.test",
    )

    with patch(
        "services.translation."
        "httpx.AsyncClient",
        return_value=client,
    ):
        with pytest.raises(
            TranslationProviderError
        ):
            await provider.translate(
                text="Test message",
                source_language="en",
                target_language="ru",
            )

class InconsistentSettingsRepository:
    def __init__(
        self,
        *,
        mode,
        legacy_enabled,
    ):
        self.session = FakeSession()
        self.settings = SimpleNamespace(
            interface_language="ru",
            message_language="en",
            translation_mode=mode,
            auto_translate_enabled=(
                legacy_enabled
            ),
            show_original_button=True,
        )

    async def get_language_settings(
        self,
        user_id,
    ):
        return self.settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "mode",
        "legacy_enabled",
        "expected_enabled",
    ),
    (
        ("off", True, False),
        ("standard", False, True),
        ("detect", False, True),
    ),
)
async def test_translation_mode_is_authoritative(
    mode,
    legacy_enabled,
    expected_enabled,
):
    repository = (
        InconsistentSettingsRepository(
            mode=mode,
            legacy_enabled=legacy_enabled,
        )
    )
    service = TranslationService(
        repository,
        FakeProvider(),
    )

    settings = (
        await service.get_language_settings_view(
            user_id=uuid4(),
        )
    )

    assert settings.translation_mode == mode
    assert (
        settings.auto_translate_enabled
        is expected_enabled
    )
    assert repository.session.commits == 1
