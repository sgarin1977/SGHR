from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    and_,
    delete,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    WebhookEvent,
    WebhookDelivery,
    ApiClient,
    WebhookEndpoint,
    WebhookSubscription,
)


class WebhookRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def create_endpoint(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
        callback_url: str,
        secret_ciphertext: bytes,
    ) -> WebhookEndpoint:
        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            api_client_id=api_client_id,
            callback_url=callback_url,
            secret_ciphertext=(
                secret_ciphertext
            ),
            status="active",
            consecutive_failures=0,
        )

        self.session.add(endpoint)
        await self.session.flush()

        return endpoint

    async def create_subscriptions(
        self,
        *,
        tenant_id: UUID,
        webhook_endpoint_id: UUID,
        event_types: tuple[str, ...],
    ) -> tuple[WebhookSubscription, ...]:
        subscriptions = tuple(
            WebhookSubscription(
                tenant_id=tenant_id,
                webhook_endpoint_id=(
                    webhook_endpoint_id
                ),
                event_type=event_type,
            )
            for event_type in event_types
        )

        self.session.add_all(subscriptions)
        await self.session.flush()

        return subscriptions

    async def get_active_api_client(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
    ) -> ApiClient | None:
        result = await self.session.execute(
            select(ApiClient).where(
                ApiClient.tenant_id == tenant_id,
                ApiClient.id == api_client_id,
                ApiClient.status == "active",
            )
        )

        return result.scalar_one_or_none()

    async def list_endpoints(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[WebhookEndpoint]:
        statement = select(
            WebhookEndpoint
        ).where(
            WebhookEndpoint.tenant_id
            == tenant_id,
        )

        if api_client_id is not None:
            statement = statement.where(
                WebhookEndpoint.api_client_id
                == api_client_id,
            )

        statement = (
            statement
            .order_by(
                WebhookEndpoint.created_at.desc(),
                WebhookEndpoint.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(
            statement
        )

        return list(result.scalars().all())

    async def list_subscriptions_for_endpoints(
        self,
        *,
        tenant_id: UUID,
        endpoint_ids: tuple[UUID, ...],
    ) -> list[WebhookSubscription]:
        if not endpoint_ids:
            return []

        result = await self.session.execute(
            select(WebhookSubscription)
            .where(
                WebhookSubscription.tenant_id
                == tenant_id,
                WebhookSubscription
                .webhook_endpoint_id
                .in_(endpoint_ids),
            )
            .order_by(
                WebhookSubscription
                .webhook_endpoint_id,
                WebhookSubscription.event_type,
            )
        )

        return list(result.scalars().all())

    async def get_endpoint(
        self,
        *,
        tenant_id: UUID,
        endpoint_id: UUID,
        api_client_id: UUID | None,
    ) -> WebhookEndpoint | None:
        statement = select(
            WebhookEndpoint
        ).where(
            WebhookEndpoint.tenant_id
            == tenant_id,
            WebhookEndpoint.id
            == endpoint_id,
        )

        if api_client_id is not None:
            statement = statement.where(
                WebhookEndpoint.api_client_id
                == api_client_id,
            )

        result = await self.session.execute(
            statement
        )

        return result.scalar_one_or_none()

    async def get_endpoint_for_update(
        self,
        *,
        tenant_id: UUID,
        endpoint_id: UUID,
        api_client_id: UUID | None,
    ) -> WebhookEndpoint | None:
        statement = select(
            WebhookEndpoint
        ).where(
            WebhookEndpoint.tenant_id
            == tenant_id,
            WebhookEndpoint.id
            == endpoint_id,
        )

        if api_client_id is not None:
            statement = statement.where(
                WebhookEndpoint.api_client_id
                == api_client_id,
            )

        result = await self.session.execute(
            statement.with_for_update()
        )

        return result.scalar_one_or_none()

    async def update_endpoint(
        self,
        *,
        endpoint: WebhookEndpoint,
        callback_url: str,
        status: str,
    ) -> WebhookEndpoint:
        endpoint.callback_url = callback_url
        endpoint.status = status

        await self.session.flush()
        return endpoint

    async def replace_subscriptions(
        self,
        *,
        tenant_id: UUID,
        webhook_endpoint_id: UUID,
        event_types: tuple[str, ...],
    ) -> tuple[WebhookSubscription, ...]:
        await self.session.execute(
            delete(WebhookSubscription).where(
                WebhookSubscription.tenant_id
                == tenant_id,
                WebhookSubscription
                .webhook_endpoint_id
                == webhook_endpoint_id,
            )
        )

        subscriptions = tuple(
            WebhookSubscription(
                tenant_id=tenant_id,
                webhook_endpoint_id=(
                    webhook_endpoint_id
                ),
                event_type=event_type,
            )
            for event_type in event_types
        )

        self.session.add_all(subscriptions)
        await self.session.flush()

        return subscriptions

    async def list_deliveries(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[WebhookDelivery]:
        statement = (
            select(WebhookDelivery)
            .join(
                WebhookEndpoint,
                and_(
                    WebhookEndpoint.tenant_id
                    == WebhookDelivery.tenant_id,
                    WebhookEndpoint.id
                    == WebhookDelivery
                    .webhook_endpoint_id,
                ),
            )
            .where(
                WebhookDelivery.tenant_id
                == tenant_id,
            )
        )

        if api_client_id is not None:
            statement = statement.where(
                WebhookEndpoint.api_client_id
                == api_client_id,
            )

        statement = (
            statement
            .order_by(
                WebhookDelivery.created_at.desc(),
                WebhookDelivery.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(
            statement
        )

        return list(result.scalars().all())

    async def get_delivery_for_update(
        self,
        *,
        tenant_id: UUID,
        delivery_id: UUID,
        api_client_id: UUID | None,
    ) -> WebhookDelivery | None:
        statement = (
            select(WebhookDelivery)
            .join(
                WebhookEndpoint,
                and_(
                    WebhookEndpoint.tenant_id
                    == WebhookDelivery.tenant_id,
                    WebhookEndpoint.id
                    == WebhookDelivery
                    .webhook_endpoint_id,
                ),
            )
            .where(
                WebhookDelivery.tenant_id
                == tenant_id,
                WebhookDelivery.id
                == delivery_id,
            )
        )

        if api_client_id is not None:
            statement = statement.where(
                WebhookEndpoint.api_client_id
                == api_client_id,
            )

        result = await self.session.execute(
            statement.with_for_update()
        )

        return result.scalar_one_or_none()

    async def requeue_delivery(
        self,
        *,
        delivery: WebhookDelivery,
        queued_at: datetime,
    ) -> WebhookDelivery:
        delivery.status = "pending"
        delivery.attempt_count = 0
        delivery.next_attempt_at = queued_at
        delivery.response_status = None
        delivery.error_category = None
        delivery.delivered_at = None

        await self.session.flush()
        return delivery

    async def create_event(
        self,
        *,
        tenant_id: UUID,
        event_type: str,
        payload: dict,
    ) -> WebhookEvent:
        event = WebhookEvent(
            tenant_id=tenant_id,
            event_type=event_type,
            payload=dict(payload),
        )

        self.session.add(event)
        await self.session.flush()

        return event

    async def list_active_subscribed_endpoints(
        self,
        *,
        tenant_id: UUID,
        event_type: str,
    ) -> list[WebhookEndpoint]:
        result = await self.session.execute(
            select(WebhookEndpoint)
            .join(
                WebhookSubscription,
                and_(
                    WebhookSubscription.tenant_id
                    == WebhookEndpoint.tenant_id,
                    WebhookSubscription
                    .webhook_endpoint_id
                    == WebhookEndpoint.id,
                ),
            )
            .where(
                WebhookEndpoint.tenant_id
                == tenant_id,
                WebhookEndpoint.status
                == "active",
                WebhookSubscription.event_type
                == event_type,
            )
            .order_by(
                WebhookEndpoint.id,
            )
        )

        return list(result.scalars().all())

    async def create_deliveries(
        self,
        *,
        tenant_id: UUID,
        webhook_event_id: UUID,
        webhook_endpoint_ids: tuple[UUID, ...],
        queued_at: datetime,
    ) -> tuple[WebhookDelivery, ...]:
        deliveries = tuple(
            WebhookDelivery(
                tenant_id=tenant_id,
                webhook_event_id=(
                    webhook_event_id
                ),
                webhook_endpoint_id=(
                    webhook_endpoint_id
                ),
                status="pending",
                attempt_count=0,
                next_attempt_at=queued_at,
                response_status=None,
                error_category=None,
                delivered_at=None,
            )
            for webhook_endpoint_id
            in webhook_endpoint_ids
        )

        self.session.add_all(deliveries)
        await self.session.flush()

        return deliveries

    async def mark_delivery_succeeded(
        self,
        *,
        delivery: WebhookDelivery,
        endpoint: WebhookEndpoint,
        response_status: int,
        delivered_at: datetime,
    ) -> WebhookDelivery:
        delivery.status = "delivered"
        delivery.attempt_count = (
            int(delivery.attempt_count) + 1
        )
        delivery.next_attempt_at = None
        delivery.response_status = int(
            response_status
        )
        delivery.error_category = None
        delivery.delivered_at = delivered_at

        endpoint.consecutive_failures = 0

        await self.session.flush()
        return delivery

    async def mark_delivery_failed(
        self,
        *,
        delivery: WebhookDelivery,
        endpoint: WebhookEndpoint,
        response_status: int | None,
        error_category: str,
        retry_at: datetime | None,
    ) -> WebhookDelivery:
        delivery.status = "failed"
        delivery.attempt_count = (
            int(delivery.attempt_count) + 1
        )
        delivery.next_attempt_at = retry_at
        delivery.response_status = (
            int(response_status)
            if response_status is not None
            else None
        )
        delivery.error_category = str(
            error_category
        )
        delivery.delivered_at = None

        endpoint.consecutive_failures = (
            int(endpoint.consecutive_failures)
            + 1
        )
        if (
            endpoint.consecutive_failures
            >= 20
        ):
            endpoint.status = "suspended"

        await self.session.flush()
        return delivery

    async def get_due_delivery_context_for_update(
        self,
        *,
        now: datetime,
    ):
        statement = (
            select(
                WebhookDelivery,
                WebhookEvent,
                WebhookEndpoint,
            )
            .join(
                WebhookEvent,
                and_(
                    WebhookEvent.tenant_id
                    == WebhookDelivery.tenant_id,
                    WebhookEvent.id
                    == WebhookDelivery
                    .webhook_event_id,
                ),
            )
            .join(
                WebhookEndpoint,
                and_(
                    WebhookEndpoint.tenant_id
                    == WebhookDelivery.tenant_id,
                    WebhookEndpoint.id
                    == WebhookDelivery
                    .webhook_endpoint_id,
                ),
            )
            .where(
                WebhookEndpoint.status
                == "active",
                WebhookDelivery.status.in_(
                    (
                        "pending",
                        "failed",
                    )
                ),
                WebhookDelivery.next_attempt_at
                .is_not(None),
                WebhookDelivery.next_attempt_at
                <= now,
            )
            .order_by(
                WebhookDelivery.next_attempt_at,
                WebhookDelivery.created_at,
                WebhookDelivery.id,
            )
            .limit(1)
            .with_for_update(
                of=(
                    WebhookDelivery,
                    WebhookEndpoint,
                ),
                skip_locked=True,
            )
        )

        result = await self.session.execute(
            statement
        )
        return result.one_or_none()

    async def delete_expired_events(
        self,
        *,
        cutoff: datetime,
    ) -> int:
        result = await self.session.execute(
            delete(WebhookEvent).where(
                WebhookEvent.expires_at
                <= cutoff,
            )
        )

        return int(result.rowcount or 0)

