import hashlib
from uuid import UUID
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ConversationThread,
    Message,
    Specialist,
    TranslationCache,
    TranslationJob,
    TranslationLog,
    User,
    UserLanguageSetting,
)


SUPPORTED_TRANSLATION_LANGUAGES = {"ru", "en", "pt"}


def normalize_translation_language(language: str | None) -> str:
    return language if language in SUPPORTED_TRANSLATION_LANGUAGES else "ru"


def translation_text_hash(text: str) -> str:
    normalized = (text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class TranslationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_message(self, message_id: UUID) -> Message | None:
        return await self.session.get(Message, message_id)

    async def get_pending_job_for_message(
        self,
        message_id: UUID,
    ) -> TranslationJob | None:
        result = await self.session.execute(
            select(TranslationJob)
            .where(
                TranslationJob.message_id == message_id,
                TranslationJob.status.in_(["pending", "retry"]),
            )
            .order_by(TranslationJob.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_pending_jobs(self, limit: int = 20) -> list[TranslationJob]:
        result = await self.session.execute(
            select(TranslationJob)
            .where(TranslationJob.status.in_(["pending", "retry"]))
            .order_by(TranslationJob.created_at.asc())
            .limit(max(1, min(int(limit), 100)))
        )
        return list(result.scalars().all())

    async def get_user_message_language(self, user_id: UUID) -> str:
        settings_result = await self.session.execute(
            select(UserLanguageSetting).where(UserLanguageSetting.user_id == user_id)
        )
        settings = settings_result.scalar_one_or_none()

        if settings and settings.message_language:
            return normalize_translation_language(settings.message_language)

        user = await self.session.get(User, user_id)
        return normalize_translation_language(user.language_code if user else None)

    async def is_auto_translate_enabled(self, user_id: UUID) -> bool:
        settings_result = await self.session.execute(
            select(UserLanguageSetting).where(UserLanguageSetting.user_id == user_id)
        )
        settings = settings_result.scalar_one_or_none()

        if settings is None:
            return True

        return bool(settings.auto_translate_enabled)

    async def get_language_settings(self, user_id: UUID) -> UserLanguageSetting:
        result = await self.session.execute(
            select(UserLanguageSetting).where(UserLanguageSetting.user_id == user_id)
        )
        settings = result.scalar_one_or_none()
        if settings:
            return settings

        user = await self.session.get(User, user_id)
        language = normalize_translation_language(user.language_code if user else None)

        settings = UserLanguageSetting(
            user_id=user_id,
            interface_language=language,
            message_language=language,
            auto_translate_enabled=True,
            show_original_button=True,
            updated_at=datetime.utcnow(),
        )
        self.session.add(settings)
        await self.session.flush()
        return settings

    async def update_language_settings(
        self,
        *,
        user_id: UUID,
        message_language: str | None = None,
        auto_translate_enabled: bool | None = None,
        show_original_button: bool | None = None,
    ) -> UserLanguageSetting:
        settings = await self.get_language_settings(user_id)

        if message_language is not None:
            settings.message_language = normalize_translation_language(message_language)

        if auto_translate_enabled is not None:
            settings.auto_translate_enabled = auto_translate_enabled

        if show_original_button is not None:
            settings.show_original_button = show_original_button

        settings.updated_at = datetime.utcnow()
        await self.session.flush()
        return settings

    async def get_cached_translation(
        self,
        *,
        source_text: str,
        source_language: str,
        target_language: str,
        provider: str,
    ) -> TranslationCache | None:
        result = await self.session.execute(
            select(TranslationCache)
            .where(
                TranslationCache.source_text_hash == translation_text_hash(source_text),
                TranslationCache.source_language == normalize_translation_language(source_language),
                TranslationCache.target_language == normalize_translation_language(target_language),
                TranslationCache.provider == provider,
            )
            .order_by(TranslationCache.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def save_cached_translation(
        self,
        *,
        source_text: str,
        source_language: str,
        target_language: str,
        translated_text: str,
        provider: str,
    ) -> TranslationCache:
        cached = TranslationCache(
            source_text_hash=translation_text_hash(source_text),
            source_language=normalize_translation_language(source_language),
            target_language=normalize_translation_language(target_language),
            translated_text=translated_text,
            provider=provider,
        )
        self.session.add(cached)
        await self.session.flush()
        return cached

    async def mark_message_not_needed(self, message: Message) -> Message:
        message.translation_status = "not_needed"
        message.translated_text = None
        message.translated_language = None
        await self.session.flush()
        return message

    async def mark_message_translated(
        self,
        *,
        message: Message,
        translated_text: str,
        target_language: str,
    ) -> Message:
        message.translated_text = translated_text
        message.translated_language = normalize_translation_language(target_language)
        message.translation_status = "translated"
        await self.session.flush()
        return message

    async def mark_message_failed(self, message: Message) -> Message:
        message.translation_status = "failed"
        await self.session.flush()
        return message

    async def mark_job_translated(self, job: TranslationJob) -> TranslationJob:
        job.status = "translated"
        job.error_message = None
        await self.session.flush()
        return job

    async def mark_job_failed(
        self,
        *,
        job: TranslationJob,
        error_message: str,
    ) -> TranslationJob:
        job.retry_count = int(job.retry_count or 0) + 1
        job.error_message = error_message[:1000]

        if job.retry_count >= int(job.max_retries or 3):
            job.status = "dead_letter"
        else:
            job.status = "retry"

        await self.session.flush()
        return job

    async def log_translation(
        self,
        *,
        tenant_id: UUID,
        job_id: UUID | None,
        provider: str,
        source_language: str,
        target_language: str,
        status: str,
        latency_ms: int | None = None,
        error_message: str | None = None,
    ) -> TranslationLog:
        log = TranslationLog(
            tenant_id=tenant_id,
            job_id=job_id,
            provider=provider,
            source_language=normalize_translation_language(source_language),
            target_language=normalize_translation_language(target_language),
            status=status,
            latency_ms=latency_ms,
            error_message=error_message[:1000] if error_message else None,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def get_latest_received_message_in_thread(
        self,
        *,
        thread_id: UUID,
        viewer_user_id: UUID,
    ) -> Message | None:
        result = await self.session.execute(
            select(Message)
            .where(
                Message.thread_id == thread_id,
                Message.receiver_user_id == viewer_user_id,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_thread_for_user(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
    ) -> ConversationThread | None:
        result = await self.session.execute(
            select(ConversationThread).where(
                ConversationThread.id == thread_id,
                (
                    (ConversationThread.client_user_id == user_id)
                    | (
                        ConversationThread.specialist_id.in_(
                            select(Specialist.id).where(Specialist.user_id == user_id)
                        )
                    )
                ),
            )
        )
        return result.scalar_one_or_none()