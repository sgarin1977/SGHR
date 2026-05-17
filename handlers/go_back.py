from aiogram import Router, F
from aiogram.types import CallbackQuery
from database.session import async_session
from services.user import get_user_by_telegram_id
from ui.buttons.menu import (
    main_menu, specialists_menu, vacancies_menu,
    help_menu, unregistered_menu, universal_menu
)
from utils.translate_utils import tr
from database.models import Employer, Seeker, Specialist
from logger import log

router = Router()

@router.callback_query(F.data == "go_back")
async def handle_go_back(callback: CallbackQuery):
    user_id = callback.from_user.id
    log.info(f"[GO_BACK] Пользователь {user_id} вызвал возврат назад")

    async with async_session() as session:
        user = await get_user_by_telegram_id(session, user_id)
        if not user:
            lang = callback.from_user.language_code or "ru"
            log.warning(f"[GO_BACK] Пользователь {user_id} не зарегистрирован")
            await callback.message.edit_text("Вы не зарегистрированы.", reply_markup=unregistered_menu(lang))
            await callback.answer()
            return

        lang = user.language or "ru"
        log.info(f"[GO_BACK] Возврат в универсальное меню для {user_id} на языке {lang}")
        await callback.message.edit_text(tr("choose_section", lang), reply_markup=universal_menu(user, lang))
        await callback.answer()


@router.callback_query(F.data == "to_main_menu")
async def go_home(callback: CallbackQuery):
    user_id = callback.from_user.id
    log.info(f"[TO_MAIN_MENU] Пользователь {user_id} запрашивает главное меню")

    async with async_session() as session:
        user = await get_user_by_telegram_id(session, user_id)
        if not user:
            lang = callback.from_user.language_code or "ru"
            log.warning(f"[TO_MAIN_MENU] Пользователь {user_id} не зарегистрирован")
            await callback.message.edit_text("Вы не зарегистрированы.", reply_markup=unregistered_menu(lang))
            await callback.answer()
            return

        lang = user.language or "ru"
        log.info(f"[TO_MAIN_MENU] Показ главного меню для {user_id} на языке {lang}")
        await callback.message.edit_text(tr("main_menu", lang), reply_markup=main_menu(lang))
        await callback.answer()

