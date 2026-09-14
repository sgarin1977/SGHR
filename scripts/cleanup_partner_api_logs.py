import asyncio

from database.repositories.partner_api import (
    PartnerApiRepository,
)
from database.session import async_session
from services.partner_api_logging import (
    PartnerApiLogRetentionService,
)


async def main() -> None:
    async with async_session() as session:
        service = PartnerApiLogRetentionService(
            session=session,
            repository=PartnerApiRepository(
                session
            ),
        )
        deleted = await service.purge_expired_logs()

    print(
        "Partner API log retention completed: "
        f"{deleted} rows deleted."
    )


if __name__ == "__main__":
    asyncio.run(main())
