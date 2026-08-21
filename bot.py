import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import ADMIN_TELEGRAM_IDS, BOT_TOKEN, ENVIRONMENT, LOG_LEVEL
from logger import configure_logging
from handlers.start import start_router
from handlers.legal import legal_router
from fsm.specialist_form import specialist_form_router
from handlers.search import search_router
from handlers.settings import settings_router
from handlers.admin_support import (
    admin_support_router,
)
from handlers.admin_dictionaries import (
    admin_dictionaries_router,
)
from handlers.admin_portfolio import (
    admin_portfolio_router,
)
from handlers.admin_reviews import (
    admin_reviews_router,
)
from handlers.admin_dialogs import (
    admin_dialogs_router,
)
from handlers.admin_governance import (
    admin_governance_router,
)
from handlers.admin_impersonation import (
    admin_impersonation_router,
)
from handlers.admin import admin_router
from handlers.admin_audit import (
    admin_audit_router,
)
from handlers.super_admin_global_blacklist import (
    super_admin_global_blacklist_router,
)
from handlers.admin_scoped_blacklist import (
    admin_scoped_blacklist_router,
)
from handlers.admin_complaints import (
    admin_complaints_router,
)
from handlers.admin_specialists import (
    admin_specialists_router,
)
from handlers.admin_users import (
    admin_users_router,
)
from handlers.admin_finance import (
    admin_finance_router,
)
from handlers.specialist_billing import (
    specialist_billing_router,
)
from handlers.specialist_services import (
    specialist_services_router,
)
from handlers.specialist_cabinets import specialist_cabinets_router
from handlers.user_dialogs import user_dialogs_router
from handlers.billing import billing_router
from handlers.user_favorites import user_favorites_router
from handlers.specialist_settings import specialist_settings_router
from handlers.specialist_portfolio import specialist_portfolio_router
from handlers.support import support_router

configure_logging(LOG_LEVEL)
logger = logging.getLogger(__name__)

async def send_fatal_admin_alert(error: BaseException) -> None:
    if not ADMIN_TELEGRAM_IDS:
        logger.warning("bot_fatal_alert_skipped reason=no_admin_telegram_ids")
        return

    alert_bot = Bot(token=BOT_TOKEN)

    try:
        text = (
            "SGHR bot fatal error\n"
            f"environment: {ENVIRONMENT}\n"
            f"error_type: {type(error).__name__}\n"
            f"error: {str(error)[:1000]}"
        )

        for admin_chat_id in ADMIN_TELEGRAM_IDS:
            try:
                await alert_bot.send_message(
                    chat_id=admin_chat_id,
                    text=text,
                )
            except Exception:
                logger.exception(
                    "bot_fatal_alert_send_failed admin_chat_id=%s",
                    admin_chat_id,
                )
    finally:
        await alert_bot.session.close()

async def main():
    logger.info("bot_starting")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(start_router)
    dp.include_router(legal_router)
    dp.include_router(specialist_form_router)
    dp.include_router(search_router)
    dp.include_router(settings_router)
    dp.include_router(support_router)
    dp.include_router(
        admin_finance_router
    )
    dp.include_router(
        admin_audit_router
    )
    dp.include_router(
        super_admin_global_blacklist_router
    )
    dp.include_router(
        admin_scoped_blacklist_router
    )
    dp.include_router(
        admin_complaints_router
    )
    dp.include_router(
        admin_specialists_router
    )
    dp.include_router(
        admin_users_router
    )
    dp.include_router(
        admin_support_router
    )
    dp.include_router(
        admin_dictionaries_router
    )
    dp.include_router(admin_portfolio_router)
    dp.include_router(admin_reviews_router)
    dp.include_router(admin_dialogs_router)
    dp.include_router(admin_governance_router)
    dp.include_router(admin_impersonation_router)
    dp.include_router(admin_router)
    dp.include_router(
        specialist_billing_router
    )
    dp.include_router(
        specialist_services_router
    )
    dp.include_router(specialist_portfolio_router)
    dp.include_router(specialist_settings_router)
    dp.include_router(user_favorites_router)
    dp.include_router(specialist_cabinets_router)
    dp.include_router(user_dialogs_router)
    dp.include_router(billing_router)

    logger.info("bot_routers_registered")

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("bot_polling_start")

    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        logger.exception("bot_fatal_error")
        try:
            asyncio.run(send_fatal_admin_alert(exc))
        except Exception:
            logger.exception("bot_fatal_alert_failed")
        raise
