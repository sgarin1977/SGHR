import asyncio

from api.settings import ApiWebhookSettings
from database.repositories.webhooks import (
    WebhookRepository,
)
from database.session import async_session
from services.webhooks import (
    WebhookDeliveryWorker,
    build_webhook_delivery_sender,
)


IDLE_POLL_SECONDS = 1


async def main() -> None:
    settings = ApiWebhookSettings.from_env()
    sender = build_webhook_delivery_sender(
        secret_encryption_key=(
            settings.secret_encryption_key
        ),
        environment=settings.environment,
    )

    while True:
        async with async_session() as session:
            worker = WebhookDeliveryWorker(
                session=session,
                repository=WebhookRepository(
                    session
                ),
                sender=sender,
            )
            processed = await worker.run_once()

        if not processed:
            await asyncio.sleep(
                IDLE_POLL_SECONDS
            )


if __name__ == "__main__":
    asyncio.run(main())
