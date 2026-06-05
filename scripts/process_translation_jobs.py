import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parent.parent))

from database.repositories.translation import TranslationRepository
from database.session import async_session
from services.translation import TranslationService


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process pending/retry SGHR translation jobs."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("TRANSLATION_WORKER_LIMIT", "20")),
        help="Maximum jobs to process in one run.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    limit = max(1, min(int(args.limit), 100))

    async with async_session() as session:
        service = TranslationService(
            TranslationRepository(session),
        )

        results = await service.process_pending_jobs(limit=limit)

    statuses: dict[str, int] = {}
    for result in results:
        statuses[result.translation_status] = (
            statuses.get(result.translation_status, 0) + 1
        )

    logger.info(
        "translation_jobs_processed count=%s statuses=%s limit=%s",
        len(results),
        statuses,
        limit,
    )


if __name__ == "__main__":
    asyncio.run(main())
