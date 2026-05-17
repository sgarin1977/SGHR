
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils.lang_manager import tr


def vacancy_manage_buttons(lang: str, vacancy_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(tr("edit_vacancy", lang), callback_data=f"edit_{vacancy_id}")],
        [InlineKeyboardButton(tr("delete_vacancy", lang), callback_data=f"delete_{vacancy_id}")],
        [InlineKeyboardButton(tr("boost_vacancy", lang), callback_data=f"boost_{vacancy_id}")],
        [InlineKeyboardButton(tr("extend_vacancy", lang), callback_data=f"extend_{vacancy_id}")],
        [InlineKeyboardButton(tr("view_stats", lang), callback_data=f"stats_{vacancy_id}")],
    ])
