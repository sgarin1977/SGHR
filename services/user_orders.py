from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.settings import ApiIdempotencySettings

from database.repositories.contact import (
    ContactChatRepository,
)
from database.repositories.webhooks import (
    WebhookRepository,
)
from services.contact_chat import (
    ContactChatService,
    ServiceOrderDraftResult,
    ServiceOrderListItem,
    ServiceOrderStatusResult,
)
from services.api_idempotency import (
    ApiIdempotencyService,
    build_api_idempotency_service,
)
from services.webhooks import (
    WebhookEventPublisher,
)


@dataclass(frozen=True)
class UserOrdersPage:
    items: list[ServiceOrderListItem]
    page: int
    has_next: bool


class UserOrdersService:
    def __init__(
        self,
        *,
        chats: ContactChatService,
        idempotency: ApiIdempotencyService
        | None = None,
    ):
        self.chats = chats
        self.idempotency = idempotency

    async def create_order_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        thread_id: UUID,
        description: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        agreed_amount: float | None,
        currency: str = "EUR",
        schedule_text: str | None = None,
        idempotency_key: str | None = None,
    ) -> ServiceOrderDraftResult:
        reservation = None
        if (
            idempotency_key is not None
            and self.idempotency is not None
        ):
            reservation = await (
                self.idempotency.reserve(
                    tenant_id=tenant_id,
                    principal_type="user",
                    principal_id=user_id,
                    operation="service_order.create",
                    idempotency_key=idempotency_key,
                    payload={
                        "dialog_id": str(thread_id),
                        "description": description,
                        "start_at": (
                            start_at.isoformat()
                            if start_at is not None
                            else None
                        ),
                        "end_at": (
                            end_at.isoformat()
                            if end_at is not None
                            else None
                        ),
                        "agreed_amount": agreed_amount,
                        "currency": currency,
                    },
                )
            )

            if reservation.is_replay:
                stored = reservation.response_payload
                return ServiceOrderDraftResult(
                    order_id=UUID(
                        stored["order_id"]
                    ),
                    thread_id=UUID(
                        stored["thread_id"]
                    ),
                    contact_request_id=UUID(
                        stored["contact_request_id"]
                    ),
                    status=stored["status"],
                )

        create_kwargs = {
            "tenant_id": tenant_id,
            "actor_user_id": user_id,
            "thread_id": thread_id,
            "description": description,
            "schedule_text": schedule_text,
            "start_at": start_at,
            "end_at": end_at,
            "agreed_amount": agreed_amount,
            "currency": currency,
            "platform": "api",
        }
        if reservation is not None:
            create_kwargs["commit"] = False

        result = await (
            self.chats
            .create_service_order_draft_from_thread(
                **create_kwargs
            )
        )

        if reservation is not None:
            await self.idempotency.complete(
                reservation=reservation,
                response_status=201,
                response_payload={
                    "order_id": str(result.order_id),
                    "thread_id": str(result.thread_id),
                    "contact_request_id": str(
                        result.contact_request_id
                    ),
                    "status": result.status,
                },
            )
            await self.idempotency.commit()

        return result


    async def update_order_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        order_id: UUID,
        description: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        agreed_amount: float | None,
        currency: str,
    ) -> ServiceOrderStatusResult:
        return await (
            self.chats
            .update_service_order_draft(
                tenant_id=tenant_id,
                actor_user_id=user_id,
                order_id=order_id,
                description=description,
                start_at=start_at,
                end_at=end_at,
                agreed_amount=agreed_amount,
                currency=currency,
                platform="api",
            )
        )

    async def confirm_order_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        order_id: UUID,
    ) -> ServiceOrderStatusResult:
        return await self.chats.confirm_service_order(
            tenant_id=tenant_id,
            actor_user_id=user_id,
            order_id=order_id,
            platform="api",
        )

    async def cancel_order_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        order_id: UUID,
    ) -> ServiceOrderStatusResult:
        return await self.chats.cancel_service_order(
            tenant_id=tenant_id,
            actor_user_id=user_id,
            order_id=order_id,
            platform="api",
        )

    async def complete_order_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        order_id: UUID,
    ) -> ServiceOrderStatusResult:
        return await self.chats.complete_service_order(
            tenant_id=tenant_id,
            actor_user_id=user_id,
            order_id=order_id,
            platform="api",
        )

    async def list_orders_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        language: str,
        page: int = 0,
        page_size: int = 20,
    ) -> UserOrdersPage:
        normalized_page = max(
            0,
            int(page),
        )
        normalized_size = max(
            1,
            min(int(page_size), 100),
        )

        items = await (
            self.chats.list_user_service_orders(
                tenant_id=tenant_id,
                user_id=user_id,
                language=language,
                limit=normalized_size + 1,
                offset=(
                    normalized_page
                    * normalized_size
                ),
            )
        )

        return UserOrdersPage(
            items=items[:normalized_size],
            page=normalized_page,
            has_next=(
                len(items) > normalized_size
            ),
        )



def build_user_orders_service(
    session: AsyncSession,
) -> UserOrdersService:
    settings = ApiIdempotencySettings.from_env()

    return UserOrdersService(
        chats=ContactChatService(
            ContactChatRepository(session),
            webhook_publisher=(
                WebhookEventPublisher(
                    repository=WebhookRepository(
                        session
                    ),
                )
            ),
        ),
        idempotency=(
            build_api_idempotency_service(
                session,
                encryption_key=(
                    settings.encryption_key
                ),
            )
        ),
    )
