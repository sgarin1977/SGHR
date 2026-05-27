import uuid

from sqlalchemy import delete, select

from database.models import (
    ContactRequest,
    ConversationThread,
    EventLog,
    LegalDocument,
    Message,
    Notification,
    Specialist,
    SpecialistLanguage,
    SpecialistLocation,
    SpecialistService,
    User,
    UserAccount,
    UserConsent,
    UserRoleMapping,
    MessageReadReceipt,
    AbuseEvent,
    RateLimitRule,
    TranslationJob,
)

from database.repositories.contact import ContactChatRepository
from database.repositories.legal import LegalRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from services.contact_chat import ContactChatError, ContactChatService
from services.legal import REQUIRED_SPECIALIST_CONSENTS, LegalService
from services.specialist import (
    SpecialistRegistrationData,
    SpecialistService as SpecialistRegistrationService,
)
def test_beta_06_orm_models_exist_for_contact_chat():
    source = open("database/models.py", encoding="utf-8").read()

    required_fragments = [
        "class ContactRequest",
        '__tablename__ = "contact_requests"',
        "from_user_id",
        "specialist_id",
        "original_language",
        "class ConversationThread",
        '__tablename__ = "conversation_threads"',
        "context_type",
        "context_id",
        "client_user_id",
        "waiting_specialist",
        "class Message",
        '__tablename__ = "messages"',
        "sender_user_id",
        "receiver_user_id",
        "original_text",
        "translation_status",
        "class MessageReadReceipt",
        '__tablename__ = "message_read_receipts"',
        "read_at",
        "class Notification",
        '__tablename__ = "notifications"',
        "notification_type",
        "channel",
        "payload",
        "sent_at",
        "class TranslationJob",
        '__tablename__ = "translation_jobs"',
        "source_language",
        "target_language",
        "max_retries",
    ]

    for fragment in required_fragments:
        assert fragment in source

def test_beta_06_contact_repository_contract_exists():
    source = open("database/repositories/contact.py", encoding="utf-8").read()

    required_fragments = [
        "class ContactChatRepository",
        "get_active_specialist",
        "create_contact_request_with_thread",
        "ContactRequest(",
        "ConversationThread(",
        "Message(",
        "Notification(",
        "contact_request_created",
        "thread_created",
        "message_sent",
        "pending",
        "waiting_specialist",
        "get_thread_for_user",
        "mark_message_read",
        "MessageReadReceipt(",
        "create_thread_message",
        "message_received",
        "in_discussion",
        "mark_thread_message_read",
        "Only message receiver can mark it as read",
        "_create_translation_job_if_needed",
        "TranslationJob(",
        'status="pending"',
    ]

    for fragment in required_fragments:
        assert fragment in source

def test_beta_06_contact_service_contract_exists():
    source = open("services/contact_chat.py", encoding="utf-8").read()

    required_fragments = [
        "class ContactChatError",
        "class ContactRequestResult",
        "class ContactChatService",
        "_validate_contact_message",
        "Contact message must be at least 10 characters",
        "get_active_specialist",
        "Specialist is not available for contact",
        "You cannot contact your own specialist profile",
        "create_contact_request_with_thread",
        "contact_request_id",
        "thread_id",
        "first_message_id",
        "notification_id",
        "specialist_user_id",
        "class ContactThreadMessageResult",
        "send_thread_message",
        "class ContactReadReceiptResult",
        "mark_thread_message_read",
    ]

    for fragment in required_fragments:
        assert fragment in source


def test_contact_service_rejects_short_message():
    from services.contact_chat import ContactChatError, ContactChatService

    service = ContactChatService(repository=None)

    try:
        service._validate_contact_message("short")
    except ContactChatError as exc:
        assert "at least 10 characters" in str(exc)
    else:
        raise AssertionError("Short contact message was not rejected")


def test_contact_service_normalizes_language():
    from services.contact_chat import ContactChatService

    service = ContactChatService(repository=None)

    assert service._normalize_language("ru") == "ru"
    assert service._normalize_language("en") == "en"
    assert service._normalize_language("pt") == "pt"
    assert service._normalize_language("uk") == "ru"
    assert service._normalize_language(None) == "ru"

LEGAL_TEST_VERSION = "test-beta-0.6"


async def cleanup_test_user(session, platform_user_id: str):
    await session.rollback()

    account_result = await session.execute(
        select(UserAccount).where(
            UserAccount.platform == "telegram",
            UserAccount.platform_user_id == platform_user_id,
        )
    )
    account = account_result.scalar_one_or_none()

    if not account:
        await session.rollback()
        return

    user_id = account.user_id
    client_contact_requests_result = await session.execute(
        select(ContactRequest).where(ContactRequest.from_user_id == user_id)
    )
    client_contact_requests = client_contact_requests_result.scalars().all()
    client_contact_request_ids = [item.id for item in client_contact_requests]

    if client_contact_request_ids:
        thread_result = await session.execute(
            select(ConversationThread).where(
                ConversationThread.context_id.in_(client_contact_request_ids)
            )
        )
        threads = thread_result.scalars().all()
        thread_ids = [item.id for item in threads]

        if thread_ids:
            message_result = await session.execute(
                select(Message).where(Message.thread_id.in_(thread_ids))
            )
            messages = message_result.scalars().all()
            message_ids = [item.id for item in messages]

            if message_ids:
                await session.execute(
                    delete(MessageReadReceipt).where(
                        MessageReadReceipt.message_id.in_(message_ids)
                    )
                )

            await session.execute(delete(Message).where(Message.thread_id.in_(thread_ids)))
            await session.execute(
                delete(ConversationThread).where(ConversationThread.id.in_(thread_ids))
            )

        await session.execute(
            delete(ContactRequest).where(ContactRequest.id.in_(client_contact_request_ids))
        )
    specialist_result = await session.execute(
        select(Specialist).where(Specialist.user_id == user_id)
    )
    specialist = specialist_result.scalar_one_or_none()

    if specialist:
        contact_requests_result = await session.execute(
            select(ContactRequest).where(ContactRequest.specialist_id == specialist.id)
        )
        contact_requests = contact_requests_result.scalars().all()
        contact_request_ids = [item.id for item in contact_requests]

        if contact_request_ids:
            thread_result = await session.execute(
                select(ConversationThread).where(
                    ConversationThread.context_id.in_(contact_request_ids)
                )
            )
            threads = thread_result.scalars().all()
            thread_ids = [item.id for item in threads]

            if thread_ids:
                message_result = await session.execute(
                    select(Message).where(Message.thread_id.in_(thread_ids))
                )
                messages = message_result.scalars().all()
                message_ids = [item.id for item in messages]

                if message_ids:
                    await session.execute(
                        delete(MessageReadReceipt).where(
                            MessageReadReceipt.message_id.in_(message_ids)
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
    await session.execute(delete(AbuseEvent).where(AbuseEvent.user_id == user_id))
    await session.execute(delete(EventLog).where(EventLog.user_id == user_id))
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
            UserConsent.version == LEGAL_TEST_VERSION,
        )
    )
    await session.execute(
        delete(LegalDocument).where(
            LegalDocument.tenant_id == tenant_id,
            LegalDocument.version == LEGAL_TEST_VERSION,
        )
    )
    await session.commit()


async def ensure_legal_documents(session, tenant_id):
    for doc_type in REQUIRED_SPECIALIST_CONSENTS:
        session.add(
            LegalDocument(
                tenant_id=tenant_id,
                doc_type=doc_type,
                version=LEGAL_TEST_VERSION,
                language="ru",
                title=f"{doc_type} beta 0.6 test title",
                content_text=f"{doc_type} beta 0.6 test content",
                status="active",
            )
        )

    await session.commit()


async def accept_specialist_consents(session, tenant_id, user_id):
    await ensure_legal_documents(session, tenant_id)

    service = LegalService(LegalRepository(session))
    await service.accept_required_specialist_consents(
        tenant_id=tenant_id,
        user_id=user_id,
        language="ru",
        platform="telegram",
    )


async def create_test_user(session, *, prefix: str, role: str = "client"):
    platform_user_id = f"{prefix}-{uuid.uuid4()}"

    user_repo = UserRepository(session)
    user_id = await user_repo.create_telegram_user_core(
        platform_user_id=platform_user_id,
        username=prefix,
        first_name="Beta",
        last_name="Contact",
        language_code="ru",
        role=role,
    )

    user = await session.get(User, user_id)
    assert user is not None
    assert user.tenant_id is not None

    return platform_user_id, user.id, user.tenant_id

async def create_active_specialist_for_contact(session):
    from tests.test_beta_05_geo_search_filter import get_reference_data

    platform_user_id, user_id, tenant_id = await create_test_user(
        session,
        prefix="test-contact-specialist",
    )
    refs = await get_reference_data(session)

    await cleanup_legal_documents(session, tenant_id)
    await accept_specialist_consents(session, tenant_id, user_id)

    service = SpecialistRegistrationService(SpecialistRepository(session))
    specialist = await service.create_pending_profile(
        SpecialistRegistrationData(
            tenant_id=tenant_id,
            user_id=user_id,
            category_id=refs["category_id"],
            profession_id=refs["profession_id"],
            country_id=refs["country_id"],
            city_id=refs["city_id"],
            display_name="Contact Beta Specialist",
            short_description="Experienced contact beta specialist.",
            full_description="Detailed contact beta specialist profile.",
            price_from=40,
            price_to=80,
            currency="EUR",
            price_unit="service",
            work_format="mixed",
            latitude=refs["city_latitude"],
            longitude=refs["city_longitude"],
            service_radius_km=25,
            languages=["ru", "en"],
            service_title="Contact beta service",
            service_description="Service created by beta 0.6 contact test.",
            contact_text="Contact inside SGHR beta chat",
        )
    )

    specialist.status = "active"
    await session.commit()

    return platform_user_id, user_id, tenant_id, specialist

async def test_create_contact_request_thread_message_notification_and_events(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        result = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, I need help with a beta service.",
            original_language="en",
        )

        contact_request = await db_session.get(ContactRequest, result.contact_request_id)
        thread = await db_session.get(ConversationThread, result.thread_id)
        first_message = await db_session.get(Message, result.first_message_id)
        notification = await db_session.get(Notification, result.notification_id)

        assert contact_request is not None
        assert contact_request.from_user_id == client_user_id
        assert contact_request.specialist_id == specialist.id
        assert contact_request.status == "new"
        assert contact_request.original_language == "en"

        assert thread is not None
        assert thread.context_type == "contact_request"
        assert thread.context_id == contact_request.id
        assert thread.client_user_id == client_user_id
        assert thread.specialist_id == specialist.id
        assert thread.status == "waiting_specialist"

        assert first_message is not None
        assert first_message.thread_id == thread.id
        assert first_message.sender_user_id == client_user_id
        assert first_message.receiver_user_id == specialist_user_id
        assert first_message.original_text == "Hello, I need help with a beta service."
        assert first_message.original_language == "en"
        assert first_message.translation_status == "pending"

        assert notification is not None
        assert notification.user_id == specialist_user_id
        assert notification.notification_type == "contact_request_created"
        assert notification.channel == "telegram"
        assert notification.status == "pending"
        assert notification.payload["contact_request_id"] == str(contact_request.id)
        assert notification.payload["thread_id"] == str(thread.id)

        events_result = await db_session.execute(
            select(EventLog).where(EventLog.user_id == client_user_id)
        )
        event_types = {event.event_type for event in events_result.scalars().all()}

        assert "contact_request_created" in event_types
        assert "thread_created" in event_types
        assert "message_sent" in event_types

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_contact_request_rejects_inactive_specialist(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-inactive",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        specialist.status = "pending_moderation"
        await db_session.commit()

        service = ContactChatService(ContactChatRepository(db_session))

        try:
            await service.create_contact_request(
                tenant_id=tenant_id,
                from_user_id=client_user_id,
                specialist_id=specialist.id,
                message="Hello, I need help with this service.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "not available" in str(exc)
        else:
            raise AssertionError("Inactive specialist contact was not rejected")

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_contact_request_rejects_blocked_or_deleted_specialist_user(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-blocked",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        specialist_user = await db_session.get(User, specialist_user_id)
        specialist_user.status = "blocked"
        await db_session.commit()

        service = ContactChatService(ContactChatRepository(db_session))

        try:
            await service.create_contact_request(
                tenant_id=tenant_id,
                from_user_id=client_user_id,
                specialist_id=specialist.id,
                message="Hello, I need help with this service.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "not available" in str(exc)
        else:
            raise AssertionError("Blocked specialist user contact was not rejected")

        specialist_user.status = "deleted"
        await db_session.commit()

        try:
            await service.create_contact_request(
                tenant_id=tenant_id,
                from_user_id=client_user_id,
                specialist_id=specialist.id,
                message="Hello, I need help with this service.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "not available" in str(exc)
        else:
            raise AssertionError("Deleted specialist user contact was not rejected")

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_contact_request_rejects_self_contact(db_session):
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        try:
            await service.create_contact_request(
                tenant_id=tenant_id,
                from_user_id=specialist_user_id,
                specialist_id=specialist.id,
                message="Hello, I need help with my own service.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "own specialist profile" in str(exc)
        else:
            raise AssertionError("Self contact was not rejected")

    finally:
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_contact_request_accepts_and_activates_thread(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-accept",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        accepted = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        contact_request = await db_session.get(ContactRequest, accepted.contact_request_id)
        thread = await db_session.get(ConversationThread, accepted.thread_id)

        assert contact_request.status == "accepted"
        assert thread.status == "open"
        assert accepted.status == "accepted"
        assert accepted.thread_status == "open"

        events_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == specialist_user_id,
                EventLog.event_type == "contact_request_accepted",
            )
        )
        assert events_result.scalar_one_or_none() is not None

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)


async def test_contact_request_rejects_and_closes_thread(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-reject",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        rejected = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="reject",
        )

        contact_request = await db_session.get(ContactRequest, rejected.contact_request_id)
        thread = await db_session.get(ConversationThread, rejected.thread_id)

        assert contact_request.status == "rejected"
        assert thread.status == "closed"
        assert rejected.status == "rejected"
        assert rejected.thread_status == "closed"

        events_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == specialist_user_id,
                EventLog.event_type == "contact_request_rejected",
            )
        )
        assert events_result.scalar_one_or_none() is not None

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)


def test_contact_service_rejects_unknown_contact_action():
    from services.contact_chat import ContactChatError, ContactChatService

    service = ContactChatService(repository=None)

    async def run():
        await service.set_contact_request_status(
            contact_request_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            action="wat",
        )

    import asyncio

    try:
        asyncio.run(run())
    except ContactChatError as exc:
        assert "Unsupported contact request action" in str(exc)
    else:
        raise AssertionError("Unsupported contact action was not rejected")
    
async def test_thread_message_rejects_non_participant(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-stranger",
    )
    stranger_platform_id, stranger_user_id, stranger_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-stranger",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        accepted = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        try:
            await service.send_thread_message(
                thread_id=accepted.thread_id,
                sender_user_id=stranger_user_id,
                text="I should not be able to write here.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "not found" in str(exc) or "participant" in str(exc)
        else:
            raise AssertionError("Non-participant was allowed to send a thread message")

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, stranger_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_thread_message_rejects_closed_thread(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-closed",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        rejected = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="reject",
        )

        try:
            await service.send_thread_message(
                thread_id=rejected.thread_id,
                sender_user_id=client_user_id,
                text="This message must not be accepted after reject.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "not open" in str(exc)
        else:
            raise AssertionError("Message was allowed in closed thread")

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_thread_message_creates_message_notification_and_event(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-message",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        accepted = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        sent = await service.send_thread_message(
            thread_id=accepted.thread_id,
            sender_user_id=specialist_user_id,
            text="Hello, I can help with this beta service.",
            original_language="en",
        )

        thread = await db_session.get(ConversationThread, sent.thread_id)
        message = await db_session.get(Message, sent.message_id)
        notification = await db_session.get(Notification, sent.notification_id)

        assert thread.status == "in_discussion"

        assert message is not None
        assert message.thread_id == accepted.thread_id
        assert message.sender_user_id == specialist_user_id
        assert message.receiver_user_id == client_user_id
        assert message.original_text == "Hello, I can help with this beta service."
        assert message.original_language == "en"
        assert message.translation_status == "pending"

        assert notification is not None
        assert notification.user_id == client_user_id
        assert notification.notification_type == "message_received"
        assert notification.payload["thread_id"] == str(accepted.thread_id)
        assert notification.payload["message_id"] == str(message.id)

        events_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == specialist_user_id,
                EventLog.event_type == "message_sent",
                EventLog.entity_id == message.id,
            )
        )
        assert events_result.scalar_one_or_none() is not None

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_message_receiver_can_mark_message_read_once(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-read",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        first_read = await service.mark_thread_message_read(
            thread_id=created.thread_id,
            message_id=created.first_message_id,
            user_id=specialist_user_id,
        )
        second_read = await service.mark_thread_message_read(
            thread_id=created.thread_id,
            message_id=created.first_message_id,
            user_id=specialist_user_id,
        )

        assert first_read.message_id == created.first_message_id
        assert first_read.user_id == specialist_user_id
        assert second_read.receipt_id == first_read.receipt_id

        receipts_result = await db_session.execute(
            select(MessageReadReceipt).where(
                MessageReadReceipt.message_id == created.first_message_id,
                MessageReadReceipt.user_id == specialist_user_id,
            )
        )
        receipts = receipts_result.scalars().all()
        assert len(receipts) == 1

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_message_sender_cannot_mark_own_message_read(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-own-read",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        try:
            await service.mark_thread_message_read(
                thread_id=created.thread_id,
                message_id=created.first_message_id,
                user_id=client_user_id,
            )
        except ContactChatError as exc:
            assert "Only message receiver" in str(exc)
        else:
            raise AssertionError("Sender was allowed to mark own message as read")

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_search_contact_handler_is_real_flow_not_placeholder():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
        "entering_contact_message",
        "selected_specialist_id",
        "ContactChatService(ContactChatRepository(session)).create_contact_request",
        "active_contact_request_id",
        "active_thread_id",
        "contact_disclaimer_text",
        "contact_disclaimer_continue",
        "callback_data=\"contact_disclaimer_continue\"",
        "contact_request_prompt",
        "contact_request_created",
    ]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "Contacting a specialist will be available in Beta 0.6",
        "search_contact_placeholder",
        "UUID(callback.data.split",
        "callback_data=f\"search_contact_pending:{",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

    assert len("search_contact_pending".encode("utf-8")) <= 64
    assert len("contact_disclaimer_continue".encode("utf-8")) <= 64
async def test_contact_request_token_allows_specialist_accept_without_uuid_callback(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-token",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        contact_request = await db_session.get(ContactRequest, created.contact_request_id)
        token = (contact_request.extra_metadata or {}).get("contact_token")

        assert token
        assert str(contact_request.id) not in token
        assert len(f"contact_accept:{token}".encode("utf-8")) <= 64
        assert len(f"contact_reject:{token}".encode("utf-8")) <= 64

        accepted = await service.set_contact_request_status_by_token(
            contact_token=token,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        assert accepted.status == "accepted"
        assert accepted.thread_status == "open"

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_contact_request_token_rejects_non_specialist_actor(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-token-denied",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        contact_request = await db_session.get(ContactRequest, created.contact_request_id)
        token = (contact_request.extra_metadata or {}).get("contact_token")

        try:
            await service.set_contact_request_status_by_token(
                contact_token=token,
                actor_user_id=client_user_id,
                tenant_id=tenant_id,
                action="accept",
            )
        except ContactChatError as exc:
            assert "Only specialist" in str(exc)
        else:
            raise AssertionError("Client was allowed to accept own contact request")

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_contact_accept_reject_callbacks_are_compact_and_do_not_use_uuid_payloads():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
        "contact_accept:",
        "contact_reject:",
        "set_contact_request_status_by_token",
        "contact_request_action_keyboard",
        "contact_request_specialist_notification",
    ]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "contact_accept:{contact_request_id}",
        "contact_reject:{contact_request_id}",
        "contact_accept:{result.contact_request_id}",
        "contact_reject:{result.contact_request_id}",
        "UUID(callback.data.split",
        "UUID(callback.data.rsplit",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

    for callback_prefix in ["contact_accept:", "contact_reject:"]:
        assert len(f"{callback_prefix}shorttoken".encode("utf-8")) <= 64

def test_contact_reply_flow_is_wired_in_search_handler():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
        "entering_thread_message",
        "contact_thread_keyboard",
        "callback_data=\"contact_reply\"",
        "ContactChatService(ContactChatRepository(session)).send_thread_message",
        "active_thread_id",
        "contact_message_sent",
        "contact_show_original",
        "callback_data=\"contact_finish\"",
        "async def finish_contact_thread",
        "ContactChatService(ContactChatRepository(session)).complete_thread",
        "receiver_platform_user_id",
        "contact_thread_message_received",
        "message.bot.send_message",
    ]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "callback_data=\"contact_finish_pending\"",
        "async def finish_contact_pending",
        "callback_data=f\"contact_reply:{",
        "callback_data=f\"contact_show_original",
        "callback_data=f\"contact_finish",
        "UUID(callback.data.split",
        "UUID(callback.data.rsplit",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

    callback_literals = [
    "contact_reply",
    "contact_show_original",
    "contact_finish",
]

    for callback_data in callback_literals:
        assert len(callback_data.encode("utf-8")) <= 64

async def test_specialist_can_reply_after_accepting_contact_request(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-specialist-reply",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        accepted = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        sent = await service.send_thread_message(
            thread_id=accepted.thread_id,
            sender_user_id=specialist_user_id,
            text="Hello, I can help with this beta service.",
            original_language="en",
        )

        message = await db_session.get(Message, sent.message_id)
        notification = await db_session.get(Notification, sent.notification_id)
        thread = await db_session.get(ConversationThread, sent.thread_id)

        assert thread.status == "in_discussion"
        assert message.sender_user_id == specialist_user_id
        assert message.receiver_user_id == client_user_id
        assert message.original_text == "Hello, I can help with this beta service."
        assert notification.user_id == client_user_id
        assert notification.notification_type == "message_received"

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_thread_can_be_completed_by_participant(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-complete",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        accepted = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        completed = await service.complete_thread(
            thread_id=accepted.thread_id,
            actor_user_id=client_user_id,
        )

        thread = await db_session.get(ConversationThread, completed.thread_id)

        assert completed.status == "completed"
        assert thread.status == "completed"

        events_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == client_user_id,
                EventLog.event_type == "thread_completed",
                EventLog.entity_id == thread.id,
            )
        )
        assert events_result.scalar_one_or_none() is not None

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)
async def test_completed_thread_rejects_new_messages(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-completed-message",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        accepted = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        await service.complete_thread(
            thread_id=accepted.thread_id,
            actor_user_id=specialist_user_id,
        )

        try:
            await service.send_thread_message(
                thread_id=accepted.thread_id,
                sender_user_id=client_user_id,
                text="This must not be accepted after completion.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "not open" in str(exc)
        else:
            raise AssertionError("Completed thread accepted a new message")

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_contact_finish_flow_is_real_not_placeholder():
    source = open("handlers/search.py", encoding="utf-8").read()

    required_fragments = [
        "callback_data=\"contact_finish\"",
        "async def finish_contact_thread",
        "ContactChatService(ContactChatRepository(session)).complete_thread",
        "contact_thread_completed",
    ]

    for fragment in required_fragments:
        assert fragment in source

    forbidden_fragments = [
        "callback_data=\"contact_finish_pending\"",
        "async def finish_contact_pending",
        "contact_finish_pending",
        "callback_data=f\"contact_finish:{",
        "UUID(callback.data.split",
        "UUID(callback.data.rsplit",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in source

    assert len("contact_finish".encode("utf-8")) <= 64

async def test_contact_request_rate_limit_blocks_excess_requests_and_logs_abuse(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-rate-limit",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    existing_rule_result = await db_session.execute(
        select(RateLimitRule).where(
            RateLimitRule.scope == "user",
            RateLimitRule.action == "contact_request",
        )
    )
    existing_rule = existing_rule_result.scalar_one_or_none()

    created_rule_id = None
    original_rule_state = None

    if existing_rule:
        original_rule_state = {
            "limit_count": existing_rule.limit_count,
            "window_seconds": existing_rule.window_seconds,
            "penalty_action": existing_rule.penalty_action,
            "is_active": existing_rule.is_active,
        }
        existing_rule.limit_count = 1
        existing_rule.window_seconds = 3600
        existing_rule.penalty_action = "block"
        existing_rule.is_active = True
        rate_limit_rule_id = existing_rule.id
    else:
        rate_limit_rule = RateLimitRule(
            scope="user",
            action="contact_request",
            limit_count=1,
            window_seconds=3600,
            penalty_action="block",
            is_active=True,
        )
        db_session.add(rate_limit_rule)
        await db_session.flush()
        rate_limit_rule_id = rate_limit_rule.id
        created_rule_id = rate_limit_rule.id

    await db_session.commit()

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        first = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this first beta service.",
            original_language="en",
        )

        assert first.contact_request_id is not None

        try:
            await service.create_contact_request(
                tenant_id=tenant_id,
                from_user_id=client_user_id,
                specialist_id=specialist.id,
                message="Hello, please help with this second beta service.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "rate limit exceeded" in str(exc).lower()
        else:
            raise AssertionError("Second contact request was not rate limited")

        abuse_result = await db_session.execute(
            select(AbuseEvent).where(
                AbuseEvent.user_id == client_user_id,
                AbuseEvent.event_type == "rate_limit_exceeded",
            )
        )
        abuse_event = abuse_result.scalar_one_or_none()

        assert abuse_event is not None
        assert abuse_event.action_taken == "block"
        assert abuse_event.details["action"] == "contact_request"
        assert abuse_event.details["limit_count"] == 1
        assert abuse_event.details["window_seconds"] == 3600

    finally:
        await db_session.rollback()

        if created_rule_id:
            await db_session.execute(
                delete(RateLimitRule).where(RateLimitRule.id == created_rule_id)
            )
        elif original_rule_state:
            rule = await db_session.get(RateLimitRule, rate_limit_rule_id)
            if rule:
                rule.limit_count = original_rule_state["limit_count"]
                rule.window_seconds = original_rule_state["window_seconds"]
                rule.penalty_action = original_rule_state["penalty_action"]
                rule.is_active = original_rule_state["is_active"]

        await db_session.commit()
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_beta_06_rate_limit_service_contract_exists():
    source = open("services/rate_limit.py", encoding="utf-8").read()

    required_fragments = [
        "class RateLimitError",
        "class RateLimitService",
        "ensure_contact_request_allowed",
        "contact_request",
        "rate limit exceeded",
    ]

    for fragment in required_fragments:
        assert fragment in source

def test_beta_06_rate_limit_repository_contract_exists():
    source = open("database/repositories/rate_limit.py", encoding="utf-8").read()

    required_fragments = [
        "class RateLimitRepository",
        "get_active_rule",
        "count_contact_requests_in_window",
        "log_rate_limit_exceeded",
        "RateLimitRule",
        "AbuseEvent",
        "ContactRequest",
    ]

    for fragment in required_fragments:
        assert fragment in source

async def test_contact_request_creates_pending_translation_job_when_languages_differ(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-translation",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        specialist_user = await db_session.get(User, specialist_user_id)
        specialist_user.language_code = "ru"
        await db_session.commit()

        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        job_result = await db_session.execute(
            select(TranslationJob).where(
                TranslationJob.message_id == created.first_message_id,
            )
        )
        job = job_result.scalar_one_or_none()

        assert job is not None
        assert job.tenant_id == tenant_id
        assert job.message_id == created.first_message_id
        assert job.source_language == "en"
        assert job.target_language == "ru"
        assert job.status == "pending"
        assert job.retry_count == 0
        assert job.max_retries == 3

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)
async def test_contact_request_does_not_create_translation_job_when_languages_match(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-no-translation",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        specialist_user = await db_session.get(User, specialist_user_id)
        specialist_user.language_code = "en"
        await db_session.commit()

        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        job_result = await db_session.execute(
            select(TranslationJob).where(
                TranslationJob.message_id == created.first_message_id,
            )
        )
        job = job_result.scalar_one_or_none()

        assert job is None

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_thread_message_creates_pending_translation_job_when_languages_differ(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-thread-translation",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        client_user = await db_session.get(User, client_user_id)
        specialist_user = await db_session.get(User, specialist_user_id)
        client_user.language_code = "ru"
        specialist_user.language_code = "en"
        await db_session.commit()

        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this beta service.",
            original_language="en",
        )

        accepted = await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        sent = await service.send_thread_message(
            thread_id=accepted.thread_id,
            sender_user_id=specialist_user_id,
            text="Hello, I can help with this beta service.",
            original_language="en",
        )

        job_result = await db_session.execute(
            select(TranslationJob).where(
                TranslationJob.message_id == sent.message_id,
            )
        )
        job = job_result.scalar_one_or_none()

        assert job is not None
        assert job.source_language == "en"
        assert job.target_language == "ru"
        assert job.status == "pending"

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_contact_request_flow_requires_message_confirmation_before_create():
    source = open("handlers/search.py", encoding="utf-8").read()

    assert "confirming_contact_message = State()" in source
    assert "pending_contact_message" in source
    assert "contact_message_confirm_keyboard" in source
    assert "callback_data=\"contact_send_confirm\"" in source
    assert "async def confirm_contact_message" in source

    receive_handler = source.split(
        "async def receive_contact_message",
        1,
    )[1].split(
        "@search_router.callback_query(F.data == \"contact_send_confirm\")",
        1,
    )[0]

    assert "pending_contact_message" in receive_handler
    assert "create_contact_request" not in receive_handler

    confirm_handler = source.split(
        "async def confirm_contact_message",
        1,
    )[1].split(
        "def callback_token",
        1,
    )[0]

    assert "ContactChatService(ContactChatRepository(session)).create_contact_request" in confirm_handler