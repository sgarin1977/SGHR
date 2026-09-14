import asyncio
import logging

from database.repositories.files import (
    FileRepository,
)
from database.repositories.portfolio import PortfolioRepository
from database.session import async_session
from services.files import (
    FileOrphanCleanupService,
)
from services.portfolio import PortfolioService

from services.portfolio_storage import (
    SupabaseFileStorage,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)


async def main() -> None:
    async with async_session() as session:
        storage = SupabaseFileStorage()

        service = PortfolioService(
            PortfolioRepository(session),
            storage=storage,
        )
        orphan_cleanup = (
            FileOrphanCleanupService(
                repository=FileRepository(
                    session
                ),
                storage=storage,
            )
        )

        cleaned_count = await service.cleanup_due_items(
            limit=500,
        )
        orphan_deleted_count = await (
            orphan_cleanup.cleanup(
                limit=500,
            )
        )

    logger.info(
        "storage_cleanup_completed "
        "portfolio_cleaned_count=%s "
        "orphan_deleted_count=%s",
        cleaned_count,
        orphan_deleted_count,
    )


if __name__ == "__main__":
    asyncio.run(main())