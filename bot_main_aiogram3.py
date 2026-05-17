import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from handlers.start_handler import router as start_router
from handlers.register_handlers import router as seeker_router
from handlers.register_employer import router as employer_router
from fsm.seeker_form import router as seeker_form_router
from fsm.employer_form import router as employer_form_router

from aiogram.client.default import DefaultBotProperties


load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

async def main():
    logging.basicConfig(level=logging.INFO)

    bot = Botr(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(start_router)
    dp.include_router(seeker_router)
    dp.include_router(employer_router)
    dp.include_router(seeker_form_router)
    dp.include_router(employer_form_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
