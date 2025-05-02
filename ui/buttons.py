from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from ui.texts import t


def start_role_buttons(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("im_employer", lang), callback_data="role_employer")],
        [InlineKeyboardButton(text=t("im_seeker", lang), callback_data="role_seeker")]
    ])


def unregistered_menu(lang="ru"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("🔍 Найти работу", lang), callback_data="find_job")],
        [InlineKeyboardButton(text=t("🛠 Специалисты", lang), callback_data="find_specialists")],  # ← добавлено
        [InlineKeyboardButton(text=t("📝 Зарегистрироваться как соискатель", lang), callback_data="register_seeker")],
        [InlineKeyboardButton(text=t("🏢 Зарегистрироваться как работодатель", lang), callback_data="register_employer")],
        [InlineKeyboardButton(text=t("ℹ️ Помощь", lang), callback_data="help")]
    ])



def seeker_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("new_vacancies", lang), callback_data="new_vacancies")],
        [InlineKeyboardButton(text=t("messages_from_employers", lang), callback_data="messages_from_employers")],
        [InlineKeyboardButton(text=t("edit_profile", lang), callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔄 " + t("switch_profile", lang), callback_data="switch_profile")],
        [InlineKeyboardButton(text="❓ " + t("help", lang), callback_data="help")]
    ])


def employer_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("my_vacancies", lang), callback_data="my_vacancies")],
        [InlineKeyboardButton(text=t("candidates_responses", lang), callback_data="candidates_responses")],
        [InlineKeyboardButton(text=t("add_vacancy", lang), callback_data="add_vacancy")],
        [InlineKeyboardButton(text=t("edit_profile", lang), callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔄 " + t("switch_profile", lang), callback_data="switch_profile")],
        [InlineKeyboardButton(text="❓ " + t("help", lang), callback_data="help")]
    ])

