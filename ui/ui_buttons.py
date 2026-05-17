from aiogram.types import InlineKeyboardButton

# Универсальные кнопки
back_button = InlineKeyboardButton(text="🔙 Назад", callback_data="back")
cancel_button = InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
confirm_button = InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm")

