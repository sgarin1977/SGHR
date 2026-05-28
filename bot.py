import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
from handlers.start import start_router
from handlers.legal import legal_router
from fsm.specialist_form import specialist_form_router
from handlers.search import search_router
from handlers.settings import settings_router
from handlers.admin import admin_router
from handlers.billing import billing_router
logging.basicConfig(level=logging.INFO)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(start_router)
    dp.include_router(legal_router)
    dp.include_router(specialist_form_router)
    dp.include_router(search_router)
    dp.include_router(settings_router)
    dp.include_router(admin_router)
    dp.include_router(billing_router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
