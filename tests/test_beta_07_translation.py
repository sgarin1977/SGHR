import pytest
from sqlalchemy import delete, select

from database.models import (
    Message,
    TranslationCache,
    TranslationJob,
    TranslationLog,
    User,
    UserLanguageSetting,
)
from database.repositories.contact import ContactChatRepository
from database.repositories.translation import TranslationRepository, translation_text_hash
from services.contact_chat import ContactChatService
from services.translation import (
    TranslationError,
    TranslationProviderError,
    TranslationService,
)
from tests.test_beta_06_contact_chat import (
    cleanup_legal_documents,
    cleanup_test_user,
    create_active_specialist_for_contact,
    create_test_user,
)


class FakeTranslationProvider:
    provider_name = "fake"

    def __init__(self, translated_text: str = "Переведенный текст", fail: bool = False):
        self.translated_text = translated_text
        self.fail = fail
        self.calls = []

    async def translate(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        self.calls.append(
            {
                "text": text,
                "source_language": source_language,
                "target_language": target_language,
            }
        )

        if self.fail:
            raise TranslationProviderError("fake provider is unavailable")

        return self.translated_text

@pytest.mark.asyncio
async def cleanup_beta_07_translation_records(session, user_ids=None):
    await session.rollback()

    await session.execute(
        delete(TranslationLog).where(TranslationLog.provider == "fake")
    )
    await session.execute(
        delete(TranslationCache).where(TranslationCache.provider == "fake")
    )

    if user_ids:
        await session.execute(
            delete(UserLanguageSetting).where(
                UserLanguageSetting.user_id.in_(list(user_ids))
            )
        )

    await session.commit()

@pytest.mark.asyncio
async def create_contact_request_for_translation(
    session,
    *,
    original_language: str = "en",
    message: str = "Hello, I need help with translation beta service.",
):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        session,
        prefix="test-beta-07-client",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(session)
    )

    specialist_user = await session.get(User, specialist_user_id)
    specialist_user.language_code = "ru"
    await session.commit()

    result = await ContactChatService(
        ContactChatRepository(session)
    ).create_contact_request(
        tenant_id=tenant_id,
        from_user_id=client_user_id,
        specialist_id=specialist.id,
        message=message,
        original_language=original_language,
    )

    return {
        "client_platform_id": client_platform_id,
        "client_user_id": client_user_id,
        "specialist_platform_id": specialist_platform_id,
        "specialist_user_id": specialist_user_id,
        "tenant_id": tenant_id,
        "specialist": specialist,
        "result": result,
    }


def test_beta_07_translation_orm_contract_matches_existing_tables():
    source = open("database/models.py", encoding="utf-8").read()

    required_fragments = [
        "class TranslationCache",
        '__tablename__ = "translation_cache"',
        "source_text_hash",
        "source_language",
        "target_language",
        "translated_text",
        "provider",
        "class TranslationLog",
        '__tablename__ = "translation_logs"',
        "job_id",
        "latency_ms",
        "error_message",
        "class UserLanguageSetting",
        '__tablename__ = "user_language_settings"',
        "message_language",
        "auto_translate_enabled",
        "show_original_button",
    ]

    for fragment in required_fragments:
        assert fragment in source


def test_beta_07_translation_repository_and_service_contract():
    repository_source = open("database/repositories/translation.py", encoding="utf-8").read()
    service_source = open("services/translation.py", encoding="utf-8").read()
    handler_source = open("handlers/search.py", encoding="utf-8").read()

    required_repository_fragments = [
        "class TranslationRepository",
        "get_cached_translation",
        "save_cached_translation",
        "get_pending_job_for_message",
        "list_pending_jobs",
        "mark_message_translated",
        "mark_message_failed",
        "mark_job_translated",
        "mark_job_failed",
        "log_translation",
        "get_user_message_language",
        "is_auto_translate_enabled",
        "get_thread_for_user",
        "get_latest_received_message_in_thread",
    ]

    required_service_fragments = [
        "class LibreTranslateProvider",
        "TRANSLATION_BASE_URL",
        "TRANSLATION_CACHE_ENABLED",
        "class TranslationService",
        "translate_message",
        "process_pending_jobs",
        "get_message_for_receiver",
        "get_original_message_for_thread",
        "TranslationProviderError",
    ]

    required_handler_fragments = [
        "translate_message_for_notification",
        "TranslationService(TranslationRepository(session))",
        "message_id=result.first_message_id",
        "message_id=result.message_id",
        "callback_data=\"contact_show_original\"",
        "async def show_original_message",
    ]

    for fragment in required_repository_fragments:
        assert fragment in repository_source

    for fragment in required_service_fragments:
        assert fragment in service_source

    for fragment in required_handler_fragments:
        assert fragment in handler_source

    assert "contact_show_original_pending" not in handler_source
    assert "callback_data=f\"contact_show_original" not in handler_source

def test_beta_07_translation_ui_text_contract():
    source = open("ui/texts.py", encoding="utf-8").read()
    handler_source = open("handlers/search.py", encoding="utf-8").read()

    required_text_keys = [
        "contact_show_original",
        "contact_original_message",
        "contact_original_not_found",
        "contact_translated_message_received",
        "contact_translation_failed_original_shown",
        "translation_provider_error",
        "translation_retry_later",
    ]

    for key in required_text_keys:
        assert f'"{key}"' in source

    assert "contact_show_original_pending" not in source
    assert "contact_show_original_pending" not in handler_source
    assert 'callback_data="contact_show_original"' in handler_source
    assert 'callback_data=f"contact_show_original' not in handler_source

@pytest.mark.asyncio
async def test_translation_cache_hit_returns_translation_without_provider_call(db_session):
    data = await create_contact_request_for_translation(db_session)

    try:
        message = await db_session.get(Message, data["result"].first_message_id)
        assert message.translation_status == "pending"

        repository = TranslationRepository(db_session)
        await repository.save_cached_translation(
            source_text=message.original_text,
            source_language="en",
            target_language="ru",
            translated_text="Кешированный перевод",
            provider="fake",
        )
        await db_session.commit()

        provider = FakeTranslationProvider(translated_text="Provider should not be called")
        result = await TranslationService(
            TranslationRepository(db_session),
            provider=provider,
        ).translate_message(message.id)

        await db_session.refresh(message)

        assert provider.calls == []
        assert result.used_translation is True
        assert result.display_text == "Кешированный перевод"
        assert message.translation_status == "translated"
        assert message.translated_text == "Кешированный перевод"
        assert message.translated_language == "ru"

        job_result = await db_session.execute(
            select(TranslationJob).where(TranslationJob.message_id == message.id)
        )
        job = job_result.scalar_one()
        assert job.status == "translated"

    finally:
        await cleanup_beta_07_translation_records(
            db_session,
            user_ids=[data["client_user_id"], data["specialist_user_id"]],
        )
        await cleanup_test_user(db_session, data["client_platform_id"])
        await cleanup_test_user(db_session, data["specialist_platform_id"])
        await cleanup_legal_documents(db_session, data["tenant_id"])

@pytest.mark.asyncio
async def test_translation_cache_miss_calls_provider_saves_cache_and_updates_message(db_session):
    data = await create_contact_request_for_translation(db_session)

    try:
        message = await db_session.get(Message, data["result"].first_message_id)
        provider = FakeTranslationProvider(translated_text="Перевод от провайдера")

        result = await TranslationService(
            TranslationRepository(db_session),
            provider=provider,
        ).translate_message(message.id)

        await db_session.refresh(message)

        assert len(provider.calls) == 1
        assert provider.calls[0]["source_language"] == "en"
        assert provider.calls[0]["target_language"] == "ru"
        assert result.used_translation is True
        assert result.display_text == "Перевод от провайдера"
        assert message.translation_status == "translated"
        assert message.translated_text == "Перевод от провайдера"

        cache_result = await db_session.execute(
            select(TranslationCache).where(
                TranslationCache.source_text_hash == translation_text_hash(message.original_text),
                TranslationCache.source_language == "en",
                TranslationCache.target_language == "ru",
                TranslationCache.provider == "fake",
            )
        )
        assert cache_result.scalar_one_or_none() is not None

        log_result = await db_session.execute(
            select(TranslationLog).where(
                TranslationLog.provider == "fake",
                TranslationLog.status == "translated",
            )
        )
        assert log_result.scalar_one_or_none() is not None

    finally:
        await cleanup_beta_07_translation_records(
            db_session,
            user_ids=[data["client_user_id"], data["specialist_user_id"]],
        )
        await cleanup_test_user(db_session, data["client_platform_id"])
        await cleanup_test_user(db_session, data["specialist_platform_id"])
        await cleanup_legal_documents(db_session, data["tenant_id"])

@pytest.mark.asyncio
async def test_same_language_creates_no_translation_job_and_marks_not_needed(db_session):
    data = await create_contact_request_for_translation(
        db_session,
        original_language="ru",
        message="Здравствуйте, нужна помощь с услугой бета перевода.",
    )

    try:
        message = await db_session.get(Message, data["result"].first_message_id)
        assert message.translation_status == "not_needed"

        job_result = await db_session.execute(
            select(TranslationJob).where(TranslationJob.message_id == message.id)
        )
        assert job_result.scalar_one_or_none() is None

    finally:
        await cleanup_beta_07_translation_records(
            db_session,
            user_ids=[data["client_user_id"], data["specialist_user_id"]],
        )
        await cleanup_test_user(db_session, data["client_platform_id"])
        await cleanup_test_user(db_session, data["specialist_platform_id"])
        await cleanup_legal_documents(db_session, data["tenant_id"])

@pytest.mark.asyncio
async def test_auto_translate_disabled_creates_no_job_and_keeps_original(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-beta-07-client-auto-off",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        db_session.add(
            UserLanguageSetting(
                user_id=specialist_user_id,
                interface_language="ru",
                message_language="ru",
                auto_translate_enabled=False,
                show_original_button=True,
            )
        )
        await db_session.commit()

        result = await ContactChatService(
            ContactChatRepository(db_session)
        ).create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, keep this original because auto translate is disabled.",
            original_language="en",
        )

        message = await db_session.get(Message, result.first_message_id)
        assert message.translation_status == "not_needed"
        assert message.translated_text is None
        assert message.translated_language is None

        job_result = await db_session.execute(
            select(TranslationJob).where(TranslationJob.message_id == message.id)
        )
        assert job_result.scalar_one_or_none() is None

    finally:
        await cleanup_beta_07_translation_records(
            db_session,
            user_ids=[client_user_id, specialist_user_id],
        )
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

@pytest.mark.asyncio
async def test_provider_failure_marks_failed_logs_error_and_returns_original(db_session):
    data = await create_contact_request_for_translation(db_session)

    try:
        message = await db_session.get(Message, data["result"].first_message_id)
        provider = FakeTranslationProvider(fail=True)

        result = await TranslationService(
            TranslationRepository(db_session),
            provider=provider,
        ).translate_message(message.id)

        await db_session.refresh(message)

        assert result.used_translation is False
        assert result.display_text == message.original_text
        assert message.translation_status == "failed"

        job_result = await db_session.execute(
            select(TranslationJob).where(TranslationJob.message_id == message.id)
        )
        job = job_result.scalar_one()
        assert job.status == "retry"
        assert job.retry_count == 1
        assert "fake provider is unavailable" in job.error_message

        log_result = await db_session.execute(
            select(TranslationLog).where(
                TranslationLog.provider == "fake",
                TranslationLog.status == "failed",
            )
        )
        log = log_result.scalar_one_or_none()
        assert log is not None
        assert "fake provider is unavailable" in log.error_message

    finally:
        await cleanup_beta_07_translation_records(
            db_session,
            user_ids=[data["client_user_id"], data["specialist_user_id"]],
        )
        await cleanup_test_user(db_session, data["client_platform_id"])
        await cleanup_test_user(db_session, data["specialist_platform_id"])
        await cleanup_legal_documents(db_session, data["tenant_id"])

@pytest.mark.asyncio
async def test_get_message_for_receiver_uses_translated_text_when_available(db_session):
    data = await create_contact_request_for_translation(db_session)

    try:
        message = await db_session.get(Message, data["result"].first_message_id)

        await TranslationService(
            TranslationRepository(db_session),
            provider=FakeTranslationProvider(translated_text="Текст для специалиста"),
        ).translate_message(message.id)

        display = await TranslationService(
            TranslationRepository(db_session),
            provider=FakeTranslationProvider(),
        ).get_message_for_receiver(
            message_id=message.id,
            receiver_user_id=data["specialist_user_id"],
        )

        assert display.used_translation is True
        assert display.display_text == "Текст для специалиста"
        assert display.original_text == message.original_text

    finally:
        await cleanup_beta_07_translation_records(
            db_session,
            user_ids=[data["client_user_id"], data["specialist_user_id"]],
        )
        await cleanup_test_user(db_session, data["client_platform_id"])
        await cleanup_test_user(db_session, data["specialist_platform_id"])
        await cleanup_legal_documents(db_session, data["tenant_id"])

@pytest.mark.asyncio
async def test_show_original_returns_original_only_for_thread_participant(db_session):
    data = await create_contact_request_for_translation(db_session)
    outsider_platform_id, outsider_user_id, outsider_tenant_id = await create_test_user(
        db_session,
        prefix="test-beta-07-outsider",
    )

    try:
        result = data["result"]

        original = await TranslationService(
            TranslationRepository(db_session),
            provider=FakeTranslationProvider(),
        ).get_original_message_for_thread(
            thread_id=result.thread_id,
            viewer_user_id=data["specialist_user_id"],
        )

        assert original.used_translation is False
        assert original.original_text == "Hello, I need help with translation beta service."

        with pytest.raises(TranslationError):
            await TranslationService(
                TranslationRepository(db_session),
                provider=FakeTranslationProvider(),
            ).get_original_message_for_thread(
                thread_id=result.thread_id,
                viewer_user_id=outsider_user_id,
            )

    finally:
        await cleanup_beta_07_translation_records(
            db_session,
            user_ids=[
                data["client_user_id"],
                data["specialist_user_id"],
                outsider_user_id,
            ],
        )
        await cleanup_test_user(db_session, outsider_platform_id)
        await cleanup_test_user(db_session, data["client_platform_id"])
        await cleanup_test_user(db_session, data["specialist_platform_id"])
        await cleanup_legal_documents(db_session, data["tenant_id"])