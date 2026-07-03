from dataclasses import dataclass
from uuid import UUID

from database.repositories.contact import ContactChatRepository
from database.repositories.support import SupportRepository
from database.repositories.contact_detection import ContactDetectionRepository
from services.contact_detection import ContactDetectionService
from database.repositories.rate_limit import RateLimitRepository
from services.rate_limit import RateLimitError, RateLimitService
from datetime import datetime, timedelta
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
class ServiceOrderDraftResult:
    order_id: UUID
    thread_id: UUID
    contact_request_id: UUID | None
    status: str

@dataclass
class ServiceOrderStatusResult:
    order_id: UUID
    thread_id: UUID
    contact_request_id: UUID | None
    status: str

@dataclass
class ServiceOrderListItem:
    order_id: UUID
    thread_id: UUID
    contact_request_id: UUID | None
    specialist_name: str
    client_name: str
    profession_name: str | None
    status: str
    description: str | None
    schedule_text: str | None
    agreed_amount: float | None
    currency: str
    created_at: datetime
    is_client: bool

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

@dataclass(frozen=True)
class OverdueCompletionRequest:
    contact_request_id: UUID
    tenant_id: UUID
    client_user_id: UUID
    specialist_id: UUID
    requested_at: datetime

@dataclass(frozen=True)
class CompletionEscalationResult:
    processed_count: int
    skipped_count: int
    support_ticket_ids: tuple[UUID, ...]

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
    client_name: str
    profession_name: str | None
    request_text: str | None
    request_status: str | None
    thread_status: str
    active_order_id: UUID | None
    active_order_status: str | None
    active_order_created_by: UUID | None
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

@dataclass
class ServiceOrderFormData:
    description: str
    schedule_text: str | None
    agreed_amount: float | None
    currency: str

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

    def parse_service_order_form(self, text: str) -> ServiceOrderFormData:
        lines = [
            line.strip()
            for line in (text or "").splitlines()
            if line.strip()
        ]

        if not lines:
            raise ContactChatError("Order description is required.")

        description = lines[0]
        if len(description) < 5:
            raise ContactChatError("Order description is too short.")

        schedule_text = lines[1] if len(lines) > 1 else None
        amount_line = lines[2] if len(lines) > 2 else "-"

        agreed_amount = None
        currency = "EUR"

        if amount_line and amount_line != "-":
            parts = amount_line.replace(",", ".").split()
            try:
                agreed_amount = float(parts[0])
            except (IndexError, TypeError, ValueError) as exc:
                raise ContactChatError("Order amount must be a number or '-'.") from exc

            if agreed_amount < 0:
                raise ContactChatError("Order amount cannot be negative.")

            if len(parts) > 1:
                currency = parts[1].upper()

            if len(currency) != 3:
                raise ContactChatError("Order currency must use 3 letters, for example EUR.")

        return ServiceOrderFormData(
            description=description,
            schedule_text=schedule_text,
            agreed_amount=agreed_amount,
            currency=currency,
        )

    def _normalize_language(self, language: str | None) -> str:
        return language if language in {"ru", "en", "pt"} else "ru"

    def _validate_contact_message(self, message: str) -> str:
        normalized = (message or "").strip()

        if len(normalized) < 10:
            raise ContactChatError("Contact message must be at least 10 characters.")

        return normalized

    def _validate_thread_message(self, message: str) -> str:
        normalized = (message or "").strip()
        if not normalized:
            raise ContactChatError("Message cannot be empty.")
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
        profession_id: UUID | None = None,
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

        resolved_profession_id = (
            await self.repository.resolve_contact_profession_id(
                specialist_id=specialist.id,
                requested_profession_id=profession_id,
            )
        )

        if resolved_profession_id is None:
            raise ContactChatError(
                "Specialist profession is not available."
            )

        existing_contact_request = await self.repository.get_active_contact_request_for_pair(
            tenant_id=tenant_id,
            from_user_id=from_user_id,
            specialist_id=specialist.id,
            profession_id=resolved_profession_id,
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
                profession_id=resolved_profession_id,
                specialist_user_id=specialist.user_id,
                message=normalized_message,
                original_language=self._normalize_language(
                    original_language,
                ),
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

            status = "declined"
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
    ) -> ContactThreadCompletionRequestResult:
        try:
            thread, notification = (
                await self.repository.request_thread_completion(
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    actor_user_id=actor_user_id,
                )
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ContactThreadCompletionRequestResult(
            thread_id=thread.id,
            contact_request_id=thread.context_id,
            notification_id=notification.id,
            requested_for_user_id=notification.user_id,
        )

    async def list_overdue_completion_requests(
        self,
        *,
        now: datetime | None = None,
        delay_days: int = 7,
        limit: int = 100,
    ) -> list[OverdueCompletionRequest]:
        normalized_delay_days = max(1, int(delay_days))
        current_time = now or datetime.utcnow()
        deadline = current_time - timedelta(
            days=normalized_delay_days,
        )

        candidates = (
            await self.repository
            .list_completion_escalation_candidates(
                limit=limit,
            )
        )

        overdue: list[OverdueCompletionRequest] = []

        for contact_request in candidates:
            metadata = dict(
                contact_request.extra_metadata or {}
            )
            requested_at_raw = metadata.get(
                "completion_requested_at"
            )

            if not requested_at_raw:
                continue

            try:
                requested_at = datetime.fromisoformat(
                    str(requested_at_raw)
                )
            except (TypeError, ValueError):
                continue

            if requested_at > deadline:
                continue

            overdue.append(
                OverdueCompletionRequest(
                    contact_request_id=contact_request.id,
                    tenant_id=contact_request.tenant_id,
                    client_user_id=contact_request.from_user_id,
                    specialist_id=contact_request.specialist_id,
                    requested_at=requested_at,
                )
            )

        return overdue

    async def process_overdue_completion_escalations(
        self,
        *,
        now: datetime | None = None,
        delay_days: int = 7,
        limit: int = 100,
    ) -> CompletionEscalationResult:
        current_time = now or datetime.utcnow()
        normalized_delay_days = max(1, int(delay_days))
        deadline = current_time - timedelta(
            days=normalized_delay_days,
        )

        overdue = await self.list_overdue_completion_requests(
            now=current_time,
            delay_days=normalized_delay_days,
            limit=limit,
        )

        support_repository = SupportRepository(
            self.repository.session
        )

        ticket_ids: list[UUID] = []
        skipped_count = 0

        try:
            for item in overdue:
                contact_request = (
                    await self.repository
                    .get_completion_escalation_candidate_for_update(
                        contact_request_id=(
                            item.contact_request_id
                        ),
                    )
                )

                if contact_request is None:
                    skipped_count += 1
                    continue

                metadata = dict(
                    contact_request.extra_metadata or {}
                )

                if metadata.get(
                    "completion_escalated_ticket_id"
                ):
                    skipped_count += 1
                    continue

                requested_at_raw = metadata.get(
                    "completion_requested_at"
                )
                if not requested_at_raw:
                    skipped_count += 1
                    continue

                try:
                    requested_at = datetime.fromisoformat(
                        str(requested_at_raw)
                    )
                except (TypeError, ValueError):
                    skipped_count += 1
                    continue

                if requested_at > deadline:
                    skipped_count += 1
                    continue

                ticket = (
                    await support_repository
                    .create_system_ticket(
                        tenant_id=contact_request.tenant_id,
                        user_id=contact_request.from_user_id,
                        subject=(
                            "Contact request completion overdue"
                        ),
                        priority="P1",
                        category="request",
                        message_text=(
                            "Completion confirmation was not "
                            "received within 7 days. "
                            "The request was escalated "
                            "automatically."
                        ),
                    )
                )

                await self.repository.mark_completion_escalated(
                    contact_request=contact_request,
                    support_ticket_id=ticket.id,
                    escalated_at=current_time,
                )

                ticket_ids.append(ticket.id)

            await self.repository.session.commit()

        except Exception as exc:
            await self.repository.session.rollback()
            raise ContactChatError(
                "Failed to escalate overdue completion requests."
            ) from exc

        return CompletionEscalationResult(
            processed_count=len(ticket_ids),
            skipped_count=skipped_count,
            support_ticket_ids=tuple(ticket_ids),
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

    async def cancel_contact_request_by_admin(
        self,
        *,
        contact_request_id: UUID,
        admin_user_id: UUID,
        tenant_id: UUID,
        reason: str,
    ) -> ContactRequestStatusResult:
        normalized_reason = (reason or "").strip()

        if len(normalized_reason) < 3:
            raise ContactChatError(
                "Cancellation reason is required."
            )

        if len(normalized_reason) > 500:
            raise ContactChatError(
                "Cancellation reason is too long."
            )

        try:
            contact_request, thread = (
                await self.repository
                .cancel_contact_request_by_admin(
                    contact_request_id=contact_request_id,
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    reason=normalized_reason,
                )
            )

            await self.repository.session.commit()

        except ValueError as exc:
            await self.repository.session.rollback()
            raise ContactChatError(str(exc)) from exc
        except Exception as exc:
            await self.repository.session.rollback()
            raise ContactChatError(
                "Failed to cancel contact request."
            ) from exc

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

    async def list_user_service_orders(
        self,
        *,
        user_id: UUID,
        language: str = "ru",
        limit: int = 10,
        offset: int = 0,
    ) -> list[ServiceOrderListItem]:
        rows = await self.repository.list_service_orders_for_user(
            user_id=user_id,
            language=language,
            limit=limit,
            offset=offset,
        )

        items: list[ServiceOrderListItem] = []

        for order, specialist_name, profession_name, client_name in rows:
            metadata = order.extra_metadata or {}
            items.append(
                ServiceOrderListItem(
                    order_id=order.id,
                    thread_id=order.thread_id,
                    contact_request_id=order.contact_request_id,
                    specialist_name=specialist_name,
                    client_name=client_name,
                    profession_name=profession_name,
                    status=order.status,
                    description=order.description,
                    schedule_text=metadata.get("schedule_text"),
                    agreed_amount=float(order.agreed_amount)
                    if order.agreed_amount is not None
                    else None,
                    currency=order.currency,
                    created_at=order.created_at,
                    is_client=order.client_user_id == user_id,
                )
            )

        return items

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

        (
            thread,
            contact_request,
            specialist_name,
            client_name,
            profession_name,
            active_order_id,
            active_order_status,
            active_order_created_by,
            messages,
        ) = row

        return ContactThreadDetail(
            thread_id=thread.id,
            contact_request_id=contact_request.id if contact_request else None,
            specialist_name=specialist_name,
            client_name=client_name or "Client",
            profession_name=profession_name,
            request_text=contact_request.message if contact_request else None,
            request_status=contact_request.status if contact_request else None,
            thread_status=thread.status,
            active_order_id=active_order_id,
            active_order_status=active_order_status,
            active_order_created_by=active_order_created_by,
            messages=[
                (
                    f"Клиент: {message.original_text}"
                    if message.sender_user_id == thread.client_user_id
                    else f"Специалист: {message.original_text}"
                )
                for message in messages
            ],
        )

    async def send_thread_message(
        self,
        *,
        thread_id: UUID,
        sender_user_id: UUID,
        text: str,
        original_language: str | None = None,
    ) -> ContactThreadMessageResult:
        normalized_text = self._validate_thread_message(text)

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

    async def create_service_order_draft_from_thread(
        self,
        *,
        thread_id: UUID,
        actor_user_id: UUID,
        tenant_id: UUID,
        description: str | None = None,
        schedule_text: str | None = None,
        agreed_amount: float | None = None,
        currency: str = "EUR",
    ) -> ServiceOrderDraftResult:
        try:
            order = await self.repository.create_service_order_draft_from_thread(
                thread_id=thread_id,
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
                description=description,
                schedule_text=schedule_text,
                agreed_amount=agreed_amount,
                currency=currency,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ServiceOrderDraftResult(
            order_id=order.id,
            thread_id=order.thread_id,
            contact_request_id=order.contact_request_id,
            status=order.status,
        )

    async def confirm_service_order(
        self,
        *,
        order_id: UUID,
        actor_user_id: UUID,
        tenant_id: UUID,
    ) -> ServiceOrderStatusResult:
        try:
            order = await self.repository.confirm_service_order(
                order_id=order_id,
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ServiceOrderStatusResult(
            order_id=order.id,
            thread_id=order.thread_id,
            contact_request_id=order.contact_request_id,
            status=order.status,
        )

    async def complete_service_order(
        self,
        *,
        order_id: UUID,
        actor_user_id: UUID,
        tenant_id: UUID,
    ) -> ServiceOrderStatusResult:
        try:
            order = await self.repository.complete_service_order(
                order_id=order_id,
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
            )
        except ValueError as exc:
            raise ContactChatError(str(exc)) from exc

        return ServiceOrderStatusResult(
            order_id=order.id,
            thread_id=order.thread_id,
            contact_request_id=order.contact_request_id,
            status=order.status,
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