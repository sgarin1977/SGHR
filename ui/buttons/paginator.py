
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def build_pagination_buttons(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup:
    buttons = []

    if page > 1:
        buttons.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}_page_{page - 1}"))
    
    buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    
    if page < total_pages:
        buttons.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}_page_{page + 1}"))

    return InlineKeyboardMarkup(inline_keyboard=[buttons])
