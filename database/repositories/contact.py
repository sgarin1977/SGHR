import secrets
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ContactRequest,
    ConversationThread,
    EventLog,
    Message,
    MessageReadReceipt,
    Notification,
    Specialist,
    User,
    TranslationJob,
)
from database.repositories.translation import TranslationRepository

class ContactChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _normalize_translation_language(self, language: str | None) -> str:
        return language if language in {"ru", "en", "pt"} else "ru"

    async def _create_translation_job_if_needed(
        self,
        *,
        tenant_id: UUID,
        message_id: UUID,
        source_language: str,
        receiver_user_id: UUID,
    ) -> TranslationJob | None:
        translation_repository = TranslationRepository(self.session)

        target_language = await translation_repository.get_user_message_language(
            receiver_user_id
        )
        normalized_source_language = self._normalize_translation_language(source_language)
        auto_translate_enabled = await translation_repository.is_auto_translate_enabled(
            receiver_user_id
        )

        message = await self.session.get(Message, message_id)
        if not message:
            return None

        if not auto_translate_enabled or target_language == normalized_source_language:
            message.translation_status = "not_needed"
            message.translated_text = None
            message.translated_language = None
            await self.session.flush()
            return None

        message.translation_status = "pending"

        translation_job = TranslationJob(
            tenant_id=tenant_id,
            message_id=message_id,
            source_language=normalized_source_language,
            target_language=target_language,
            status="pending",
            retry_count=0,
            max_retries=3,
        )
        self.session.add(translation_job)
        await self.session.flush()

        return translation_job
    async def get_active_specialist(
        self,
        specialist_id: UUID,
    ) -> Specialist | None:
        result = await self.session.execute(
            select(Specialist)
            .join(User, User.id == Specialist.user_id)
            .where(
                Specialist.id == specialist_id,
                Specialist.status == "active",
                User.status.notin_(["blocked", "deleted"]),
            )
        )
        return result.scalar_one_or_none()

    async def get_thread_by_contact_request_id(
        self,
        contact_request_id: UUID,
    ) -> ConversationThread | None:
        result = await self.session.execute(
            select(ConversationThread).where(
                ConversationThread.context_type == "contact_request",
                ConversationThread.context_id == contact_request_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_contact_request_by_token(
        self,
        token: str,
    ) -> ContactRequest | None:
        result = await self.session.execute(
            select(ContactRequest).where(
                ContactRequest.extra_metadata["contact_token"].astext == token,
            )
        )
        return result.scalar_one_or_none()

    async def set_contact_request_status(
        self,
        *,
        contact_request_id: UUID,
        status: str,
        thread_status: str,
        actor_user_id: UUID,
        tenant_id: UUID,
        platform: str = "telegram",
    ) -> tuple[ContactRequest, ConversationThread]:
        contact_request = await self.session.get(ContactRequest, contact_request_id)
        if not contact_request:
            raise ValueError("Contact request not found.")
        
        specialist = await self.session.get(Specialist, contact_request.specialist_id)
        if not specialist or specialist.user_id != actor_user_id:
            raise ValueError("Only specialist can change contact request status.")

        if contact_request.status != "new":
            raise ValueError("Contact request is not new.")

        thread = await self.get_thread_by_contact_request_id(contact_request_id)
        if not thread:
            raise ValueError("Conversation thread not found.")

        contact_request.status = status
        thread.status = thread_status

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=actor_user_id,
                event_type=f"contact_request_{status}",
                entity_type="contact_request",
                entity_id=contact_request.id,
                platform=platform,
                payload={
                    "thread_id": str(thread.id),
                    "thread_status": thread_status,
                },
            )
        )

        await self.session.commit()
        return contact_request, thread

    async def create_contact_request_with_thread(
        self,
        *,
        tenant_id: UUID,
        from_user_id: UUID,
        specialist_id: UUID,
        specialist_user_id: UUID,
        message: str,
        original_language: str,
        platform: str = "telegram",
    ) -> tuple[ContactRequest, ConversationThread, Message, Notification]:
        
        contact_token = secrets.token_urlsafe(9)
        contact_request = ContactRequest(
            tenant_id=tenant_id,
            from_user_id=from_user_id,
            specialist_id=specialist_id,
            message=message,
            original_language=original_language,
            status="new",
            extra_metadata={
                "contact_token": contact_token,
            },
        )
        self.session.add(contact_request)
        await self.session.flush()

        thread = ConversationThread(
            tenant_id=tenant_id,
            context_type="contact_request",
            context_id=contact_request.id,
            client_user_id=from_user_id,
            specialist_id=specialist_id,
            status="waiting_specialist",
        )
        self.session.add(thread)
        await self.session.flush()

        first_message = Message(
            tenant_id=tenant_id,
            thread_id=thread.id,
            sender_user_id=from_user_id,
            receiver_user_id=specialist_user_id,
            original_text=message,
            original_language=original_language,
            translation_status="pending",
            is_system=False,
            is_masked=False,
        )
        self.session.add(first_message)
        await self.session.flush()

        await self._create_translation_job_if_needed(
            tenant_id=tenant_id,
            message_id=first_message.id,
            source_language=original_language,
            receiver_user_id=specialist_user_id,
        )

        notification = Notification(
            tenant_id=tenant_id,
            user_id=specialist_user_id,
            notification_type="contact_request_created",
            channel=platform,
            payload={
                "contact_request_id": str(contact_request.id),
                "thread_id": str(thread.id),
                "message_id": str(first_message.id) if first_message.id else None,
                "specialist_id": str(specialist_id),
                "from_user_id": str(from_user_id),
                "contact_token": contact_token,
            },
            status="pending",
        )
        self.session.add(notification)

        self.session.add_all(
            [
                EventLog(
                    tenant_id=tenant_id,
                    user_id=from_user_id,
                    event_type="contact_request_created",
                    entity_type="contact_request",
                    entity_id=contact_request.id,
                    platform=platform,
                    payload={
                        "specialist_id": str(specialist_id),
                        "thread_id": str(thread.id),
                    },
                ),
                EventLog(
                    tenant_id=tenant_id,
                    user_id=from_user_id,
                    event_type="thread_created",
                    entity_type="conversation_thread",
                    entity_id=thread.id,
                    platform=platform,
                    payload={
                        "contact_request_id": str(contact_request.id),
                        "specialist_id": str(specialist_id),
                    },
                ),
                EventLog(
                    tenant_id=tenant_id,
                    user_id=from_user_id,
                    event_type="message_sent",
                    entity_type="message",
                    entity_id=first_message.id,
                    platform=platform,
                    payload={
                        "thread_id": str(thread.id),
                        "receiver_user_id": str(specialist_user_id),
                    },
                ),
            ]
        )

        await self.session.commit()
        return contact_request, thread, first_message, notification

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

    async def create_thread_message(
        self,
        *,
        thread_id: UUID,
        sender_user_id: UUID,
        original_text: str,
        original_language: str,
        platform: str = "telegram",
    ) -> tuple[ConversationThread, Message, Notification]:
        thread = await self.get_thread_for_user(
            thread_id=thread_id,
            user_id=sender_user_id,
        )
        if not thread:
            raise ValueError("Conversation thread not found.")

        if thread.status not in {"open", "waiting_client", "waiting_specialist", "in_discussion"}:
            raise ValueError("Conversation thread is not open for messages.")

        specialist = await self.session.get(Specialist, thread.specialist_id)
        if not specialist:
            raise ValueError("Specialist not found.")

        if sender_user_id == thread.client_user_id:
            receiver_user_id = specialist.user_id
        elif sender_user_id == specialist.user_id:
            receiver_user_id = thread.client_user_id
        else:
            raise ValueError("User is not a thread participant.")

        message = Message(
            tenant_id=thread.tenant_id,
            thread_id=thread.id,
            sender_user_id=sender_user_id,
            receiver_user_id=receiver_user_id,
            original_text=original_text,
            original_language=original_language,
            translation_status="pending",
            is_system=False,
            is_masked=False,
        )
        self.session.add(message)
        await self.session.flush()

        await self._create_translation_job_if_needed(
            tenant_id=thread.tenant_id,
            message_id=message.id,
            source_language=original_language,
            receiver_user_id=receiver_user_id,
        )


        notification = Notification(
            tenant_id=thread.tenant_id,
            user_id=receiver_user_id,
            notification_type="message_received",
            channel=platform,
            payload={
                "thread_id": str(thread.id),
                "message_id": str(message.id),
                "sender_user_id": str(sender_user_id),
            },
            status="pending",
        )
        self.session.add(notification)

        thread.status = "in_discussion"

        self.session.add(
            EventLog(
                tenant_id=thread.tenant_id,
                user_id=sender_user_id,
                event_type="message_sent",
                entity_type="message",
                entity_id=message.id,
                platform=platform,
                payload={
                    "thread_id": str(thread.id),
                    "receiver_user_id": str(receiver_user_id),
                },
            )
        )

        await self.session.commit()
        return thread, message, notification

    async def mark_thread_message_read(
        self,
        *,
        thread_id: UUID,
        message_id: UUID,
        user_id: UUID,
    ) -> tuple[ConversationThread, MessageReadReceipt]:
        thread = await self.get_thread_for_user(
            thread_id=thread_id,
            user_id=user_id,
        )
        if not thread:
            raise ValueError("Conversation thread not found.")

        message = await self.session.get(Message, message_id)
        if not message or message.thread_id != thread.id:
            raise ValueError("Message not found in conversation thread.")

        if message.receiver_user_id != user_id:
            raise ValueError("Only message receiver can mark it as read.")

        existing_result = await self.session.execute(
            select(MessageReadReceipt).where(
                MessageReadReceipt.message_id == message_id,
                MessageReadReceipt.user_id == user_id,
            )
        )
        existing_receipt = existing_result.scalar_one_or_none()
        if existing_receipt:
            return thread, existing_receipt

        receipt = MessageReadReceipt(
            message_id=message_id,
            user_id=user_id,
        )
        self.session.add(receipt)
        await self.session.commit()

        return thread, receipt

    async def complete_thread(
        self,
        *,
        thread_id: UUID,
        actor_user_id: UUID,
        platform: str = "telegram",
    ) -> ConversationThread:
        thread = await self.get_thread_for_user(
            thread_id=thread_id,
            user_id=actor_user_id,
        )
        if not thread:
            raise ValueError("Conversation thread not found.")

        if thread.status in {"closed", "completed", "restricted", "disputed"}:
            raise ValueError("Conversation thread cannot be completed.")

        thread.status = "completed"

        self.session.add(
            EventLog(
                tenant_id=thread.tenant_id,
                user_id=actor_user_id,
                event_type="thread_completed",
                entity_type="conversation_thread",
                entity_id=thread.id,
                platform=platform,
                payload={},
            )
        )

        await self.session.commit()
        return thread

    async def mark_message_read(
        self,
        *,
        message_id: UUID,
        user_id: UUID,
    ) -> MessageReadReceipt:
        receipt = MessageReadReceipt(
            message_id=message_id,
            user_id=user_id,
        )
        self.session.add(receipt)
        await self.session.commit()
        return receipt