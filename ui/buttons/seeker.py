
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from ui.texts import t


def resume_menu_buttons(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(t("view_resume", lang), callback_data="view_resume")],
        [InlineKeyboardButton(t("edit_resume", lang), callback_data="edit_resume")],
        [InlineKeyboardButton(t("send_resume", lang), callback_data="send_resume")]
    ])


def vacancy_action_buttons(lang: str, vacancy_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(t("more_info", lang), callback_data=f"details_{vacancy_id}")],
        [InlineKeyboardButton(t("respond", lang), callback_data=f"respond_{vacancy_id}")],
        [InlineKeyboardButton(t("favorite", lang), callback_data=f"fav_{vacancy_id}")],
        [InlineKeyboardButton(t("report", lang), callback_data=f"report_{vacancy_id}")]
    ])
