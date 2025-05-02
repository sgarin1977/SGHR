# handlers/start.py

from aiogram import Router, types
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from ui.buttons.menu import start_role_buttons, unregistered_menu, seeker_menu, employer_menu
from services.translator import translate
from database.session import async_session
from services.user import create_or_update_user, get_user_by_telegram_id
from datetime import datetime

router = Router()


from aiogram.filters import Command

@router.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    full_name = message.from_user.full_name or "Пользователь"
    user_lang = message.from_user.language_code or "ru"
    lang = user_lang if user_lang in ["ru", "pt", "en"] else "en"

    async with async_session() as session:
        # Проверяем, есть ли пользователь
        user = await get_user_by_telegram_id(session, user_id)
        if not user:
            user_data = {
                "language": lang,
                "last_login": datetime.utcnow()
            }
            await create_or_update_user(session, user_id, user_data)
            reply_markup = unregistered_menu(lang)

        else:
            await create_or_update_user(session, user_id, {"last_login": datetime.utcnow()})
            if user.role == "seeker":
                reply_markup = seeker_menu(lang)
            elif user.role == "employer":
                reply_markup = employer_menu(lang)
            else:
                reply_markup = unregistered_menu(lang)

    greeting = await translate("Привет! Я помогу вам найти работу или сотрудников.", to_lang=lang)
    await message.answer(greeting, reply_markup=reply_markup)

