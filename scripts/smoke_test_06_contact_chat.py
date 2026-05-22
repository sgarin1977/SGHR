import asyncio
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

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
    User,
    UserAccount,
    UserConsent,
    UserRoleMapping,
)
from database.repositories.contact import ContactChatRepository
from database.repositories.legal import LegalRepository
from database.repositories.specialist import SpecialistRepository
from database.repositories.user import UserRepository
from database.session import async_session
from services.contact_chat import ContactChatService
from services.legal import REQUIRED_SPECIALIST_CONSENTS, LegalService
from services.specialist import (
    SpecialistRegistrationData,
    SpecialistService as SpecialistRegistrationService,
)
from sqlalchemy import delete, select


LEGAL_VERSION = "smoke-beta-0.6"


async def cleanup_user(session, platform_user_id: str):
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

    client_thread_result = await session.execute(
        select(ConversationThread).where(ConversationThread.client_user_id == user_id)
    )
    client_threads = client_thread_result.scalars().all()
    client_thread_ids = [item.id for item in client_threads]
    client_contact_request_ids = [
        item.context_id
        for item in client_threads
        if item.context_type == "contact_request" and item.context_id is not None
    ]

    if client_thread_ids:
        client_message_result = await session.execute(
            select(Message).where(Message.thread_id.in_(client_thread_ids))
        )
        client_messages = client_message_result.scalars().all()
        client_message_ids = [item.id for item in client_messages]

        if client_message_ids:
            await session.execute(
                delete(MessageReadReceipt).where(
                    MessageReadReceipt.message_id.in_(client_message_ids)
                )
            )

        await session.execute(delete(Message).where(Message.thread_id.in_(client_thread_ids)))
        await session.execute(
            delete(Notification).where(
                Notification.payload["thread_id"].astext.in_(
                    [str(thread_id) for thread_id in client_thread_ids]
                )
            )
        )
        await session.execute(delete(ConversationThread).where(ConversationThread.id.in_(client_thread_ids)))

    if client_contact_request_ids:
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

                await session.execute(delete(Message).where(Message.thread_id.in_(thread_ids)))
                await session.execute(delete(ConversationThread).where(ConversationThread.id.in_(thread_ids)))

            await session.execute(delete(ContactRequest).where(ContactRequest.id.in_(contact_request_ids)))

        await session.execute(delete(SpecialistService).where(SpecialistService.specialist_id == specialist.id))
        await session.execute(delete(SpecialistLanguage).where(SpecialistLanguage.specialist_id == specialist.id))
        await session.execute(delete(SpecialistLocation).where(SpecialistLocation.specialist_id == specialist.id))
        await session.execute(delete(Specialist).where(Specialist.id == specialist.id))

    await session.execute(delete(Notification).where(Notification.user_id == user_id))
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


async def create_user(session, prefix: str):
    platform_user_id = f"{prefix}-{uuid.uuid4()}"

    user_id = await UserRepository(session).create_telegram_user_core(
        platform_user_id=platform_user_id,
        username=prefix,
        first_name="Smoke",
        last_name="Contact",
        language_code="ru",
        role="client",
    )

    user = await session.get(User, user_id)
    if not user or not user.tenant_id:
        raise SystemExit("FAIL: user was not created")

    return platform_user_id, user.id, user.tenant_id


async def get_reference_data(session):
    from tests.test_beta_05_geo_search_filter import get_reference_data as get_refs

    return await get_refs(session)


async def accept_specialist_consents(session, tenant_id, user_id):
    for doc_type in REQUIRED_SPECIALIST_CONSENTS:
        session.add(
            LegalDocument(
                tenant_id=tenant_id,
                doc_type=doc_type,
                version=LEGAL_VERSION,
                language="ru",
                title=f"{doc_type} smoke beta 0.6",
                content_text=f"{doc_type} smoke beta 0.6 content",
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
    specialist_platform_id, specialist_user_id, tenant_id = await create_user(
        session,
        "smoke-contact-specialist",
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
            display_name="Smoke Contact Specialist",
            short_description="Experienced smoke contact beta specialist.",
            full_description="Detailed smoke contact beta specialist profile.",
            price_from=40,
            price_to=80,
            currency="EUR",
            price_unit="service",
            work_format="mixed",
            latitude=refs["city_latitude"],
            longitude=refs["city_longitude"],
            service_radius_km=25,
            languages=["ru", "en"],
            service_title="Smoke contact service",
            service_description="Service created by beta 0.6 smoke test.",
            contact_text="Contact inside SGHR beta chat",
        )
    )

    specialist.status = "active"
    await session.commit()

    return specialist_platform_id, specialist_user_id, tenant_id, specialist


async def main():
    async with async_session() as session:
        client_platform_id = None
        specialist_platform_id = None
        tenant_id = None

        try:
            client_platform_id, client_user_id, client_tenant_id = await create_user(
                session,
                "smoke-contact-client",
            )
            specialist_platform_id, specialist_user_id, tenant_id, specialist = (
                await create_specialist(session)
            )

            service = ContactChatService(ContactChatRepository(session))

            created = await service.create_contact_request(
                tenant_id=tenant_id,
                from_user_id=client_user_id,
                specialist_id=specialist.id,
                message="Hello, I need help with a smoke beta service.",
                original_language="en",
            )

            contact_request = await session.get(ContactRequest, created.contact_request_id)
            thread = await session.get(ConversationThread, created.thread_id)
            first_message = await session.get(Message, created.first_message_id)
            notification = await session.get(Notification, created.notification_id)

            print(f"contact_request_id={created.contact_request_id}")
            print(f"thread_id={created.thread_id}")
            print(f"first_message_id={created.first_message_id}")
            print(f"notification_id={created.notification_id}")

            if not contact_request or contact_request.status != "new":
                raise SystemExit("FAIL: contact_request was not created with status new")

            if not thread or thread.status != "waiting_specialist":
                raise SystemExit("FAIL: thread was not created with status waiting_specialist")

            if not first_message or first_message.original_text != "Hello, I need help with a smoke beta service.":
                raise SystemExit("FAIL: first message was not saved")

            if not notification or notification.notification_type != "contact_request_created":
                raise SystemExit("FAIL: specialist notification was not created")

            if not created.contact_token:
                raise SystemExit("FAIL: compact contact token was not created")

            if len(f"contact_accept:{created.contact_token}".encode("utf-8")) > 64:
                raise SystemExit("FAIL: accept callback exceeds Telegram 64-byte limit")

            accepted = await service.set_contact_request_status_by_token(
                contact_token=created.contact_token,
                actor_user_id=specialist_user_id,
                tenant_id=tenant_id,
                action="accept",
            )

            if accepted.status != "accepted" or accepted.thread_status != "open":
                raise SystemExit("FAIL: contact request was not accepted/opened")

            sent = await service.send_thread_message(
                thread_id=accepted.thread_id,
                sender_user_id=specialist_user_id,
                text="Hello, I can help you inside SGHR Beta.",
                original_language="en",
            )

            sent_message = await session.get(Message, sent.message_id)
            message_notification = await session.get(Notification, sent.notification_id)

            if not sent_message or sent_message.receiver_user_id != client_user_id:
                raise SystemExit("FAIL: thread message was not saved for client")

            if not message_notification or message_notification.notification_type != "message_received":
                raise SystemExit("FAIL: message notification was not created")

            receipt = await service.mark_thread_message_read(
                thread_id=sent.thread_id,
                message_id=sent.message_id,
                user_id=client_user_id,
            )

            read_receipt = await session.get(MessageReadReceipt, receipt.receipt_id)
            if not read_receipt:
                raise SystemExit("FAIL: read receipt was not created")

            completed = await service.complete_thread(
                thread_id=sent.thread_id,
                actor_user_id=client_user_id,
            )

            if completed.status != "completed":
                raise SystemExit("FAIL: thread was not completed")

            print("OK: beta 0.6 contact chat smoke passed")

        finally:
            await session.rollback()

            if client_platform_id:
                await cleanup_user(session, client_platform_id)

            if specialist_platform_id:
                await cleanup_user(session, specialist_platform_id)

            if tenant_id:
                await cleanup_legal_documents(session, tenant_id)


if __name__ == "__main__":
    asyncio.run(main())