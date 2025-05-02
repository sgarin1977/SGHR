from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from ui.texts import t


def start_role_buttons(lang: str) -> InlineKeyboardMarkup:
    """Меню на старте для выбора роли (работодатель или соискатель)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("im_employer", lang), callback_data="role_employer")],
        [InlineKeyboardButton(text=t("im_seeker", lang), callback_data="role_seeker")]
    ])


def unregistered_menu(lang: str) -> InlineKeyboardMarkup:
    """Меню для пользователей без регистрации"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 " + t("find_job", lang), callback_data="find_job")],
        [InlineKeyboardButton(text="🛠 " + t("find_specialists", lang), callback_data="view_specialists")],
        [InlineKeyboardButton(text="📝 " + t("register_seeker", lang), callback_data="register_seeker")],
        [InlineKeyboardButton(text="🏢 " + t("register_employer", lang), callback_data="register_employer")],
        [InlineKeyboardButton(text="ℹ️ " + t("help", lang), callback_data="help")]
    ])


def seeker_menu(lang: str) -> InlineKeyboardMarkup:
    """Меню для соискателя"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("new_vacancies", lang), callback_data="new_vacancies")],
        [InlineKeyboardButton(text=t("messages_from_employers", lang), callback_data="messages_from_employers")],
        [InlineKeyboardButton(text=t("edit_profile", lang), callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔄 " + t("switch_profile", lang), callback_data="switch_profile")],
        [InlineKeyboardButton(text="❓ " + t("help", lang), callback_data="help")]
    ])


def employer_menu(lang: str) -> InlineKeyboardMarkup:
    """Меню для работодателя"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("my_vacancies", lang), callback_data="my_vacancies")],
        [InlineKeyboardButton(text=t("candidates_responses", lang), callback_data="candidates_responses")],
        [InlineKeyboardButton(text=t("add_vacancy", lang), callback_data="add_vacancy")],
        [InlineKeyboardButton(text=t("edit_profile", lang), callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔄 " + t("switch_profile", lang), callback_data="switch_profile")],
        [InlineKeyboardButton(text="❓ " + t("help", lang), callback_data="help")]
    ])

