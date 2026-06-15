from dataclasses import dataclass
from uuid import UUID

from database.repositories.contact import ContactChatRepository
from database.repositories.contact_detection import ContactDetectionRepository
from services.contact_detection import ContactDetectionService
from database.repositories.rate_limit import RateLimitRepository
from services.rate_limit import RateLimitError, RateLimitService
from datetime import datetime
class ContactChatError(Exception):
    pass


@dataclass
class ContactRequestResult:
    contact_request_id: UUID
    thread_id: UUID
    first_message_id: UUID
    notification_id: UUID
    specialist_user_id: UUID
    contact_token: str
    message_masked: bool = False
    detection_types: list[str] | None = None
    thread_restricted: bool = False
    was_existing: bool = False

@dataclass
class ContactRequestStatusResult:
    contact_request_id: UUID
    thread_id: UUID
    status: str
    thread_status: str

@dataclass
class ContactThreadMessageResult:
    thread_id: UUID
    message_id: UUID
    notification_id: UUID
    sender_user_id: UUID
    receiver_user_id: UUID
    thread_status: str
    message_masked: bool = False
    detection_types: list[str] | None = None
    thread_restricted: bool = False

@dataclass
class ContactReadReceiptResult:
    thread_id: UUID
    message_id: UUID
    user_id: UUID
    receipt_id: UUID

@dataclass
class ContactThreadStatusResult:
    thread_id: UUID
    status: str

@dataclass
class ContactThreadVisibilityResult:
    thread_id: UUID
    user_id: UUID
    is_archived: bool
    is_hidden: bool

@dataclass
class ContactThreadCompletionRequestResult:
    thread_id: UUID
    contact_request_id: UUID | None
    notification_id: UUID
    requested_for_user_id: UUID

@dataclass
class ContactThreadListItem:
    thread_id: UUID
    specialist_name: str
    profession_name: str | None
    last_message_text: str | None
    last_message_at: datetime | None
    unread_count: int
    status: str

@dataclass
class ContactThreadDetail:
    thread_id: UUID
    contact_request_id: UUID | None
    specialist_name: str
    profession_name: str | None
    request_text: str | None
    request_status: str | None
    thread_status: str
    messages: list[str]

@dataclass
class ContactRequestListItem:
    contact_request_id: UUID
    thread_id: UUID | None
    specialist_name: str
    profession_name: str | None
    message: str
    status: str
    created_at: datetime


@dataclass
class ContactRequestDetail:
    contact_request_id: UUID
    thread_id: UUID | None
    specialist_name: str
    profession_name: str | None
    message: str
    status: str
    created_at: datetime

@dataclass
class SpecialistContactRequestListItem:
    contact_request_id: UUID
    thread_id: UUID | None
    client_user_id: UUID
    client_name: str
    profession_name: str | None
    message: str
    status: str
    created_at: datetime

class ContactChatService:
    def __init__(
        self,
        repository: ContactChatRepository | None,
        rate_limit_service: RateLimitService | None = None,
    ):
        self.repository = repository

        if rate_limit_service is not None:
            self.rate_limit_service = rate_limit_service
        elif repository is not None:
            self.rate_limit_service = RateLimitService(
                RateLimitRepository(repository.session)
            )
        else:
            self.rate_limit_service = None

    def _normalize_language(self, language: str | None) -> str:
        return language if language in {"ru", "en", "pt"} else "ru"

    def _validate_contact_message(self, message: str) -> str:
        normalized = (message or "").strip()

        if len(normalized) < 10:
            raise ContactChatError("Contact message must be at least 10 characters.")

        return normalized

    async def list_specialist_requests(
        self,
        *,
        specialist_id: UUID,
        status: str = "new",
        limit: int = 5,
        offset: int = 0,
        language: str = "ru",
    ) -> list[SpecialistContactRequestListItem]:
        rows = await self.repository.list_contact_requests_for_specialist(
            specialist_id=specialist_id,
            status=status,
            limit=limit,
            offset=offset,
            language=self._normalize_language(language),
        )

        items = []
        for (
            contact_request,
            thread_id,
            client_user_id,
            display_name,
            first_name,
            username,
            profession_name,
        ) in rows:
            client_name = display_name or first_name or username or "Client"
            items.append(
                SpecialistContactRequestListItem(
                    contact_request_id=contact_request.id,
                    thread_id=thread_id,
                    client_user_id=client_user_id,
                    client_name=client_name,
                    profession_name=profession_name,
                    message=contact_request.message,
                    status=contact_request.status,
                    created_at=contact_request.created_at,
                )
            )

        return items

    async def create_contact_request(
        self,
        *,
        tenant_id: UUID,
        from_user_id: UUID,
        specialist_id: UUID,
        message: str,
        original_language: str | None = None,
    ) -> ContactRequestResult:
        normalized_message = self._validate_contact_message(message)
        if self.rate_limit_service is not None:
            try:
                await self.rate_limit_service.ensure_contact_request_allowed(
                    tenant_id=tenant_id,
                    user_id=from_user_id,
                )
            except RateLimitError as exc:
                raise ContactChatError(str(exc)) from exc

        specialist = await self.repository.get_active_specialist(specialist_id)
        if not specialist:
            raise ContactChatError("Specialist is not available for contact.")

        if specialist.user_id == from_user_id:
            raise ContactChatError("You cannot contact your own specialist profile.")

        existing_contact_request = await self.repository.get_active_contact_request_for_pair(
            tenant_id=tenant_id,
            from_user_id=from_user_id,
            specialist_id=specialist.id,
        )
        if existing_contact_request:
            existing_thread = await self.repository.get_thread_by_contact_request_id(
                existing_contact_request.id
            )
            if not existing_thread:
                raise ContactChatError("Conversation thread not found.")

            contact_token = (existing_contact_request.extra_metadata or {}).get(
                "contact_token",
                "",
            )

            return ContactRequestResult(
                contact_request_id=existing_contact_request.id,
                thread_id=existing_thread.id,
                first_message_id=existing_thread.id,
                notification_id=existing_thread.id,
                specialist_user_id=specialist.user_id,
                contact_token=contact_token,
                was_existing=True,
            )

        contact_request, thread, first_message, notification = (
            await self.repository.create_contact_request_with_thread(
                tenant_id=tenant_id,
                from_user_id=from_user_id,
                specialist_id=specialist.id,
                specialist_user_id=specialist.user_id,
                message=normalized_message,
                original_language=self._normalize_language(original_language),
            )
        )

        contact_token = (contact_request.extra_metadata or {}).get("contact_token", "")
        detection_result = await ContactDetectionService(
            ContactDetectionRepository(self.repository.session)
        ).process_message(first_message.id)
        return ContactRequestResult(
            contact_request_id=contact_request.id,
            thread_id=thread.id,
            first_message_id=first_message.id,
            notification_id=notification.id,
            specialist_user_id=specialist.user_id,
            contact_token=contact_token,
            message_masked=detection_result.is_masked,
            detection_types=detection_result.detected_types,
            thread_restricted=detection_result.thread_restricted,
        )

    async def set_contact_request_status(
        self,
        *,
        contact_request_id: UUID,
        actor_user_id: UUID,
        tenant_id: UUID,
        action: str,
        decline_reason: str | None = None,
    ) -> ContactRequestStatusResult:
        if action == "accept":
            status = "accepted"
            thread_status = "open"
        elif action == "reject":
            normalized_reason = (decline_reason or "").strip()
            if len(normalized_reason) < 3:
                raise ContactChatError("Decline reason is required.")
            status = "rejected"
            thread_status = "closed"
        else:
            raise ContactChatError("Unsupported contact request action.")

        try:
            contact_request, thread = await self.repository.set_contact_request_status(
                contact_request_id=contact_request_id,
                status=status,
                thread_status=thread_status,
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
                decline_reason=decline_reason,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ContactRequestStatusResult(
            contact_request_id=contact_request.id,
            thread_id=thread.id,
            status=contact_request.status,
            thread_status=thread.status,
        )

    async def request_thread_completion(
        self,
        *,
        tenant_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        role: str = "specialist",
    ) -> ContactThreadCompletionRequestResult:
        if role not in {"client", "specialist"}:
            raise ContactChatError("Unsupported completion requester role.")

        try:
            thread, notification = await self.repository.request_thread_completion(
                tenant_id=tenant_id,
                thread_id=thread_id,
                actor_user_id=actor_user_id,
                role=role,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ContactThreadCompletionRequestResult(
            thread_id=thread.id,
            contact_request_id=thread.context_id,
            notification_id=notification.id,
            requested_for_user_id=notification.user_id,
        )

    async def set_contact_request_status_by_token(
        self,
        *,
        contact_token: str,
        actor_user_id: UUID,
        tenant_id: UUID,
        action: str,
    ) -> ContactRequestStatusResult:
        normalized_token = (contact_token or "").strip()
        if not normalized_token:
            raise ContactChatError("Contact request token is required.")

        contact_request = await self.repository.get_contact_request_by_token(normalized_token)
        if not contact_request:
            raise ContactChatError("Contact request not found.")

        return await self.set_contact_request_status(
            contact_request_id=contact_request.id,
            actor_user_id=actor_user_id,
            tenant_id=tenant_id,
            action=action,
        )


    async def cancel_contact_request(
        self,
        *,
        contact_request_id: UUID,
        actor_user_id: UUID,
        tenant_id: UUID,
    ) -> ContactRequestStatusResult:
        try:
            contact_request, thread = await self.repository.cancel_contact_request_by_client(
                contact_request_id=contact_request_id,
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ContactRequestStatusResult(
            contact_request_id=contact_request.id,
            thread_id=thread.id,
            status=contact_request.status,
            thread_status=thread.status,
        )

    async def list_client_requests(
        self,
        *,
        user_id: UUID,
        limit: int = 5,
        offset: int = 0,
        language: str = "ru",
    ) -> list[ContactRequestListItem]:
        rows = await self.repository.list_contact_requests_for_client(
            user_id=user_id,
            limit=limit,
            offset=offset,
            language=language,
        )

        return [
            ContactRequestListItem(
                contact_request_id=request.id,
                thread_id=thread_id,
                specialist_name=specialist_name,
                profession_name=profession_name,
                message=request.message,
                status=request.status,
                created_at=request.created_at,
            )
            for request, thread_id, specialist_name, profession_name in rows
        ]


    async def get_client_request_detail(
        self,
        *,
        contact_request_id: UUID,
        user_id: UUID,
        language: str = "ru",
    ) -> ContactRequestDetail:
        row = await self.repository.get_contact_request_detail_for_client(
            contact_request_id=contact_request_id,
            user_id=user_id,
            language=language,
        )
        if not row:
            raise ContactChatError("Contact request not found.")

        request, thread_id, specialist_name, profession_name = row

        return ContactRequestDetail(
            contact_request_id=request.id,
            thread_id=thread_id,
            specialist_name=specialist_name,
            profession_name=profession_name,
            message=request.message,
            status=request.status,
            created_at=request.created_at,
        )

    async def get_thread_detail(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
        language: str = "ru",
    ) -> ContactThreadDetail:
        row = await self.repository.get_thread_detail_for_user(
            thread_id=thread_id,
            user_id=user_id,
            language=language,
        )
        if not row:
            raise ContactChatError("Conversation thread not found.")

        thread, contact_request, specialist_name, profession_name, messages = row

        return ContactThreadDetail(
            thread_id=thread.id,
            contact_request_id=contact_request.id if contact_request else None,
            specialist_name=specialist_name,
            profession_name=profession_name,
            request_text=contact_request.message if contact_request else None,
            request_status=contact_request.status if contact_request else None,
            thread_status=thread.status,
            messages=[message.original_text for message in messages],
        )

    async def send_thread_message(
        self,
        *,
        thread_id: UUID,
        sender_user_id: UUID,
        text: str,
        original_language: str | None = None,
    ) -> ContactThreadMessageResult:
        normalized_text = self._validate_contact_message(text)

        if self.rate_limit_service is not None:
            try:
                thread = await self.repository.get_thread_for_user(
                    thread_id=thread_id,
                    user_id=sender_user_id,
                )
                if not thread:
                    raise ContactChatError("Conversation thread not found.")

                await self.rate_limit_service.ensure_chat_message_allowed(
                    tenant_id=thread.tenant_id,
                    user_id=sender_user_id,
                )
            except RateLimitError as exc:
                raise ContactChatError(str(exc)) from exc

        try:
            thread, message, notification = await self.repository.create_thread_message(
                thread_id=thread_id,
                sender_user_id=sender_user_id,
                original_text=normalized_text,
                original_language=self._normalize_language(original_language),
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc
        detection_result = await ContactDetectionService(
            ContactDetectionRepository(self.repository.session)
        ).process_message(message.id)
        return ContactThreadMessageResult(
            thread_id=thread.id,
            message_id=message.id,
            notification_id=notification.id,
            sender_user_id=message.sender_user_id,
            receiver_user_id=message.receiver_user_id,
            thread_status=thread.status,
            message_masked=detection_result.is_masked,
            detection_types=detection_result.detected_types,
            thread_restricted=detection_result.thread_restricted,
        )
    
    async def mark_thread_message_read(
        self,
        *,
        thread_id: UUID,
        message_id: UUID,
        user_id: UUID,
    ) -> ContactReadReceiptResult:
        try:
            thread, receipt = await self.repository.mark_thread_message_read(
                thread_id=thread_id,
                message_id=message_id,
                user_id=user_id,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ContactReadReceiptResult(
            thread_id=thread.id,
            message_id=receipt.message_id,
            user_id=receipt.user_id,
            receipt_id=receipt.id,
        )

    async def complete_thread(
        self,
        *,
        thread_id: UUID,
        actor_user_id: UUID,
    ) -> ContactThreadStatusResult:
        try:
            thread = await self.repository.complete_thread(
                thread_id=thread_id,
                actor_user_id=actor_user_id,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ContactThreadStatusResult(
            thread_id=thread.id,
            status=thread.status,
        )
    
    async def set_thread_visibility(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
        is_archived: bool | None = None,
        is_hidden: bool | None = None,
    ) -> ContactThreadVisibilityResult:
        if is_archived is None and is_hidden is None:
            raise ContactChatError("No visibility change requested.")

        try:
            thread, participant = await self.repository.set_thread_participant_visibility(
                thread_id=thread_id,
                user_id=user_id,
                is_archived=is_archived,
                is_hidden=is_hidden,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ContactThreadVisibilityResult(
            thread_id=thread.id,
            user_id=participant.user_id,
            is_archived=participant.is_archived,
            is_hidden=participant.is_hidden,
        )
    
    async def list_client_threads(
        self,
        *,
        user_id: UUID,
        view: str = "active",
        limit: int = 5,
        offset: int = 0,
        language: str = "ru",
    ) -> list[ContactThreadListItem]:
        rows = await self.repository.list_threads_for_user(
            user_id=user_id,
            participant_role="client",
            view=view,
            limit=limit,
            offset=offset,
            language=language,
        )

        return [
            ContactThreadListItem(
                thread_id=thread.id,
                specialist_name=specialist_name,
                profession_name=profession_name,
                last_message_text=last_message_text,
                last_message_at=last_message_at,
                unread_count=int(participant.unread_count or 0),
                status=thread.status,
            )
            for (
                thread,
                participant,
                specialist_name,
                profession_name,
                last_message_text,
                last_message_at,
            ) in rows
        ]
    
    async def list_specialist_threads(
        self,
        *,
        user_id: UUID,
        view: str = "active",
        limit: int = 5,
        offset: int = 0,
        language: str = "ru",
    ) -> list[ContactThreadListItem]:
        rows = await self.repository.list_threads_for_user(
            user_id=user_id,
            participant_role="specialist",
            view=view,
            limit=limit,
            offset=offset,
            language=language,
        )

        return [
            ContactThreadListItem(
                thread_id=thread.id,
                specialist_name=specialist_name,
                profession_name=profession_name,
                last_message_text=last_message_text,
                last_message_at=last_message_at,
                unread_count=int(participant.unread_count or 0),
                status=thread.status,
            )
            for (
                thread,
                participant,
                specialist_name,
                profession_name,
                last_message_text,
                last_message_at,
            ) in rows
        ]