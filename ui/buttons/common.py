
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils.lang_manager import tr


def back_cancel_buttons(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(tr("back", lang), callback_data="back")],
        [InlineKeyboardButton(tr("cancel", lang), callback_data="cancel")]
    ])


def confirm_buttons(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(tr("confirm", lang), callback_data="confirm")],
        [InlineKeyboardButton(tr("cancel", lang), callback_data="cancel")]
    ])
