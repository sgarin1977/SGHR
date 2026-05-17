
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils.lang_manager import tr


def resume_menu_buttons(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(tr("view_resume", lang), callback_data="view_resume")],
        [InlineKeyboardButton(tr("edit_resume", lang), callback_data="edit_resume")],
        [InlineKeyboardButton(tr("send_resume", lang), callback_data="send_resume")]
    ])


def vacancy_action_buttons(lang: str, vacancy_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(tr("more_info", lang), callback_data=f"details_{vacancy_id}")],
        [InlineKeyboardButton(tr("respond", lang), callback_data=f"respond_{vacancy_id}")],
        [InlineKeyboardButton(tr("favorite", lang), callback_data=f"fav_{vacancy_id}")],
        [InlineKeyboardButton(tr("report", lang), callback_data=f"report_{vacancy_id}")]
    ])
