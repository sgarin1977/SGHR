from logger import log

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from pathlib import Path

from handlers.specialists.specialists_filter_city import router as city_filter_router
from handlers.specialists.specialists_filter_profession import router as prof_filter_router
from handlers.specialists.specialists_find_nearby import router as nearby_router

from handlers.start import router as start_router
from handlers.switch_profile import router as switch_profile_router
from handlers.role_selection import router as role_selection_router
from handlers.find_job import router as find_job_router
from handlers.vacancy_form import router as vacancy_form_router
from handlers.view_vacancies import router as view_vacancies_router
from handlers.link_specialist import router as link_specialist_router
from handlers import go_back

from fsm.employer_form import router as employer_form_router
from fsm.seeker_form import router as seeker_form_router
#from fsm.specialist_form import router as specialist_form_router
from aiogram import Router, F
from aiogram.types import Message

from handlers.specialists.search_filters import router as search_router
from handlers.specialists.contact_specialist import router as contact_router
from handlers.specialists.contact_reply import router as reply_router
from handlers.specialists.my_orders import router as orders_router
from utils.translate_utils import tr
from fsm.specialist_form import specialist_form_router


# router = Router()

# @router.message(F.text)
# async def handle_any_message(message: Message):
#     await message.answer("✅ Бот работает. Вы написали: " + message.text)

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    log.info("🤖 Бот успешно запущен.")

    # Порядок регистрации
    # FSM в конце
    dp.include_router(employer_form_router)
    dp.include_router(seeker_form_router)
    dp.include_router(specialist_form_router)
    
    dp.include_router(search_router)
    dp.include_router(contact_router)
    dp.include_router(reply_router)
    dp.include_router(orders_router)

    dp.include_router(start_router)
    dp.include_router(city_filter_router)
    dp.include_router(prof_filter_router)
    dp.include_router(nearby_router)
    dp.include_router(switch_profile_router)
    dp.include_router(role_selection_router)
    dp.include_router(find_job_router)
    dp.include_router(vacancy_form_router)
    dp.include_router(view_vacancies_router)
    dp.include_router(link_specialist_router)
    dp.include_router(go_back.router)



    # dp.include_router(router)  # fallback, отключён для FSM работы

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

