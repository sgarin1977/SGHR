import os
import time
from dataclasses import dataclass
from uuid import UUID

import httpx

from database.repositories.translation import (
    TranslationRepository,
    normalize_translation_language,
)
from database.repositories.event import EventRepository
from database.repositories.user import UserRepository

class TranslationError(Exception):
    pass


class TranslationProviderError(TranslationError):
    pass


@dataclass
class TranslationDisplayResult:
    message_id: UUID
    original_text: str
    display_text: str
    original_language: str
    display_language: str
    translation_status: str
    used_translation: bool

@dataclass(frozen=True)
class NotificationTranslationResult:
    display_text: str
    used_translation: bool
    translation_status: str

@dataclass(frozen=True)
class TranslationSettingsView:
    interface_language: str
    message_language: str
    auto_translate_enabled: bool
    show_original_button: bool

class LibreTranslateProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("TRANSLATION_BASE_URL")
            or "http://127.0.0.1:5000"
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("TRANSLATION_API_KEY")
        self.timeout_seconds = float(
            timeout_seconds
            or os.getenv("TRANSLATION_TIMEOUT_SECONDS")
            or 15
        )
        self.provider_name = "libretranslate"

    async def translate(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        payload = {
            "q": text,
            "source": source_language,
            "target": target_language,
            "format": "text",
        }

        if self.api_key:
            payload["api_key"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/translate",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise TranslationProviderError(f"Translation provider failed: {exc}") from exc

        translated_text = data.get("translatedText")
        if not translated_text:
            raise TranslationProviderError("Translation provider returned empty text.")

        return str(translated_text)


class TranslationService:
    def __init__(
        self,
        repository: TranslationRepository,
        provider: LibreTranslateProvider | None = None,
        *,
        cache_enabled: bool | None = None,
    ):
        self.repository = repository
        self.events = EventRepository(repository.session)
        self.users = UserRepository(repository.session)
        self.provider = provider or LibreTranslateProvider()
        self.cache_enabled = (
            cache_enabled
            if cache_enabled is not None
            else os.getenv("TRANSLATION_CACHE_ENABLED", "true").lower() == "true"
        )

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "provider_name", "libretranslate")

    async def get_language_settings_view(
        self,
        *,
        user_id: UUID,
    ) -> TranslationSettingsView:
        try:
            settings = (
                await self.repository.get_language_settings(
                    user_id
                )
            )
            await self.repository.session.commit()
        except Exception as exc:
            await self.repository.session.rollback()
            raise TranslationError(
                "Unable to load language settings."
            ) from exc

        return TranslationSettingsView(
            interface_language=(
                settings.interface_language
            ),
            message_language=settings.message_language,
            auto_translate_enabled=bool(
                settings.auto_translate_enabled
            ),
            show_original_button=bool(
                settings.show_original_button
            ),
        )

    async def update_interface_language(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        language_code: str,
        source: str,
    ) -> TranslationSettingsView:
        normalized_language = (
            normalize_translation_language(
                language_code
            )
        )
        normalized_source = (
            source or "settings"
        ).strip()[:100]

        try:
            settings = (
                await self.repository
                .update_language_settings(
                    user_id=user_id,
                    interface_language=(
                        normalized_language
                    ),
                )
            )

            await self.users.update_language_code(
                user_id=user_id,
                language_code=normalized_language,
            )

            await self.events.create_event(
                event_type="settings_changed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="user",
                entity_id=user_id,
                payload={
                    "setting": "interface_language",
                    "value": normalized_language,
                    "source": normalized_source,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception as exc:
            await self.repository.session.rollback()
            raise TranslationError(
                "Unable to update interface language."
            ) from exc

        return TranslationSettingsView(
            interface_language=(
                settings.interface_language
            ),
            message_language=settings.message_language,
            auto_translate_enabled=bool(
                settings.auto_translate_enabled
            ),
            show_original_button=bool(
                settings.show_original_button
            ),
        )

    async def update_message_language(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        language_code: str,
        source: str,
    ) -> TranslationSettingsView:
        normalized_language = (
            normalize_translation_language(
                language_code
            )
        )
        normalized_source = (
            source or "settings"
        ).strip()[:100]

        try:
            settings = (
                await self.repository
                .update_language_settings(
                    user_id=user_id,
                    message_language=normalized_language,
                )
            )

            await self.events.create_event(
                event_type="settings_changed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="user",
                entity_id=user_id,
                payload={
                    "setting": "message_language",
                    "value": normalized_language,
                    "source": normalized_source,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception as exc:
            await self.repository.session.rollback()
            raise TranslationError(
                "Unable to update message language."
            ) from exc

        return TranslationSettingsView(
            interface_language=(
                settings.interface_language
            ),
            message_language=settings.message_language,
            auto_translate_enabled=bool(
                settings.auto_translate_enabled
            ),
            show_original_button=bool(
                settings.show_original_button
            ),
        )

    async def toggle_show_original(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        source: str,
    ) -> TranslationSettingsView:
        normalized_source = (
            source or "settings"
        ).strip()[:100]

        try:
            current_settings = (
                await self.repository.get_language_settings(
                    user_id
                )
            )
            new_value = not bool(
                current_settings.show_original_button
            )

            settings = (
                await self.repository
                .update_language_settings(
                    user_id=user_id,
                    show_original_button=new_value,
                )
            )

            await self.events.create_event(
                event_type="settings_changed",
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="user",
                entity_id=user_id,
                payload={
                    "setting": "show_original_button",
                    "value": new_value,
                    "source": normalized_source,
                },
                platform="telegram",
            )

            await self.repository.session.commit()

        except Exception as exc:
            await self.repository.session.rollback()
            raise TranslationError(
                "Unable to update original text visibility."
            ) from exc

        return TranslationSettingsView(
            interface_language=(
                settings.interface_language
            ),
            message_language=settings.message_language,
            auto_translate_enabled=bool(
                settings.auto_translate_enabled
            ),
            show_original_button=bool(
                settings.show_original_button
            ),
        )

    async def resolve_interface_language(
        self,
        *,
        user_id: UUID,
        fallback_language: str | None,
    ) -> str:
        settings = await self.get_language_settings_view(
            user_id=user_id,
        )

        return normalize_translation_language(
            settings.interface_language
            or fallback_language
        )

    async def translate_notification_message(
        self,
        *,
        message_id: UUID,
        receiver_user_id: UUID,
    ) -> NotificationTranslationResult:
        try:
            translated = (
                await self.translate_message(
                    message_id
                )
            )

            display = (
                await self.get_message_for_receiver(
                    message_id=message_id,
                    receiver_user_id=(
                        receiver_user_id
                    ),
                )
            )

            return NotificationTranslationResult(
                display_text=display.display_text,
                used_translation=(
                    translated.used_translation
                ),
                translation_status=(
                    translated.translation_status
                ),
            )

        except TranslationError:
            message = (
                await self.repository.get_message(
                    message_id
                )
            )

            return NotificationTranslationResult(
                display_text=(
                    message.original_text
                    if message
                    else ""
                ),
                used_translation=False,
                translation_status="failed",
            )

    async def translate_message(self, message_id: UUID) -> TranslationDisplayResult:
        message = await self.repository.get_message(message_id)
        if not message:
            raise TranslationError("Message not found.")

        source_language = normalize_translation_language(message.original_language)
        target_language = await self.repository.get_user_message_language(
            message.receiver_user_id
        )
        auto_translate_enabled = await self.repository.is_auto_translate_enabled(
            message.receiver_user_id
        )

        if not auto_translate_enabled or source_language == target_language:
            await self.repository.mark_message_not_needed(message)
            await self.repository.session.commit()
            return TranslationDisplayResult(
                message_id=message.id,
                original_text=message.original_text,
                display_text=message.original_text,
                original_language=source_language,
                display_language=source_language,
                translation_status="not_needed",
                used_translation=False,
            )

        job = await self.repository.get_pending_job_for_message(message.id)

        if self.cache_enabled:
            cached = await self.repository.get_cached_translation(
                source_text=message.original_text,
                source_language=source_language,
                target_language=target_language,
                provider=self.provider_name,
            )
            if cached:
                await self.repository.mark_message_translated(
                    message=message,
                    translated_text=cached.translated_text,
                    target_language=target_language,
                )
                if job:
                    await self.repository.mark_job_translated(job)
                await self.repository.log_translation(
                    tenant_id=message.tenant_id,
                    job_id=job.id if job else None,
                    provider=self.provider_name,
                    source_language=source_language,
                    target_language=target_language,
                    status="cache_hit",
                    latency_ms=0,
                )
                await self.repository.session.commit()
                return TranslationDisplayResult(
                    message_id=message.id,
                    original_text=message.original_text,
                    display_text=cached.translated_text,
                    original_language=source_language,
                    display_language=target_language,
                    translation_status="translated",
                    used_translation=True,
                )

        started_at = time.monotonic()

        try:
            translated_text = await self.provider.translate(
                text=message.original_text,
                source_language=source_language,
                target_language=target_language,
            )
            latency_ms = int((time.monotonic() - started_at) * 1000)

            await self.repository.mark_message_translated(
                message=message,
                translated_text=translated_text,
                target_language=target_language,
            )
            if job:
                await self.repository.mark_job_translated(job)

            if self.cache_enabled:
                await self.repository.save_cached_translation(
                    source_text=message.original_text,
                    source_language=source_language,
                    target_language=target_language,
                    translated_text=translated_text,
                    provider=self.provider_name,
                )

            await self.repository.log_translation(
                tenant_id=message.tenant_id,
                job_id=job.id if job else None,
                provider=self.provider_name,
                source_language=source_language,
                target_language=target_language,
                status="translated",
                latency_ms=latency_ms,
            )
            await self.repository.session.commit()

            return TranslationDisplayResult(
                message_id=message.id,
                original_text=message.original_text,
                display_text=translated_text,
                original_language=source_language,
                display_language=target_language,
                translation_status="translated",
                used_translation=True,
            )

        except TranslationProviderError as exc:
            latency_ms = int((time.monotonic() - started_at) * 1000)
            error_message = str(exc)

            await self.repository.mark_message_failed(message)
            if job:
                await self.repository.mark_job_failed(
                    job=job,
                    error_message=error_message,
                )

            await self.repository.log_translation(
                tenant_id=message.tenant_id,
                job_id=job.id if job else None,
                provider=self.provider_name,
                source_language=source_language,
                target_language=target_language,
                status="failed",
                latency_ms=latency_ms,
                error_message=error_message,
            )
            await self.repository.session.commit()

            return TranslationDisplayResult(
                message_id=message.id,
                original_text=message.original_text,
                display_text=message.original_text,
                original_language=source_language,
                display_language=source_language,
                translation_status="failed",
                used_translation=False,
            )

    async def process_pending_jobs(self, limit: int = 20) -> list[TranslationDisplayResult]:
        jobs = await self.repository.list_pending_jobs(limit=limit)
        results = []

        for job in jobs:
            results.append(await self.translate_message(job.message_id))

        return results

    async def get_message_for_receiver(
        self,
        *,
        message_id: UUID,
        receiver_user_id: UUID,
    ) -> TranslationDisplayResult:
        message = await self.repository.get_message(message_id)
        if not message:
            raise TranslationError("Message not found.")

        if message.receiver_user_id != receiver_user_id:
            raise TranslationError("Message is not addressed to this user.")

        if (
            message.translation_status == "translated"
            and message.translated_text
            and message.translated_language
        ):
            return TranslationDisplayResult(
                message_id=message.id,
                original_text=message.original_text,
                display_text=message.translated_text,
                original_language=normalize_translation_language(message.original_language),
                display_language=normalize_translation_language(message.translated_language),
                translation_status=message.translation_status,
                used_translation=True,
            )

        return TranslationDisplayResult(
            message_id=message.id,
            original_text=message.original_text,
            display_text=message.original_text,
            original_language=normalize_translation_language(message.original_language),
            display_language=normalize_translation_language(message.original_language),
            translation_status=message.translation_status,
            used_translation=False,
        )

    async def get_original_message_for_thread(
        self,
        *,
        thread_id: UUID,
        viewer_user_id: UUID,
    ) -> TranslationDisplayResult:
        thread = await self.repository.get_thread_for_user(
            thread_id=thread_id,
            user_id=viewer_user_id,
        )
        if not thread:
            raise TranslationError("Conversation thread not found.")

        message = await self.repository.get_latest_received_message_in_thread(
            thread_id=thread_id,
            viewer_user_id=viewer_user_id,
        )
        if not message:
            raise TranslationError("No received message found.")

        return TranslationDisplayResult(
            message_id=message.id,
            original_text=message.original_text,
            display_text=message.original_text,
            original_language=normalize_translation_language(message.original_language),
            display_language=normalize_translation_language(message.original_language),
            translation_status=message.translation_status,
            used_translation=False,
        )