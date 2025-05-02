# handlers/unregistered_menu.py

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.markdown import hbold

from ui.buttons.menu import start_role_buttons

router = Router()

@router.callback_query(F.data == "find_job")
async def handle_find_job(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("🔎 Фильтры поиска пока не реализованы, но скоро появятся.")

@router.callback_query(F.data == "register_seeker")
async def handle_register_seeker(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("📝 Запускаем регистрацию соискателя...", reply_markup=start_role_buttons("ru"))  # Можешь заменить на свою FSM

@router.callback_query(F.data == "register_employer")
async def handle_register_employer(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("📝 Запускаем регистрацию работодателя...", reply_markup=start_role_buttons("ru"))  # Заменить при необходимости

@router.callback_query(F.data == "help")
async def handle_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        f"ℹ️ {hbold('Помощь')}\n\n"
        "1. Нажмите 'Зарегистрироваться', чтобы создать профиль.\n"
        "2. После регистрации вам откроется дополнительное меню.\n"
        "3. Вы можете переключаться между ролями соискателя и работодателя."
    )

