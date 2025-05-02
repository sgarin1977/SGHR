# bot.py

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from handlers.specialists import router as specialists_router
from handlers.start import router as start_router
from handlers.switch_profile import router as switch_profile_router
from handlers.role_selection import router as role_selection_router
from handlers.find_job import router as find_job_router
from handlers.vacancy_form import router as vacancy_form_router
from handlers.view_vacancies import router as view_vacancies_router  # ← добавлено

from fsm.employer_form import router as employer_form_router  # FSM для работодателя
from fsm.seeker_form import router as seeker_form_router      # FSM для соискателя

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # Порядок регистрации важен: FSM-обработчики последними
    dp.include_router(start_router)
    dp.include_router(specialists_router)
    dp.include_router(switch_profile_router)
    dp.include_router(role_selection_router)
    dp.include_router(find_job_router)
    dp.include_router(vacancy_form_router)
    dp.include_router(view_vacancies_router)  # ← обязательно подключили
    dp.include_router(employer_form_router)
    dp.include_router(seeker_form_router)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

