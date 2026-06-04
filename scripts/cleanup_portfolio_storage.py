import asyncio
import logging

from database.repositories.portfolio import PortfolioRepository
from database.session import async_session
from services.portfolio import PortfolioService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)


async def main() -> None:
    async with async_session() as session:
        service = PortfolioService(
            PortfolioRepository(session)
        )

        cleaned_count = await service.cleanup_due_items(
            limit=500,
        )

    logger.info(
        "portfolio_storage_cleanup_completed cleaned_count=%s",
        cleaned_count,
    )


if __name__ == "__main__":
    asyncio.run(main())