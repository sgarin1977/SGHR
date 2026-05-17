from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils.translate_utils import tr
from logger import log


def unregistered_menu(lang):
    log.debug(f"[MENU] Генерация меню незарегистрированного пользователя | lang={lang}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🧑‍🔧 {tr('register_specialist', lang)}", callback_data="register_specialist")],
        [InlineKeyboardButton(text=f"📝 {tr('register_seeker', lang)}", callback_data="register_seeker")],
        [InlineKeyboardButton(text=f"🏢 {tr('register_employer', lang)}", callback_data="register_employer")]
    ])


def universal_menu(user):
    lang = user.language
    log.debug(f"[MENU] Универсальное меню | user_id={user.telegram_id} | lang={lang}")

    if user.is_specialist:
        return specialists_menu(lang, is_specialist=True)
    elif user.is_employer:
        return vacancies_menu(lang, role="employer")
    elif user.is_seeker:
        return vacancies_menu(lang, role="seeker")
    else:
        return unregistered_menu(lang)


def main_menu(lang):
    log.debug(f"[MENU] Главное меню | lang={lang}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🧑‍🔧 {tr('specialists_section', lang)}", callback_data="specialists_menu")],
        [InlineKeyboardButton(text=f"📄 {tr('vacancies_section', lang)}", callback_data="vacancies_menu")],
        [InlineKeyboardButton(text=f"❓ {tr('help', lang)}", callback_data="help")]
    ])


def specialists_menu(lang, is_specialist=False):
    log.debug(f"[MENU] Меню специалистов | lang={lang} | is_specialist={is_specialist}")
    buttons = [
        [InlineKeyboardButton(text=f"🔍 {tr('find_specialists', lang)}", callback_data="find_specialist")]
    ]
    if is_specialist:
        buttons.append([InlineKeyboardButton(text=f"👤 {tr('profile', lang)}", callback_data="specialist_profile")])
    else:
        buttons.append([InlineKeyboardButton(text=f"💪 {tr('register_specialist', lang)}", callback_data="register_specialist")])
    buttons.append([
        InlineKeyboardButton(text="🔙", callback_data="back_to_menu"),
        InlineKeyboardButton(text="🏠", callback_data="to_main_menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vacancies_menu(lang, role=None):
    log.debug(f"[MENU] Меню вакансий | lang={lang} | role={role}")
    buttons = [
        [InlineKeyboardButton(text=f"📜 {tr('find_job', lang)}", callback_data="find_job")]
    ]
    if role == "seeker":
        buttons.append([InlineKeyboardButton(text=f"👤 {tr('profile', lang)}", callback_data="seeker_profile")])
    elif role == "employer":
        buttons.append([InlineKeyboardButton(text=f"👤 {tr('profile', lang)}", callback_data="employer_profile")])
    else:
        buttons.append([InlineKeyboardButton(text=f"📝 {tr('register_seeker', lang)}", callback_data="register_seeker")])
        buttons.append([InlineKeyboardButton(text=f"🏢 {tr('register_employer', lang)}", callback_data="register_employer")])
    buttons.append([
        InlineKeyboardButton(text="🔙", callback_data="back_to_menu"),
        InlineKeyboardButton(text="🏠", callback_data="to_main_menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def help_menu(lang):
    log.debug(f"[MENU] Меню помощи | lang={lang}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ℹ️ " + tr("help", lang), callback_data="help_info")],
        [
            InlineKeyboardButton(text="🔙", callback_data="back_to_menu"),
            InlineKeyboardButton(text="🏠", callback_data="to_main_menu")
        ]
    ])


def personal_profile_menu(user, lang):
    log.debug(f"[MENU] Меню профиля | user_id={user.telegram_id} | lang={lang}")
    buttons = []
    role_text = tr("current_role", lang) + ": "
    if user.is_specialist:
        role_text += tr("role_specialist", lang)
    elif user.is_employer:
        role_text += tr("role_employer", lang)
    elif user.is_seeker:
        role_text += tr("role_seeker", lang)
    else:
        role_text += tr("role_unregistered", lang)
    buttons.append([InlineKeyboardButton(text=f"ℹ️ {role_text}", callback_data="noop")])
    buttons.append([InlineKeyboardButton(text=f"📝 {tr('view_profile', lang)}", callback_data="view_profile")])
    buttons.append([InlineKeyboardButton(text=f"💬 {tr('chat_history', lang)}", callback_data="chat_history")])
    buttons.append([InlineKeyboardButton(text=f"🌐 {tr('switch_profile', lang)}", callback_data="switch_profile")])
    buttons.append([
        InlineKeyboardButton(text="🔙", callback_data="back_to_menu"),
        InlineKeyboardButton(text="🏠", callback_data="to_main_menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

