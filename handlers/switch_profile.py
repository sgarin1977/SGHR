# handlers/switch_profile.py

from aiogram import Router, F
from aiogram.types import CallbackQuery
from database.session import async_session
from services.user import get_user_by_telegram_id, create_or_update_user
from ui.buttons.menu import seeker_menu, employer_menu
from datetime import datetime

router = Router()

@router.callback_query(F.data == "switch_profile")
async def handle_switch_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = callback.from_user.language_code or "ru"

    async with async_session() as session:
        user = await get_user_by_telegram_id(session, user_id)

        if not user:
            await callback.answer("Сначала нужно зарегистрироваться.", show_alert=True)
            return

        new_role = "employer" if user.role == "seeker" else "seeker"
        await create_or_update_user(session, user_id, {
            "role": new_role,
            "last_login": datetime.utcnow()
        })

        if new_role == "employer":
            await callback.message.edit_text("Вы переключились на профиль работодателя.", reply_markup=employer_menu(lang))
        else:
            await callback.message.edit_text("Вы переключились на профиль соискателя.", reply_markup=seeker_menu(lang))

        await callback.answer()

