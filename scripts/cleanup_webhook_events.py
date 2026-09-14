import asyncio

from database.repositories.webhooks import (
    WebhookRepository,
)
from database.session import async_session
from services.webhooks import (
    WebhookRetentionService,
)


async def main() -> None:
    async with async_session() as session:
        service = WebhookRetentionService(
            session=session,
            repository=WebhookRepository(
                session
            ),
        )
        deleted = await (
            service.purge_expired_events()
        )

    print(
        "Webhook retention completed: "
        f"{deleted} events deleted."
    )


if __name__ == "__main__":
    asyncio.run(main())
