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
    ContactDetectionEvent,
    Message,
    RiskFlag,
    ThreadRestriction,
    ConversationParticipant,
    Blacklist,
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
        assert first_message.translation_status == "not_needed"
        assert first_message.translated_text is None
        assert first_message.translated_language is None

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
        assert "request_created" in event_types
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
            decline_reason="Not relevant for this request.",
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
            decline_reason="Not relevant for this request.",
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

async def test_thread_message_is_read_only_when_participant_blacklisted(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-blacklist-readonly",
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

        blacklist = Blacklist(
            tenant_id=tenant_id,
            user_id=client_user_id,
            platform="telegram",
            platform_user_id=client_platform_id,
            reason="test_blacklist_read_only",
            status="active",
            created_by=specialist_user_id,
        )
        db_session.add(blacklist)
        await db_session.commit()

        try:
            await service.send_thread_message(
                thread_id=accepted.thread_id,
                sender_user_id=client_user_id,
                text="Trying to write from a blacklisted account.",
                original_language="en",
            )
        except ContactChatError as exc:
            assert "read-only" in str(exc)
        else:
            raise AssertionError("Blacklisted user was allowed to write to thread")

    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(Blacklist).where(
                Blacklist.tenant_id == tenant_id,
                Blacklist.user_id.in_([client_user_id, specialist_user_id]),
            )
        )
        await db_session.commit()

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
        assert message.translation_status == "not_needed"
        assert message.translated_text is None
        assert message.translated_language is None

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
        "callback_data=\"contact_archive\"",
        "callback_data=\"contact_hide\"",
        "callback_data=\"CLIENT_DIALOGS\"",
        "async def archive_contact_thread",
        "async def hide_contact_thread",
        "set_thread_visibility",
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
    "contact_archive",
    "contact_hide",
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
        request_completed_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == client_user_id,
                EventLog.event_type == "request_completed",
                EventLog.entity_id == created.contact_request_id,
            )
        )
        assert request_completed_result.scalar_one_or_none() is not None
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

async def test_contact_request_rejects_duplicate_active_request(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-duplicate",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))
        service.rate_limit_service = None

        first = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this first beta service.",
            original_language="en",
        )

        assert first.contact_request_id is not None

        second = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, please help with this second beta service.",
            original_language="en",
        )

        assert second.was_existing is True
        assert second.contact_request_id == first.contact_request_id
        assert second.thread_id == first.thread_id

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

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
        await service.set_contact_request_status(
            contact_request_id=first.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="reject",
            decline_reason="Not relevant for this request.",
        )
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

async def test_contact_request_does_not_create_translation_job_in_controlled_beta(db_session):
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

        assert job is None

        message = await db_session.get(Message, created.first_message_id)
        assert message.translation_status == "not_needed"
        assert message.translated_text is None
        assert message.translated_language is None

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

async def test_thread_message_does_not_create_translation_job_in_controlled_beta(db_session):
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

        assert job is None

        message = await db_session.get(Message, sent.message_id)
        assert message.translation_status == "not_needed"
        assert message.translated_text is None
        assert message.translated_language is None

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
    assert "if result.was_existing:" in confirm_handler
    assert "contact_request_existing" in confirm_handler
    assert "contact_thread_keyboard(language)" in confirm_handler
async def test_contact_request_masks_external_contacts_and_logs_detection(db_session):
    client_platform_user_id, client_user_id, tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-detection-client",
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        result = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Please contact me by email test@example.com or Telegram @testuser123.",
            original_language="en",
        )

        assert result.message_masked is True
        assert "email" in result.detection_types
        assert "telegram_username" in result.detection_types

        message = await db_session.get(Message, result.first_message_id)
        assert message is not None
        assert message.is_masked is True
        assert "test@example.com" not in message.original_text
        assert "@testuser123" not in message.original_text
        assert "[masked]" in message.original_text

        detection_events = (
            await db_session.execute(
                select(ContactDetectionEvent).where(
                    ContactDetectionEvent.message_id == result.first_message_id
                )
            )
        ).scalars().all()
        assert {event.detected_type for event in detection_events}.issuperset(
            {"email", "telegram_username"}
        )

        risk_flag = (
            await db_session.execute(
                select(RiskFlag).where(
                    RiskFlag.entity_type == "message",
                    RiskFlag.entity_id == result.first_message_id,
                    RiskFlag.flag_code == "off_platform_contact",
                )
            )
        ).scalar_one_or_none()
        assert risk_flag is not None
        assert risk_flag.status == "open"
    finally:
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_thread_message_with_external_payment_restricts_thread(db_session):
    client_platform_user_id, client_user_id, tenant_id = await create_test_user(
        db_session,
        prefix="test-payment-detection-client",
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    try:
        service = ContactChatService(ContactChatRepository(db_session))

        created = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, I need a normal consultation inside SGHR.",
            original_language="en",
        )

        await service.set_contact_request_status(
            contact_request_id=created.contact_request_id,
            actor_user_id=specialist_user_id,
            tenant_id=tenant_id,
            action="accept",
        )

        sent = await service.send_thread_message(
            thread_id=created.thread_id,
            sender_user_id=client_user_id,
            text="I can pay directly by PayPal after the call.",
            original_language="en",
        )

        assert sent.message_masked is True
        assert "external_payment" in sent.detection_types
        assert sent.thread_restricted is True

        message = await db_session.get(Message, sent.message_id)
        assert message is not None
        assert message.is_masked is True
        assert "PayPal" not in message.original_text
        assert "[masked]" in message.original_text

        restriction = (
            await db_session.execute(
                select(ThreadRestriction).where(
                    ThreadRestriction.thread_id == created.thread_id,
                    ThreadRestriction.status == "active",
                    ThreadRestriction.reason == "off_platform_payment",
                )
            )
        ).scalar_one_or_none()
        assert restriction is not None

        risk_flag = (
            await db_session.execute(
                select(RiskFlag).where(
                    RiskFlag.entity_type == "message",
                    RiskFlag.entity_id == sent.message_id,
                    RiskFlag.flag_code == "off_platform_contact",
                )
            )
        ).scalar_one_or_none()
        assert risk_flag is not None
        assert risk_flag.severity == "high"
    finally:
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)

async def test_contact_request_creates_conversation_participants(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-participants",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    service = ContactChatService(ContactChatRepository(db_session))
    service.rate_limit_service = None

    try:
        result = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, I want to contact you about the service.",
            original_language="en",
        )

        participants = (
            await db_session.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.thread_id == result.thread_id,
                )
            )
        ).scalars().all()

        assert len(participants) == 2

        by_user_id = {item.user_id: item for item in participants}

        assert by_user_id[client_user_id].participant_role == "client"
        assert by_user_id[client_user_id].unread_count == 0
        assert by_user_id[client_user_id].is_archived is False
        assert by_user_id[client_user_id].is_hidden is False

        assert by_user_id[specialist_user_id].participant_role == "specialist"
        assert by_user_id[specialist_user_id].unread_count == 1
        assert by_user_id[specialist_user_id].is_archived is False
        assert by_user_id[specialist_user_id].is_hidden is False
    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

async def test_thread_visibility_and_unread_are_persisted_per_participant(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-visibility",
    )
    specialist_platform_id, specialist_user_id, tenant_id, specialist = (
        await create_active_specialist_for_contact(db_session)
    )

    service = ContactChatService(ContactChatRepository(db_session))
    service.rate_limit_service = None

    try:
        request = await service.create_contact_request(
            tenant_id=tenant_id,
            from_user_id=client_user_id,
            specialist_id=specialist.id,
            message="Hello, I want to contact you about the service.",
            original_language="en",
        )

        await service.set_contact_request_status(
            tenant_id=tenant_id,
            contact_request_id=request.contact_request_id,
            actor_user_id=specialist_user_id,
            action="accept",
        )

        reply = await service.send_thread_message(
            thread_id=request.thread_id,
            sender_user_id=specialist_user_id,
            text="Specialist reply message.",
            original_language="en",
        )

        client_participant = (
            await db_session.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.thread_id == request.thread_id,
                    ConversationParticipant.user_id == client_user_id,
                )
            )
        ).scalar_one()

        assert client_participant.unread_count == 1

        visibility = await service.set_thread_visibility(
            thread_id=request.thread_id,
            user_id=client_user_id,
            is_archived=True,
            is_hidden=True,
        )

        assert visibility.is_archived is True
        assert visibility.is_hidden is True
        archived_event_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == client_user_id,
                EventLog.event_type == "dialog_archived",
                EventLog.entity_id == request.thread_id,
            )
        )
        assert archived_event_result.scalar_one_or_none() is not None


        await service.mark_thread_message_read(
            thread_id=request.thread_id,
            message_id=reply.message_id,
            user_id=client_user_id,
        )

        client_participant = (
            await db_session.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.thread_id == request.thread_id,
                    ConversationParticipant.user_id == client_user_id,
                )
            )
        ).scalar_one()

        assert client_participant.unread_count == 0
        assert client_participant.last_read_message_id == reply.message_id
        assert client_participant.last_read_at is not None
        assert client_participant.is_archived is True
        assert client_participant.is_hidden is True
    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_client_dialogs_c13_screen_is_wired_to_threads_participant_state():
    contact_repo_source = open("database/repositories/contact.py", encoding="utf-8").read()
    contact_service_source = open("services/contact_chat.py", encoding="utf-8").read()
    billing_source = open("handlers/billing.py", encoding="utf-8").read()

    assert "list_threads_for_user" in contact_repo_source
    assert "ConversationParticipant.unread_count" in contact_repo_source
    assert "ConversationParticipant.is_archived" in contact_repo_source
    assert "ConversationParticipant.is_hidden" in contact_repo_source
    assert "Specialist.display_name" in contact_repo_source
    assert "Profession" in contact_repo_source

    assert "ContactThreadListItem" in contact_service_source
    assert "list_client_threads" in contact_service_source
    assert 'participant_role="client"' in contact_service_source

    assert 'F.data == "CLIENT_DIALOGS"' in billing_source
    assert 'F.data.startswith("CLIENT_DIALOGS:")' in billing_source
    assert "client_dialogs_keyboard" in billing_source
    assert "CLIENT_DIALOG_OPEN" in billing_source
    assert "client_dialog_thread_ids" in billing_source
    assert "dialogs_opened" in billing_source
    assert "EventRepository(session).create_event" in billing_source
    assert "for index in range(items_count)" in billing_source
    assert 'callback_data=f"CLIENT_DIALOG_OPEN:{index}"' in billing_source
    assert "async def open_client_dialog" in billing_source
    assert "client_dialog_thread_ids" in billing_source
    assert "active_thread_id=thread_id" in billing_source
    assert "contact_thread_keyboard(language)" in billing_source
    assert "get_thread_detail_for_user" in contact_repo_source
    assert "ContactThreadDetail" in contact_service_source
    assert "get_thread_detail" in contact_service_source
    assert "format_client_thread_detail_text" in billing_source
    assert "get_thread_detail(" in billing_source
    assert "client_thread_detail_title" in billing_source
    assert "client_thread_history_label" in billing_source

def test_client_requests_c15_backend_is_wired_to_contact_requests():
    contact_repo_source = open("database/repositories/contact.py", encoding="utf-8").read()
    contact_service_source = open("services/contact_chat.py", encoding="utf-8").read()

    assert "list_contact_requests_for_client" in contact_repo_source
    assert "ContactRequest.from_user_id == user_id" in contact_repo_source
    assert "ConversationThread.id.label(\"thread_id\")" in contact_repo_source
    assert "Specialist.display_name" in contact_repo_source
    assert "Profession" in contact_repo_source

    assert "ContactRequestListItem" in contact_service_source
    assert "list_client_requests" in contact_service_source
    billing_source = open("handlers/billing.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "CLIENT_REQUESTS_PAGE_SIZE" in billing_source
    assert "client_requests_keyboard" in billing_source
    assert "format_client_requests_text" in billing_source
    assert "CLIENT_REQUEST_OPEN" in billing_source
    assert "CLIENT_REQUEST_DIALOG" in billing_source
    assert "CLIENT_REQUEST_CANCEL" in billing_source

    assert "client_requests_title" in texts_source
    assert "client_requests_empty" in texts_source
    assert "client_request_cancelled" in texts_source
    assert 'F.data == "CLIENT_REQUESTS"' in billing_source
    assert 'F.data.startswith("CLIENT_REQUESTS:")' in billing_source
    assert "async def show_client_requests" in billing_source
    assert "list_client_requests" in billing_source
    assert "request_list" in billing_source
    assert "client_request_ids" in billing_source
    assert "client_request_thread_ids" in billing_source
    assert "send_client_thread_detail" in billing_source
    assert "async def open_client_request_dialog" in billing_source
    assert 'F.data.startswith("CLIENT_REQUEST_DIALOG:")' in billing_source
    assert "client_request_thread_ids" in billing_source
    assert "cancel_contact_request_by_client" in contact_repo_source
    assert "contact_request.status not in" in contact_repo_source
    assert "request_cancelled" in contact_repo_source
    assert "cancel_contact_request" in contact_service_source
    assert "async def cancel_client_request" in billing_source
    assert 'F.data.startswith("CLIENT_REQUEST_CANCEL:")' in billing_source
    assert "cancel_contact_request(" in billing_source
    assert "client_request_cancelled" in billing_source
    assert "ContactRequestDetail" in contact_service_source
    assert "get_contact_request_detail_for_client" in contact_repo_source
    assert "get_client_request_detail" in contact_service_source
    assert "format_client_request_detail_text" in billing_source
    assert "async def open_client_request_card" in billing_source
    assert 'F.data.startswith("CLIENT_REQUEST_OPEN:")' in billing_source
    assert "client_request_detail_title" in texts_source
    assert "client_request_card_keyboard" in billing_source
    assert "CLIENT_REQUEST_CARD_DIALOG:" in billing_source
    assert "CLIENT_REQUEST_CARD_CANCEL:" in billing_source
    assert "CLIENT_REQUEST_CARD_FINISH:" in billing_source
    assert "async def open_client_request_card_dialog" in billing_source
    assert "async def cancel_client_request_card" in billing_source
    assert "async def finish_client_request_card" in billing_source
    assert "request_viewed" in billing_source
    assert "client_request_status_updated" in texts_source
    assert "client_request_back_to_requests" in texts_source
    assert "complete_thread(" in billing_source

async def test_client_can_cancel_only_new_or_accepted_contact_request(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-cancel",
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

        cancelled = await service.cancel_contact_request(
            contact_request_id=created.contact_request_id,
            actor_user_id=client_user_id,
            tenant_id=tenant_id,
        )

        assert cancelled.status == "rejected"
        assert cancelled.thread_status == "closed"

        event_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == client_user_id,
                EventLog.event_type == "request_cancelled",
                EventLog.entity_id == created.contact_request_id,
            )
        )
        assert event_result.scalar_one_or_none() is not None

        try:
            await service.cancel_contact_request(
                contact_request_id=created.contact_request_id,
                actor_user_id=client_user_id,
                tenant_id=tenant_id,
            )
        except ContactChatError as exc:
            assert "cannot be cancelled" in str(exc)
        else:
            raise AssertionError("Cancelled request was cancelled twice")

    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)
async def test_client_can_complete_request_thread_from_request_card_backend(db_session):
    client_platform_id, client_user_id, client_tenant_id = await create_test_user(
        db_session,
        prefix="test-contact-client-finish",
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

        detail = await service.get_client_request_detail(
            contact_request_id=created.contact_request_id,
            user_id=client_user_id,
            language="ru",
        )

        assert detail.thread_id == created.thread_id
        assert detail.specialist_name
        assert detail.message == "Hello, please help with this beta service."

        completed = await service.complete_thread(
            thread_id=detail.thread_id,
            actor_user_id=client_user_id,
        )

        assert completed.status == "completed"

        event_result = await db_session.execute(
            select(EventLog).where(
                EventLog.user_id == client_user_id,
                EventLog.event_type == "request_completed",
                EventLog.entity_id == created.contact_request_id,
            )
        )
        event = event_result.scalar_one_or_none()

        assert event is not None
        assert event.entity_type == "contact_request"
        assert event.payload["thread_id"] == str(detail.thread_id)
    finally:
        await cleanup_test_user(db_session, client_platform_id)
        await cleanup_test_user(db_session, specialist_platform_id)
        await cleanup_legal_documents(db_session, tenant_id)

def test_specialist_s9_new_requests_screen_is_wired():
    billing_source = open("handlers/billing.py", encoding="utf-8").read()
    contact_repo_source = open("database/repositories/contact.py", encoding="utf-8").read()
    contact_service_source = open("services/contact_chat.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    assert "list_contact_requests_for_specialist" in contact_repo_source
    assert "ContactRequest.specialist_id == specialist_id" in contact_repo_source
    assert 'ContactRequest.status == status' in contact_repo_source

    assert "SpecialistContactRequestListItem" in contact_service_source
    assert "list_specialist_requests" in contact_service_source

    assert "async def show_specialist_requests" in billing_source
    assert "specialist_requests_keyboard" in billing_source
    assert "format_specialist_requests_text" in billing_source

    assert 'callback_data="SPEC_REQUESTS"' in billing_source
    assert "SPEC_REQUESTS_PAGE:" in billing_source
    assert "SPEC_REQUEST_ACCEPT:" in billing_source
    assert "SPEC_REQUEST_REJECT:" in billing_source

    assert "set_contact_request_status" in billing_source
    assert 'action="accept"' in billing_source
    assert 'action="reject"' in billing_source

    assert "specialist_requests_opened" in billing_source
    assert "specialist_requests_title" in texts_source
    assert "specialist_requests_empty" in texts_source
    assert "specialist_request_status_updated" in texts_source