import argparse
import asyncio
import logging
from pathlib import Path

from database.repositories.privacy import PrivacyRepository
from database.session import async_session
from services.privacy import PrivacyService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)


async def run_privacy_jobs(
    *,
    limit: int,
    export_dir: str,
    process_exports: bool,
    process_deletions: bool,
) -> None:
    async with async_session() as session:
        service = PrivacyService(PrivacyRepository(session))

        export_result = None
        deletion_result = None

        if process_exports:
            export_result = await service.process_requested_data_exports(
                export_dir=Path(export_dir),
                limit=limit,
            )

        if process_deletions:
            deletion_result = await service.process_scheduled_deletions(
                limit=limit,
            )

    logger.info(
        "privacy_jobs_processed exports=%s deletions=%s limit=%s export_dir=%s",
        export_result,
        deletion_result,
        limit,
        export_dir,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process SGHR privacy/DSR jobs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of jobs per type to process.",
    )
    parser.add_argument(
        "--export-dir",
        default="exports/data_subject_requests",
        help="Directory for generated DSR export JSON files.",
    )
    parser.add_argument(
        "--exports-only",
        action="store_true",
        help="Process only data export requests.",
    )
    parser.add_argument(
        "--deletions-only",
        action="store_true",
        help="Process only deletion/anonymization jobs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    process_exports = not args.deletions_only
    process_deletions = not args.exports_only

    asyncio.run(
        run_privacy_jobs(
            limit=args.limit,
            export_dir=args.export_dir,
            process_exports=process_exports,
            process_deletions=process_deletions,
        )
    )


if __name__ == "__main__":
    main()