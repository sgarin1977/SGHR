import asyncio
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from database.models import (
    ContactRequest,
    ConversationThread,
    EventLog,
    LegalDocument,
    Message,
    MessageReadReceipt,
    Notification,
    Specialist,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistService,
    TranslationCache,
    TranslationJob,
    TranslationLog,
    User,
    UserAccount,
    UserConsent,
    UserLanguageSetting,
    UserRoleMapping,
)
from database.repositories.contact import ContactChatRepository
from database.repositories.legal import LegalRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.translation import TranslationRepository
from database.repositories.user import UserRepository
from database.session import async_session
from services.contact_chat import ContactChatService
from services.legal import REQUIRED_SPECIALIST_CONSENTS, LegalService
from services.specialist import (
    SpecialistRegistrationData,
    SpecialistService as SpecialistRegistrationService,
)
from services.translation import (
    TranslationProviderError,
    TranslationService,
)


LEGAL_VERSION = "smoke-beta-0.7"


class SmokeProvider:
    provider_name = "smoke"

    def __init__(self, translated_text="Переведенное smoke сообщение", fail=False):
        self.translated_text = translated_text
        self.fail = fail
        self.calls = []

    async def translate(self, *, text: str, source_language: str, target_language: str) -> str:
        self.calls.append(
            {
                "text": text,
                "source_language": source_language,
                "target_language": target_language,
            }
        )
        if self.fail:
            raise TranslationProviderError("smoke provider unavailable")
        return self.translated_text


async def cleanup_user(session, platform_user_id: str):
    await session.rollback()

    account = (
        await session.execute(
            select(UserAccount).where(
                UserAccount.platform == "telegram",
                UserAccount.platform_user_id == platform_user_id,
            )
        )
    ).scalar_one_or_none()

    if not account:
        await session.rollback()
        return

    user_id = account.user_id

    thread_result = await session.execute(
        select(ConversationThread).where(ConversationThread.client_user_id == user_id)
    )
    client_threads = thread_result.scalars().all()
    client_thread_ids = [item.id for item in client_threads]
    client_contact_request_ids = [
        item.context_id
        for item in client_threads
        if item.context_type == "contact_request" and item.context_id is not None
    ]

    if client_thread_ids:
        messages = (
            await session.execute(
                select(Message).where(Message.thread_id.in_(client_thread_ids))
            )
        ).scalars().all()
        message_ids = [item.id for item in messages]

        if message_ids:
            await session.execute(
                delete(MessageReadReceipt).where(
                    MessageReadReceipt.message_id.in_(message_ids)
                )
            )
            await session.execute(
                delete(TranslationLog).where(
                    TranslationLog.job_id.in_(
                        select(TranslationJob.id).where(
                            TranslationJob.message_id.in_(message_ids)
                        )
                    )
                )
            )
            await session.execute(
                delete(TranslationJob).where(TranslationJob.message_id.in_(message_ids))
            )

        await session.execute(delete(Message).where(Message.thread_id.in_(client_thread_ids)))
        await session.execute(
            delete(ConversationThread).where(ConversationThread.id.in_(client_thread_ids))
        )

    if client_contact_request_ids:
        await session.execute(
            delete(ContactRequest).where(ContactRequest.id.in_(client_contact_request_ids))
        )

    specialist = (
        await session.execute(select(Specialist).where(Specialist.user_id == user_id))
    ).scalar_one_or_none()

    if specialist:
        contact_requests = (
            await session.execute(
                select(ContactRequest).where(ContactRequest.specialist_id == specialist.id)
            )
        ).scalars().all()
        contact_request_ids = [item.id for item in contact_requests]

        if contact_request_ids:
            threads = (
                await session.execute(
                    select(ConversationThread).where(
                        ConversationThread.context_id.in_(contact_request_ids)
                    )
                )
            ).scalars().all()
            thread_ids = [item.id for item in threads]

            if thread_ids:
                messages = (
                    await session.execute(
                        select(Message).where(Message.thread_id.in_(thread_ids))
                    )
                ).scalars().all()
                message_ids = [item.id for item in messages]

                if message_ids:
                    await session.execute(
                        delete(MessageReadReceipt).where(
                            MessageReadReceipt.message_id.in_(message_ids)
                        )
                    )
                    await session.execute(
                        delete(TranslationLog).where(
                            TranslationLog.job_id.in_(
                                select(TranslationJob.id).where(
                                    TranslationJob.message_id.in_(message_ids)
                                )
                            )
                        )
                    )
                    await session.execute(
                        delete(TranslationJob).where(
                            TranslationJob.message_id.in_(message_ids)
                        )
                    )

                await session.execute(delete(Message).where(Message.thread_id.in_(thread_ids)))
                await session.execute(
                    delete(ConversationThread).where(ConversationThread.id.in_(thread_ids))
                )

            await session.execute(
                delete(ContactRequest).where(ContactRequest.id.in_(contact_request_ids))
            )

        await session.execute(
            delete(SpecialistService).where(SpecialistService.specialist_id == specialist.id)
        )
        await session.execute(
            delete(SpecialistLanguage).where(SpecialistLanguage.specialist_id == specialist.id)
        )
        await session.execute(
            delete(SpecialistLocation).where(SpecialistLocation.specialist_id == specialist.id)
        )
        await session.execute(delete(Specialist).where(Specialist.id == specialist.id))

    await session.execute(delete(Notification).where(Notification.user_id == user_id))
    await session.execute(delete(EventLog).where(EventLog.user_id == user_id))
    await session.execute(delete(UserLanguageSetting).where(UserLanguageSetting.user_id == user_id))
    await session.execute(delete(UserConsent).where(UserConsent.user_id == user_id))
    await session.execute(delete(UserRoleMapping).where(UserRoleMapping.user_id == user_id))
    await session.execute(delete(UserAccount).where(UserAccount.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()


async def cleanup_legal_documents(session, tenant_id):
    await session.rollback()
    await session.execute(
        delete(UserConsent).where(
            UserConsent.tenant_id == tenant_id,
            UserConsent.version == LEGAL_VERSION,
        )
    )
    await session.execute(
        delete(LegalDocument).where(
            LegalDocument.tenant_id == tenant_id,
            LegalDocument.version == LEGAL_VERSION,
        )
    )
    await session.commit()


async def create_user(session, prefix: str, language_code: str = "ru"):
    platform_user_id = f"{prefix}-{uuid.uuid4()}"

    user_id = await UserRepository(session).create_telegram_user_core(
        platform_user_id=platform_user_id,
        username=prefix,
        first_name="Smoke",
        last_name="Translation",
        language_code=language_code,
        role="client",
    )

    user = await session.get(User, user_id)
    if not user or not user.tenant_id:
        raise SystemExit("FAIL: user was not created")

    return platform_user_id, user.id, user.tenant_id


async def accept_specialist_consents(session, tenant_id, user_id):
    for doc_type in REQUIRED_SPECIALIST_CONSENTS:
        session.add(
            LegalDocument(
                tenant_id=tenant_id,
                doc_type=doc_type,
                version=LEGAL_VERSION,
                language="ru",
                title=f"{doc_type} smoke beta 0.7",
                content_text=f"{doc_type} smoke beta 0.7 content",
                status="active",
            )
        )
    await session.commit()

    await LegalService(LegalRepository(session)).accept_required_specialist_consents(
        tenant_id=tenant_id,
        user_id=user_id,
        language="ru",
        platform="telegram",
    )


async def create_specialist(session):
    from tests.test_beta_05_geo_search_filter import get_reference_data

    specialist_platform_id, specialist_user_id, tenant_id = await create_user(
        session,
        prefix="smoke-translation-specialist",
        language_code="ru",
    )
    refs = await get_reference_data(session)

    await cleanup_legal_documents(session, tenant_id)
    await accept_specialist_consents(session, tenant_id, specialist_user_id)

    specialist = await SpecialistRegistrationService(
        SpecialistRepository(session)
    ).create_pending_profile(
        SpecialistRegistrationData(
            tenant_id=tenant_id,
            user_id=specialist_user_id,
            category_id=refs["category_id"],
            profession_id=refs["profession_id"],
            country_id=refs["country_id"],
            city_id=refs["city_id"],
            display_name="Smoke Translation Specialist",
            short_description="Smoke specialist for translation beta.",
            full_description="Smoke specialist for translation beta 0.7.",
            price_from=40,
            price_to=80,
            currency="EUR",
            price_unit="service",
            work_format="mixed",
            latitude=refs["city_latitude"],
            longitude=refs["city_longitude"],
            service_radius_km=25,
            languages=["ru", "en"],
            service_title="Smoke translation service",
            service_description="Service created by translation smoke test.",
            contact_text="Contact inside SGHR beta chat",
        )
    )

    specialist.status = "active"
    await session.commit()

    return specialist_platform_id, specialist_user_id, tenant_id, specialist


async def main():
    client_platform_id = None
    specialist_platform_id = None
    tenant_id = None

    async with async_session() as session:
        try:
            client_platform_id, client_user_id, client_tenant_id = await create_user(
                session,
                prefix="smoke-translation-client",
                language_code="en",
            )
            specialist_platform_id, specialist_user_id, tenant_id, specialist = (
                await create_specialist(session)
            )

            request = await ContactChatService(
                ContactChatRepository(session)
            ).create_contact_request(
                tenant_id=tenant_id,
                from_user_id=client_user_id,
                specialist_id=specialist.id,
                message="Hello, I need help with translation smoke test.",
                original_language="en",
            )

            message = await session.get(Message, request.first_message_id)
            if not message:
                raise SystemExit("FAIL: message was not created")
            if message.translation_status != "pending":
                raise SystemExit(f"FAIL: expected pending, got {message.translation_status}")

            provider = SmokeProvider(translated_text="Привет, нужен smoke перевод.")
            translated = await TranslationService(
                TranslationRepository(session),
                provider=provider,
            ).translate_message(message.id)

            await session.refresh(message)

            if not translated.used_translation:
                raise SystemExit("FAIL: translated message did not use translation")
            if translated.display_text != "Привет, нужен smoke перевод.":
                raise SystemExit("FAIL: unexpected translated text")
            if message.translation_status != "translated":
                raise SystemExit(f"FAIL: message status is {message.translation_status}")
            if message.translated_language != "ru":
                raise SystemExit(f"FAIL: translated language is {message.translated_language}")

            cached_provider = SmokeProvider(translated_text="SHOULD NOT BE USED")
            message.translation_status = "pending"
            message.translated_text = None
            message.translated_language = None
            await session.commit()

            cached = await TranslationService(
                TranslationRepository(session),
                provider=cached_provider,
            ).translate_message(message.id)

            if cached_provider.calls:
                raise SystemExit("FAIL: cache hit called provider")
            if cached.display_text != "Привет, нужен smoke перевод.":
                raise SystemExit("FAIL: cache hit returned wrong text")

            fail_request = await ContactChatService(
                ContactChatRepository(session)
            ).send_thread_message(
                thread_id=request.thread_id,
                sender_user_id=specialist_user_id,
                text="Это сообщение проверяет fallback перевода.",
                original_language="ru",
            )

            failing_provider = SmokeProvider(fail=True)
            failed = await TranslationService(
                TranslationRepository(session),
                provider=failing_provider,
            ).translate_message(fail_request.message_id)

            failed_message = await session.get(Message, fail_request.message_id)
            if failed.used_translation:
                raise SystemExit("FAIL: failed provider marked translation as used")
            if failed_message.translation_status != "failed":
                raise SystemExit("FAIL: failed provider did not mark message failed")

            original = await TranslationService(
                TranslationRepository(session),
                provider=SmokeProvider(),
            ).get_original_message_for_thread(
                thread_id=request.thread_id,
                viewer_user_id=client_user_id,
            )

            if original.original_text != "Это сообщение проверяет fallback перевода.":
                raise SystemExit("FAIL: show original returned wrong message")

            log_count = (
                await session.execute(
                    select(TranslationLog).where(TranslationLog.provider == "smoke")
                )
            ).scalars().all()

            if not log_count:
                raise SystemExit("FAIL: translation logs were not created")

            print("OK: translation smoke passed")
            print("contact_request_id:", request.contact_request_id)
            print("thread_id:", request.thread_id)
            print("translated_message_id:", message.id)
            print("fallback_message_id:", fail_request.message_id)

        finally:
            if client_platform_id:
                await cleanup_user(session, client_platform_id)
            if specialist_platform_id:
                await cleanup_user(session, specialist_platform_id)
            if tenant_id:
                await cleanup_legal_documents(session, tenant_id)
            await session.execute(delete(TranslationCache).where(TranslationCache.provider == "smoke"))
            await session.execute(delete(TranslationLog).where(TranslationLog.provider == "smoke"))
            await session.commit()


if __name__ == "__main__":
    asyncio.run(main())