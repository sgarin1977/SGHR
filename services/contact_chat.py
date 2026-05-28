from dataclasses import dataclass
from uuid import UUID

from database.repositories.contact import ContactChatRepository
from database.repositories.contact_detection import ContactDetectionRepository
from services.contact_detection import ContactDetectionService
from database.repositories.rate_limit import RateLimitRepository
from services.rate_limit import RateLimitError, RateLimitService

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
            raise ContactChatError(
                "You already have an active contact request with this specialist."
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
    ) -> ContactRequestStatusResult:
        if action == "accept":
            status = "accepted"
            thread_status = "open"
        elif action == "reject":
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
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ContactRequestStatusResult(
            contact_request_id=contact_request.id,
            thread_id=thread.id,
            status=contact_request.status,
            thread_status=thread.status,
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