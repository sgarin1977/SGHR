from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from dotenv import load_dotenv
import os
import logging

from handlers.start_handler import router as start_router
from handlers.register_handlers import router as seeker_router
from handlers.register_employer import router as employer_router
from fsm.seeker_form import router as seeker_form_router
from fsm.employer_form import router as employer_form_router

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Регистрация роутеров
dp.include_router(start_router)
dp.include_router(seeker_router)
dp.include_router(employer_router)
dp.include_router(seeker_form_router)
dp.include_router(employer_form_router)

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)