import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


sys.path.append(
    str(Path(__file__).resolve().parent.parent)
)

from database.repositories.contact import ContactChatRepository
from database.session import async_session
from services.contact_chat import (
    ContactChatError,
    ContactChatService,
)


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format=(
        "%(asctime)s %(levelname)s "
        "[%(name)s] %(message)s"
    ),
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Escalate overdue SGHR contact request "
            "completion confirmations."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(
            os.getenv(
                "CONTACT_COMPLETION_ESCALATION_LIMIT",
                "100",
            )
        ),
        help="Maximum contact requests to process.",
    )
    parser.add_argument(
        "--delay-days",
        type=int,
        default=int(
            os.getenv(
                "CONTACT_COMPLETION_ESCALATION_DAYS",
                "7",
            )
        ),
        help="Days before completion escalation.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    limit = max(1, min(int(args.limit), 500))
    delay_days = max(1, int(args.delay_days))

    async with async_session() as session:
        service = ContactChatService(
            ContactChatRepository(session)
        )

        try:
            result = (
                await service
                .process_overdue_completion_escalations(
                    delay_days=delay_days,
                    limit=limit,
                )
            )
        except ContactChatError:
            logger.exception(
                "contact_completion_escalation_failed "
                "limit=%s delay_days=%s",
                limit,
                delay_days,
            )
            raise

    logger.info(
        "contact_completion_escalation_processed "
        "processed=%s skipped=%s ticket_ids=%s "
        "limit=%s delay_days=%s",
        result.processed_count,
        result.skipped_count,
        [
            str(ticket_id)
            for ticket_id in result.support_ticket_ids
        ],
        limit,
        delay_days,
    )


if __name__ == "__main__":
    asyncio.run(main())