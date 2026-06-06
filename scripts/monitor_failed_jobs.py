import argparse
import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy import text

from config import ADMIN_TELEGRAM_IDS, BOT_TOKEN, ENVIRONMENT
from database.session import async_session


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorResult:
    db_ok: bool
    translation_failed_count: int
    translation_retry_count: int
    deletion_failed_count: int
    data_export_failed_count: int

    @property
    def failed_jobs_count(self) -> int:
        return (
            self.translation_failed_count
            + self.translation_retry_count
            + self.deletion_failed_count
            + self.data_export_failed_count
        )


async def send_admin_alert(text_message: str) -> None:
    if not ADMIN_TELEGRAM_IDS:
        logger.warning("monitor_alert_skipped reason=no_admin_telegram_ids")
        return

    bot = Bot(token=BOT_TOKEN)

    try:
        for admin_chat_id in ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_chat_id,
                    text=text_message,
                )
            except Exception:
                logger.exception(
                    "monitor_alert_send_failed admin_chat_id=%s",
                    admin_chat_id,
                )
    finally:
        await bot.session.close()


async def collect_monitor_result() -> MonitorResult:
    async with async_session() as session:
        await session.execute(text("select 1"))

        translation_failed_count = (
            await session.execute(
                text("""
                    select count(*)
                    from translation_jobs
                    where status = 'failed'
                """)
            )
        ).scalar_one()

        translation_retry_count = (
            await session.execute(
                text("""
                    select count(*)
                    from translation_jobs
                    where status = 'retry'
                """)
            )
        ).scalar_one()

        deletion_failed_count = (
            await session.execute(
                text("""
                    select count(*)
                    from deletion_jobs
                    where status = 'failed'
                """)
            )
        ).scalar_one()

        data_export_failed_count = (
            await session.execute(
                text("""
                    select count(*)
                    from data_subject_requests
                    where request_type = 'export_data'
                      and status in ('rejected', 'cancelled')
                """)
            )
        ).scalar_one()

    return MonitorResult(
        db_ok=True,
        translation_failed_count=int(translation_failed_count),
        translation_retry_count=int(translation_retry_count),
        deletion_failed_count=int(deletion_failed_count),
        data_export_failed_count=int(data_export_failed_count),
    )


def build_alert_message(
    *,
    result: MonitorResult,
    failed_jobs_threshold: int,
    translation_fail_threshold: int,
) -> str | None:
    reasons = []

    if result.failed_jobs_count >= failed_jobs_threshold:
        reasons.append(
            f"failed_jobs_count={result.failed_jobs_count} >= {failed_jobs_threshold}"
        )

    translation_problem_count = (
        result.translation_failed_count + result.translation_retry_count
    )
    if translation_problem_count >= translation_fail_threshold:
        reasons.append(
            f"translation_problem_count={translation_problem_count} >= {translation_fail_threshold}"
        )

    if not reasons:
        return None

    return (
        "SGHR monitoring alert\n"
        f"environment: {ENVIRONMENT}\n"
        f"reason: {', '.join(reasons)}\n"
        f"translation_failed: {result.translation_failed_count}\n"
        f"translation_retry: {result.translation_retry_count}\n"
        f"deletion_failed: {result.deletion_failed_count}\n"
        f"data_export_failed: {result.data_export_failed_count}\n"
        f"failed_jobs_count: {result.failed_jobs_count}"
    )


async def run_monitor(
    *,
    failed_jobs_threshold: int,
    translation_fail_threshold: int,
    send_alert: bool,
) -> MonitorResult:
    try:
        result = await collect_monitor_result()
    except Exception as exc:
        logger.exception("monitor_db_check_failed")

        if send_alert:
            await send_admin_alert(
                "SGHR monitoring alert\n"
                f"environment: {ENVIRONMENT}\n"
                "reason: DB check failed\n"
                f"error_type: {type(exc).__name__}\n"
                f"error: {str(exc)[:1000]}"
            )

        raise

    alert_message = build_alert_message(
        result=result,
        failed_jobs_threshold=failed_jobs_threshold,
        translation_fail_threshold=translation_fail_threshold,
    )

    if alert_message and send_alert:
        await send_admin_alert(alert_message)

    logger.info(
        "monitor_failed_jobs_completed db_ok=%s translation_failed=%s translation_retry=%s deletion_failed=%s data_export_failed=%s failed_jobs_count=%s alert_sent=%s",
        result.db_ok,
        result.translation_failed_count,
        result.translation_retry_count,
        result.deletion_failed_count,
        result.data_export_failed_count,
        result.failed_jobs_count,
        bool(alert_message and send_alert),
    )

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor SGHR failed/retry jobs and send admin alerts.",
    )
    parser.add_argument(
        "--failed-jobs-threshold",
        type=int,
        default=5,
        help="Send alert when total failed/retry jobs reaches this threshold.",
    )
    parser.add_argument(
        "--translation-fail-threshold",
        type=int,
        default=3,
        help="Send alert when failed/retry translation jobs reaches this threshold.",
    )
    parser.add_argument(
        "--no-alert",
        action="store_true",
        help="Only log monitor result, do not send Telegram alerts.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    asyncio.run(
        run_monitor(
            failed_jobs_threshold=args.failed_jobs_threshold,
            translation_fail_threshold=args.translation_fail_threshold,
            send_alert=not args.no_alert,
        )
    )


if __name__ == "__main__":
    main()