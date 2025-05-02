
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from ui.texts import t


def vacancy_manage_buttons(lang: str, vacancy_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(t("edit_vacancy", lang), callback_data=f"edit_{vacancy_id}")],
        [InlineKeyboardButton(t("delete_vacancy", lang), callback_data=f"delete_{vacancy_id}")],
        [InlineKeyboardButton(t("boost_vacancy", lang), callback_data=f"boost_{vacancy_id}")],
        [InlineKeyboardButton(t("extend_vacancy", lang), callback_data=f"extend_{vacancy_id}")],
        [InlineKeyboardButton(t("view_stats", lang), callback_data=f"stats_{vacancy_id}")],
    ])
