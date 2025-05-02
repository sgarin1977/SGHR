
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from ui.texts import t


def back_cancel_buttons(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(t("back", lang), callback_data="back")],
        [InlineKeyboardButton(t("cancel", lang), callback_data="cancel")]
    ])


def confirm_buttons(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(t("confirm", lang), callback_data="confirm")],
        [InlineKeyboardButton(t("cancel", lang), callback_data="cancel")]
    ])
