from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_start_buttons():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔎 Я соискатель")],
            [KeyboardButton(text="🏢 Я работодатель")],
        ],
        resize_keyboard=True
    )