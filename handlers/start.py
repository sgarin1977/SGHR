import os
import logging
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from database.session import get_session
from database.repositories.user import UserRepository

logger = logging.getLogger(__name__)
start_router = Router()

@start_router.message(CommandStart())
async def cmd_start(message: Message):
    tg_id = str(message.from_user.id)
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    lang = message.from_user.language_code or "ru"
    
    async with get_session() as session:
        repo = UserRepository(session)
        
        existing_account = await repo.get_by_platform_account("telegram", tg_id)
        
        if existing_account:
            await message.answer(
                f"Привет, {first_name}! 👋\n\nРады видеть тебя снова в SGHR Beta.",
                reply_markup=get_main_menu_keyboard()
            )
            return

        admin_ids_str = os.getenv("ADMIN_TELEGRAM_IDS", "")
        admin_ids = [i.strip() for i in admin_ids_str.split(",") if i.strip()]
        
        # ВИПРАВЛЕНО: звичайний користувач - це "client" за ТЗ
        assigned_role = "super_admin" if tg_id in admin_ids else "client"

        await repo.create_telegram_user_core(
            platform_user_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=lang,
            role=assigned_role
        )

        role_text = "Супер-Администратор 👑" if assigned_role == "super_admin" else "Клиент 👤"
        await message.answer(
            f"🎉 Добро пожаловать в SGHR Beta, {first_name}!\n\n"
            f"Вы успешно зарегистрированы.\n"
            f"Ваша роль в системе: <b>{role_text}</b>",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )

def get_main_menu_keyboard():
    buttons = [
        [InlineKeyboardButton(text="🔎 Найти специалиста", callback_data="M_FIND")],
        [InlineKeyboardButton(text="💼 Предложить услуги", callback_data="SS_START")],
        [InlineKeyboardButton(text="🗖 Мой кабинет", callback_data="M_CABINET")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="M_SETTINGS")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
