import secrets
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from database.models import (
    Blacklist,
    ContactRequest,
    ConversationParticipant,
    ConversationThread,
    EventLog,
    Message,
    MessageReadReceipt,
    Notification,
    Profession,
    Specialist,
    SpecialistProfession,
    TranslationJob,
    User,
    UserAccount,
    UserRoleMapping,
)
from database.repositories.translation import TranslationRepository

class ContactChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def user_has_contact_admin_access(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> bool:
        result = await self.session.execute(
            select(UserRoleMapping.id)
            .where(
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.status == "active",
                UserRoleMapping.role.in_(
                    {"admin", "super_admin"}
                ),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

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
        message = await self.session.get(Message, message_id)
        if not message:
            return None

        message.translation_status = "not_needed"
        message.translated_text = None
        message.translated_language = None
        await self.session.flush()

        return None
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

    async def resolve_contact_profession_id(
        self,
        *,
        specialist_id: UUID,
        requested_profession_id: UUID | None = None,
    ) -> UUID | None:
        query = select(SpecialistProfession.profession_id).where(
            SpecialistProfession.specialist_id == specialist_id,
            SpecialistProfession.status == "active",
        )

        if requested_profession_id is not None:
            query = query.where(
                SpecialistProfession.profession_id == requested_profession_id,
            )

        query = query.order_by(
            SpecialistProfession.is_primary.desc(),
            SpecialistProfession.created_at.asc(),
        ).limit(1)

        result = await self.session.execute(query)
        profession_id = result.scalar_one_or_none()

        if profession_id is not None:
            return profession_id

        specialist = await self.session.get(Specialist, specialist_id)
        return specialist.profession_id if specialist else None

    async def get_active_contact_request_for_pair(
        self,
        *,
        tenant_id: UUID,
        from_user_id: UUID,
        specialist_id: UUID,
        profession_id: UUID,
    ) -> ContactRequest | None:
        result = await self.session.execute(
            select(ContactRequest)
            .where(
                ContactRequest.tenant_id == tenant_id,
                ContactRequest.from_user_id == from_user_id,
                ContactRequest.specialist_id == specialist_id,
                ContactRequest.profession_id == profession_id,
                ContactRequest.status.in_(["new", "accepted"]),
            )
            .order_by(ContactRequest.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
    

    async def request_thread_completion(
        self,
        *,
        tenant_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        platform: str = "telegram",
    ) -> tuple[ConversationThread, Notification]:
        thread = await self.get_thread_for_user(
            thread_id=thread_id,
            user_id=actor_user_id,
        )

        if not thread or thread.tenant_id != tenant_id:
            raise ValueError("Conversation thread not found.")

        if (
            thread.context_type != "contact_request"
            or not thread.context_id
        ):
            raise ValueError(
                "Conversation thread is not linked to a contact request."
            )

        specialist = await self.session.get(
            Specialist,
            thread.specialist_id,
        )
        if not specialist or specialist.user_id != actor_user_id:
            raise ValueError(
                "Only specialist can request completion."
            )

        contact_request = await self.session.get(
            ContactRequest,
            thread.context_id,
        )
        if (
            not contact_request
            or contact_request.tenant_id != tenant_id
        ):
            raise ValueError("Contact request not found.")

        if contact_request.status != "accepted":
            raise ValueError(
                "Only accepted contact request can be completed."
            )

        if thread.status in {
            "closed",
            "completed",
            "restricted",
            "disputed",
        }:
            raise ValueError(
                "Conversation thread completion is not available."
            )

        requested_at = datetime.utcnow()

        metadata = dict(contact_request.extra_metadata or {})
        if metadata.get("completion_requested_at"):
            raise ValueError(
                "Completion has already been requested."
            )

        metadata["completion_requested_at"] = (
            requested_at.isoformat()
        )
        metadata["completion_requested_by_user_id"] = str(
            actor_user_id
        )
        contact_request.extra_metadata = metadata
        contact_request.updated_at = requested_at

        notification = Notification(
            tenant_id=tenant_id,
            user_id=thread.client_user_id,
            notification_type="completion_requested",
            channel=platform,
            payload={
                "thread_id": str(thread.id),
                "contact_request_id": str(contact_request.id),
                "requested_by_user_id": str(actor_user_id),
                "requested_at": requested_at.isoformat(),
            },
            status="pending",
        )
        self.session.add(notification)

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=actor_user_id,
                event_type="completion_requested",
                entity_type="contact_request",
                entity_id=contact_request.id,
                payload={
                    "thread_id": str(thread.id),
                    "requested_for_user_id": str(
                        thread.client_user_id
                    ),
                    "requested_at": requested_at.isoformat(),
                },
                platform=platform,
            )
        )

        await self.session.commit()
        return thread, notification

    async def list_completion_escalation_candidates(
        self,
        *,
        limit: int = 100,
    ) -> list[ContactRequest]:
        result = await self.session.execute(
            select(ContactRequest)
            .where(
                ContactRequest.status == "accepted",
                ContactRequest.extra_metadata[
                    "completion_requested_at"
                ].astext.isnot(None),
                ContactRequest.extra_metadata[
                    "completion_escalated_ticket_id"
                ].astext.is_(None),
            )
            .order_by(ContactRequest.updated_at.asc())
            .limit(max(1, min(int(limit), 500)))
        )
        return list(result.scalars().all())

    async def get_completion_escalation_candidate_for_update(
        self,
        *,
        contact_request_id: UUID,
    ) -> ContactRequest | None:
        result = await self.session.execute(
            select(ContactRequest)
            .where(
                ContactRequest.id == contact_request_id,
                ContactRequest.status == "accepted",
            )
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def mark_completion_escalated(
        self,
        *,
        contact_request: ContactRequest,
        support_ticket_id: UUID,
        escalated_at: datetime,
    ) -> None:
        metadata = dict(
            contact_request.extra_metadata or {}
        )

        if metadata.get("completion_escalated_ticket_id"):
            raise ValueError(
                "Completion request is already escalated."
            )

        metadata["completion_escalated_ticket_id"] = str(
            support_ticket_id
        )
        metadata["completion_escalated_at"] = (
            escalated_at.isoformat()
        )

        contact_request.extra_metadata = metadata
        contact_request.updated_at = escalated_at

        self.session.add(
            EventLog(
                tenant_id=contact_request.tenant_id,
                user_id=contact_request.from_user_id,
                event_type="completion_escalated",
                entity_type="contact_request",
                entity_id=contact_request.id,
                platform="system",
                payload={
                    "support_ticket_id": str(
                        support_ticket_id
                    ),
                    "escalated_at": (
                        escalated_at.isoformat()
                    ),
                },
            )
        )

        await self.session.flush()

    async def get_contact_request_by_escalated_ticket_id(
        self,
        *,
        tenant_id: UUID,
        support_ticket_id: UUID,
    ) -> ContactRequest | None:
        result = await self.session.execute(
            select(ContactRequest)
            .where(
                ContactRequest.tenant_id == tenant_id,
                ContactRequest.extra_metadata[
                    "completion_escalated_ticket_id"
                ].astext == str(support_ticket_id),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def complete_contact_request_by_admin(
        self,
        *,
        contact_request: ContactRequest,
        thread: ConversationThread,
        admin_user_id: UUID,
        reason: str,
        completed_at: datetime,
        platform: str = "telegram",
    ) -> None:
        thread.status = "completed"
        thread.updated_at = completed_at

        contact_request.status = "completed"
        contact_request.updated_at = completed_at

        metadata = dict(
            contact_request.extra_metadata or {}
        )
        metadata["completed_at"] = completed_at.isoformat()
        metadata["completed_by_user_id"] = str(
            admin_user_id
        )
        metadata["completion_source"] = "admin_escalation"
        metadata["completion_reason"] = reason
        contact_request.extra_metadata = metadata

        self.session.add_all(
            [
                EventLog(
                    tenant_id=contact_request.tenant_id,
                    user_id=admin_user_id,
                    event_type="thread_completed",
                    entity_type="conversation_thread",
                    entity_id=thread.id,
                    platform=platform,
                    payload={
                        "contact_request_id": str(
                            contact_request.id
                        ),
                        "completion_source": (
                            "admin_escalation"
                        ),
                        "reason": reason,
                    },
                ),
                EventLog(
                    tenant_id=contact_request.tenant_id,
                    user_id=admin_user_id,
                    event_type="request_completed",
                    entity_type="contact_request",
                    entity_id=contact_request.id,
                    platform=platform,
                    payload={
                        "thread_id": str(thread.id),
                        "completion_source": (
                            "admin_escalation"
                        ),
                        "reason": reason,
                    },
                ),
            ]
        )

        await self.session.flush()

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
        decline_reason: str | None = None,
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
        if status == "declined":
            metadata = dict(contact_request.extra_metadata or {})
            metadata["decline_reason"] = (decline_reason or "").strip()
            metadata["declined_by_user_id"] = str(actor_user_id)
            metadata["declined_at"] = datetime.utcnow().isoformat()
            contact_request.extra_metadata = metadata

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
                    "decline_reason": (
                        (decline_reason or "").strip()
                        if status == "declined"
                        else None
                    ),
                },
            )
        )

        await self.session.commit()
        return contact_request, thread


    async def cancel_contact_request_by_client(
        self,
        *,
        contact_request_id: UUID,
        actor_user_id: UUID,
        tenant_id: UUID,
        platform: str = "telegram",
    ) -> tuple[ContactRequest, ConversationThread]:
        contact_request = await self.session.get(ContactRequest, contact_request_id)
        if not contact_request or contact_request.tenant_id != tenant_id:
            raise ValueError("Contact request not found.")

        if contact_request.from_user_id != actor_user_id:
            raise ValueError("Only request owner can cancel contact request.")

        if contact_request.status not in {"new", "accepted"}:
            raise ValueError("Contact request cannot be cancelled.")

        thread = await self.get_thread_by_contact_request_id(contact_request_id)
        if not thread:
            raise ValueError("Conversation thread not found.")

        previous_status = contact_request.status
        contact_request.status = "cancelled"
        contact_request.updated_at = datetime.utcnow()

        if thread.status not in {"completed", "closed", "restricted", "disputed"}:
            thread.status = "closed"
            thread.updated_at = datetime.utcnow()

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=actor_user_id,
                event_type="request_cancelled",
                entity_type="contact_request",
                entity_id=contact_request.id,
                platform=platform,
                payload={
                    "thread_id": str(thread.id),
                    "thread_status": thread.status,
                    "previous_status": previous_status,
                    "cancelled_by": "client",
                },
            )
        )

        await self.session.commit()
        return contact_request, thread

    async def cancel_contact_request_by_admin(
        self,
        *,
        contact_request_id: UUID,
        admin_user_id: UUID,
        tenant_id: UUID,
        reason: str,
        platform: str = "telegram",
    ) -> tuple[ContactRequest, ConversationThread]:
        has_access = await self.user_has_contact_admin_access(
            tenant_id=tenant_id,
            user_id=admin_user_id,
        )
        if not has_access:
            raise ValueError("Admin access denied.")

        result = await self.session.execute(
            select(ContactRequest)
            .where(
                ContactRequest.id == contact_request_id,
                ContactRequest.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        contact_request = result.scalar_one_or_none()

        if not contact_request:
            raise ValueError("Contact request not found.")

        if contact_request.status not in {
            "new",
            "accepted",
        }:
            raise ValueError(
                "Contact request cannot be cancelled."
            )

        thread = await self.get_thread_by_contact_request_id(
            contact_request.id
        )
        if not thread:
            raise ValueError("Conversation thread not found.")

        cancelled_at = datetime.utcnow()
        previous_status = contact_request.status

        contact_request.status = "cancelled"
        contact_request.updated_at = cancelled_at

        metadata = dict(
            contact_request.extra_metadata or {}
        )
        metadata["cancelled_at"] = cancelled_at.isoformat()
        metadata["cancelled_by_user_id"] = str(
            admin_user_id
        )
        metadata["cancelled_by"] = "admin"
        metadata["cancellation_reason"] = reason
        contact_request.extra_metadata = metadata

        if thread.status not in {
            "completed",
            "closed",
            "restricted",
            "disputed",
        }:
            thread.status = "closed"
            thread.updated_at = cancelled_at

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="request_cancelled",
                entity_type="contact_request",
                entity_id=contact_request.id,
                platform=platform,
                payload={
                    "thread_id": str(thread.id),
                    "previous_status": previous_status,
                    "cancelled_by": "admin",
                    "reason": reason,
                },
            )
        )

        await self.session.flush()
        return contact_request, thread

    async def create_contact_request_with_thread(
        self,
        *,
        tenant_id: UUID,
        from_user_id: UUID,
        specialist_id: UUID,
        profession_id: UUID,
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
            profession_id=profession_id,
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

        self.session.add_all(
            [
                ConversationParticipant(
                    thread_id=thread.id,
                    user_id=from_user_id,
                    participant_role="client",
                    unread_count=0,
                ),
                ConversationParticipant(
                    thread_id=thread.id,
                    user_id=specialist_user_id,
                    participant_role="specialist",
                    unread_count=1,
                ),
            ]
        )

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
                    event_type="request_created",
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

    async def _get_conversation_participant(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
    ) -> ConversationParticipant | None:
        result = await self.session.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.thread_id == thread_id,
                ConversationParticipant.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_threads_for_user(
        self,
        *,
        user_id: UUID,
        participant_role: str,
        view: str = "active",
        limit: int = 5,
        offset: int = 0,
        language: str = "ru",
    ) -> list[tuple]:
        localized_profession_name = {
            "ru": Profession.name_ru,
            "en": Profession.name_en,
            "pt": Profession.name_pt,
        }.get(language, Profession.name_ru)

        last_message_text = (
            select(Message.original_text)
            .where(Message.thread_id == ConversationThread.id)
            .order_by(Message.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        last_message_at = (
            select(Message.created_at)
            .where(Message.thread_id == ConversationThread.id)
            .order_by(Message.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )

        stmt = (
            select(
                ConversationThread,
                ConversationParticipant,
                Specialist.display_name,
                func.coalesce(
                    localized_profession_name,
                    Profession.name_ru,
                    Profession.name_en,
                    Profession.name_pt,
                    Profession.name,
                ).label("profession_name"),
                last_message_text.label("last_message_text"),
                last_message_at.label("last_message_at"),
            )
            .join(
                ConversationParticipant,
                ConversationParticipant.thread_id == ConversationThread.id,
            )
            .join(Specialist, Specialist.id == ConversationThread.specialist_id)
            .outerjoin(
                SpecialistProfession,
                (SpecialistProfession.specialist_id == Specialist.id)
                & (SpecialistProfession.status == "active")
                & (SpecialistProfession.is_primary.is_(True)),
            )
            .outerjoin(Profession, Profession.id == SpecialistProfession.profession_id)
            .where(
                ConversationParticipant.user_id == user_id,
                ConversationParticipant.participant_role == participant_role,
            )
        )

        messageable_statuses = {
            "open",
            "waiting_client",
            "waiting_specialist",
            "in_discussion",
        }

        if view == "new":
            stmt = stmt.where(
                ConversationParticipant.unread_count > 0,
                ConversationParticipant.is_archived.is_(False),
                ConversationParticipant.is_hidden.is_(False),
                ConversationThread.status.in_(messageable_statuses),
            )
        elif view == "archive":
            stmt = stmt.where(ConversationParticipant.is_archived.is_(True))
        elif view == "hidden":
            stmt = stmt.where(ConversationParticipant.is_hidden.is_(True))
        else:
            stmt = stmt.where(
                ConversationParticipant.is_archived.is_(False),
                ConversationParticipant.is_hidden.is_(False),
                ConversationThread.status.in_(messageable_statuses),
            )

        stmt = (
            stmt.order_by(
                last_message_at.desc().nullslast(),
                ConversationThread.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(stmt)
        return list(result.all())


    async def list_contact_requests_for_client(
        self,
        *,
        user_id: UUID,
        limit: int = 5,
        offset: int = 0,
        language: str = "ru",
    ) -> list[tuple]:
        localized_profession_name = {
            "ru": Profession.name_ru,
            "en": Profession.name_en,
            "pt": Profession.name_pt,
        }.get(language, Profession.name_ru)

        result = await self.session.execute(
            select(
                ContactRequest,
                ConversationThread.id.label("thread_id"),
                Specialist.display_name,
                func.coalesce(
                    localized_profession_name,
                    Profession.name_ru,
                    Profession.name_en,
                    Profession.name_pt,
                    Profession.name,
                ).label("profession_name"),
            )
            .join(Specialist, Specialist.id == ContactRequest.specialist_id)
            .outerjoin(
                ConversationThread,
                (ConversationThread.context_type == "contact_request")
                & (ConversationThread.context_id == ContactRequest.id),
            )
            .outerjoin(
                SpecialistProfession,
                (SpecialistProfession.specialist_id == Specialist.id)
                & (SpecialistProfession.status == "active")
                & (SpecialistProfession.is_primary.is_(True)),
            )
            .outerjoin(Profession, Profession.id == SpecialistProfession.profession_id)
            .where(ContactRequest.from_user_id == user_id)
            .order_by(ContactRequest.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.all())

    async def list_contact_requests_for_specialist(
        self,
        *,
        specialist_id: UUID,
        status: str = "new",
        limit: int = 5,
        offset: int = 0,
        language: str = "ru",
    ) -> list[tuple]:
        localized_profession_name = {
            "ru": Profession.name_ru,
            "en": Profession.name_en,
            "pt": Profession.name_pt,
        }.get(language, Profession.name_ru)

        result = await self.session.execute(
            select(
                ContactRequest,
                ConversationThread.id.label("thread_id"),
                User.id.label("client_user_id"),
                UserAccount.display_name,
                UserAccount.first_name,
                UserAccount.username,
                func.coalesce(
                    localized_profession_name,
                    Profession.name_ru,
                    Profession.name_en,
                    Profession.name_pt,
                    Profession.name,
                ).label("profession_name"),
            )
            .join(User, User.id == ContactRequest.from_user_id)
            .outerjoin(
                UserAccount,
                (UserAccount.user_id == User.id)
                & (UserAccount.platform == "telegram"),
            )
            .join(Specialist, Specialist.id == ContactRequest.specialist_id)
            .outerjoin(
                ConversationThread,
                (ConversationThread.context_type == "contact_request")
                & (ConversationThread.context_id == ContactRequest.id),
            )
            .outerjoin(
                SpecialistProfession,
                (SpecialistProfession.specialist_id == Specialist.id)
                & (SpecialistProfession.status == "active")
                & (SpecialistProfession.is_primary.is_(True)),
            )
            .outerjoin(Profession, Profession.id == SpecialistProfession.profession_id)
            .where(
                ContactRequest.specialist_id == specialist_id,
                ContactRequest.status == status,
            )
            .order_by(ContactRequest.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.all())

    async def get_contact_request_detail_for_client(
        self,
        *,
        contact_request_id: UUID,
        user_id: UUID,
        language: str = "ru",
    ) -> tuple | None:
        localized_profession_name = {
            "ru": Profession.name_ru,
            "en": Profession.name_en,
            "pt": Profession.name_pt,
        }.get(language, Profession.name_ru)

        result = await self.session.execute(
            select(
                ContactRequest,
                ConversationThread.id.label("thread_id"),
                Specialist.display_name,
                func.coalesce(
                    localized_profession_name,
                    Profession.name_ru,
                    Profession.name_en,
                    Profession.name_pt,
                    Profession.name,
                ).label("profession_name"),
            )
            .join(Specialist, Specialist.id == ContactRequest.specialist_id)
            .outerjoin(
                ConversationThread,
                (ConversationThread.context_type == "contact_request")
                & (ConversationThread.context_id == ContactRequest.id),
            )
            .outerjoin(
                SpecialistProfession,
                (SpecialistProfession.specialist_id == Specialist.id)
                & (SpecialistProfession.status == "active")
                & (SpecialistProfession.is_primary.is_(True)),
            )
            .outerjoin(Profession, Profession.id == SpecialistProfession.profession_id)
            .where(
                ContactRequest.id == contact_request_id,
                ContactRequest.from_user_id == user_id,
            )
        )
        return result.one_or_none()

    async def get_thread_detail_for_user(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
        language: str = "ru",
        messages_limit: int = 10,
    ) -> tuple | None:
        localized_profession_name = {
            "ru": Profession.name_ru,
            "en": Profession.name_en,
            "pt": Profession.name_pt,
        }.get(language, Profession.name_ru)

        result = await self.session.execute(
            select(
                ConversationThread,
                ContactRequest,
                Specialist.display_name,
                func.coalesce(
                    localized_profession_name,
                    Profession.name_ru,
                    Profession.name_en,
                    Profession.name_pt,
                    Profession.name,
                ).label("profession_name"),
            )
            .join(ContactRequest, ContactRequest.id == ConversationThread.context_id)
            .join(Specialist, Specialist.id == ConversationThread.specialist_id)
            .outerjoin(
                SpecialistProfession,
                (SpecialistProfession.specialist_id == Specialist.id)
                & (SpecialistProfession.status == "active")
                & (SpecialistProfession.is_primary.is_(True)),
            )
            .outerjoin(Profession, Profession.id == SpecialistProfession.profession_id)
            .where(
                ConversationThread.id == thread_id,
                ConversationThread.context_type == "contact_request",
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
        detail = result.one_or_none()
        if not detail:
            return None

        messages_result = await self.session.execute(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.created_at.desc())
            .limit(messages_limit)
        )
        messages = list(reversed(messages_result.scalars().all()))

        return (*detail, messages)

    async def _is_user_blacklisted_or_blocked(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> bool:
        user = await self.session.get(User, user_id)
        if not user or user.status in {"blocked", "deleted"}:
            return True

        result = await self.session.execute(
            select(Blacklist.id).where(
                Blacklist.tenant_id == tenant_id,
                Blacklist.user_id == user_id,
                Blacklist.status == "active",
            )
        )
        return result.scalar_one_or_none() is not None

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
        if await self._is_user_blacklisted_or_blocked(
            tenant_id=thread.tenant_id,
            user_id=sender_user_id,
        ):
            raise ValueError("Conversation thread is read-only for blacklisted users.")

        specialist = await self.session.get(Specialist, thread.specialist_id)
        if not specialist:
            raise ValueError("Specialist not found.")

        if sender_user_id == thread.client_user_id:
            receiver_user_id = specialist.user_id
        elif sender_user_id == specialist.user_id:
            receiver_user_id = thread.client_user_id
        else:
            raise ValueError("User is not a thread participant.")
        if await self._is_user_blacklisted_or_blocked(
            tenant_id=thread.tenant_id,
            user_id=receiver_user_id,
        ):
            raise ValueError("Conversation thread is read-only for blacklisted users.")


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

        sender_participant = await self._get_conversation_participant(
            thread_id=thread.id,
            user_id=sender_user_id,
        )
        receiver_participant = await self._get_conversation_participant(
            thread_id=thread.id,
            user_id=receiver_user_id,
        )

        if sender_participant:
            sender_participant.unread_count = 0
            sender_participant.last_read_message_id = message.id
            sender_participant.last_read_at = datetime.utcnow()
            sender_participant.updated_at = datetime.utcnow()

        if receiver_participant:
            receiver_participant.unread_count += 1

            receiver_participant.is_archived = False
            receiver_participant.archived_at = None

            receiver_participant.is_hidden = False
            receiver_participant.hidden_at = None

            receiver_participant.updated_at = datetime.utcnow()

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

        participant = await self._get_conversation_participant(
            thread_id=thread.id,
            user_id=user_id,
        )
        if participant:
            participant.unread_count = 0
            participant.last_read_message_id = message.id
            participant.last_read_at = datetime.utcnow()
            participant.updated_at = datetime.utcnow()

        await self.session.commit()

        return thread, receipt

    async def set_thread_participant_visibility(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
        is_archived: bool | None = None,
        is_hidden: bool | None = None,
        platform: str = "telegram",
    ) -> tuple[ConversationThread, ConversationParticipant]:
        thread = await self.get_thread_for_user(
            thread_id=thread_id,
            user_id=user_id,
        )
        if not thread:
            raise ValueError("Conversation thread not found.")

        participant = await self._get_conversation_participant(
            thread_id=thread.id,
            user_id=user_id,
        )
        if not participant:
            raise ValueError("Conversation participant not found.")

        now = datetime.utcnow()

        if is_archived is not None:
            participant.is_archived = is_archived
            participant.archived_at = now if is_archived else None

        if is_hidden is not None:
            participant.is_hidden = is_hidden
            participant.hidden_at = now if is_hidden else None

        participant.updated_at = now

        events = [
            EventLog(
                tenant_id=thread.tenant_id,
                user_id=user_id,
                event_type="thread_visibility_changed",
                entity_type="conversation_thread",
                entity_id=thread.id,
                platform=platform,
                payload={
                    "is_archived": participant.is_archived,
                    "is_hidden": participant.is_hidden,
                },
            )
        ]

        if is_archived is True:
            events.append(
                EventLog(
                    tenant_id=thread.tenant_id,
                    user_id=user_id,
                    event_type="dialog_archived",
                    entity_type="conversation_thread",
                    entity_id=thread.id,
                    platform=platform,
                    payload={
                        "is_archived": participant.is_archived,
                        "is_hidden": participant.is_hidden,
                    },
                )
            )

        self.session.add_all(events)
        await self.session.commit()
        return thread, participant

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

        if actor_user_id != thread.client_user_id:
            raise ValueError(
                "Only client can confirm request completion."
            )

        if (
            thread.context_type != "contact_request"
            or not thread.context_id
        ):
            raise ValueError(
                "Conversation thread is not linked to a contact request."
            )

        contact_request = await self.session.get(
            ContactRequest,
            thread.context_id,
        )
        if not contact_request:
            raise ValueError("Contact request not found.")

        if contact_request.status != "accepted":
            raise ValueError(
                "Only accepted contact request can be completed."
            )

        metadata = dict(
            contact_request.extra_metadata or {}
        )
        if not metadata.get("completion_requested_at"):
            raise ValueError(
                "Specialist has not requested completion."
            )

        if thread.status in {
            "closed",
            "completed",
            "restricted",
            "disputed",
        }:
            raise ValueError(
                "Conversation thread cannot be completed."
            )

        completed_at = datetime.utcnow()

        thread.status = "completed"
        thread.updated_at = completed_at

        contact_request.status = "completed"
        contact_request.updated_at = completed_at

        metadata["completed_at"] = completed_at.isoformat()
        metadata["completed_by_user_id"] = str(actor_user_id)
        metadata["completion_source"] = "client"
        contact_request.extra_metadata = metadata

        self.session.add_all(
            [
                EventLog(
                    tenant_id=thread.tenant_id,
                    user_id=actor_user_id,
                    event_type="thread_completed",
                    entity_type="conversation_thread",
                    entity_id=thread.id,
                    platform=platform,
                    payload={
                        "contact_request_id": str(
                            contact_request.id
                        ),
                    },
                ),
                EventLog(
                    tenant_id=thread.tenant_id,
                    user_id=actor_user_id,
                    event_type="request_completed",
                    entity_type="contact_request",
                    entity_id=contact_request.id,
                    platform=platform,
                    payload={
                        "thread_id": str(thread.id),
                        "completion_source": "client",
                    },
                ),
            ]
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