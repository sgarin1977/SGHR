from aiogram import Router, F
from aiogram.types import CallbackQuery
from database.session import async_session
from services.user import get_user_by_telegram_id, create_or_update_user
from ui.buttons.menu import main_menu, specialists_menu, vacancies_menu, help_menu, universal_menu
from database.models import Seeker, Employer, Specialist
from logger import log
from utils.translate_utils import tr
from aiogram.fsm.context import FSMContext

router = Router()

def get_lang(event):
    code = (event.from_user.language_code or "ru").split("-")[0].split("_")[0]
    return code if code in ["ru", "pt", "en"] else "ru"

@router.callback_query(F.data == "specialists_menu")
async def show_specialists_menu(call: CallbackQuery, state: FSMContext):
    lang = get_lang(call)
    await state.update_data(prev_step="main_menu")
    log.info(f"[SHOW_SPECIALISTS_MENU] User: {call.from_user.id}, Lang: {lang}")
    await call.message.edit_text(tr("specialists_section", lang), reply_markup=specialists_menu(lang))

@router.callback_query(F.data == "vacancies_menu")
async def show_vacancies_menu(call: CallbackQuery, state: FSMContext):
    lang = get_lang(call)
    await state.update_data(prev_step="main_menu")
    log.info(f"[SHOW_VACANCIES_MENU] User: {call.from_user.id}, Lang: {lang}")
    await call.message.edit_text(tr("vacancies_section", lang), reply_markup=vacancies_menu(lang))

@router.callback_query(F.data == "switch_profile")
async def handle_switch_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = get_lang(callback)

    async with async_session() as session:
        user = await get_user_by_telegram_id(session, user_id)
        log.info(f"[SWITCH_PROFILE] Current user: {user_id}, Role: {user.role if user else 'None'}")

        roles = []
        if await session.get(Seeker, user_id):
            roles.append("seeker")
        if await session.get(Employer, user_id):
            roles.append("employer")
        if await session.get(Specialist, user_id):
            roles.append("specialist")

        log.info(f"[SWITCH_PROFILE] Available roles for user {user_id}: {roles}")

        if not roles:
            log.warning(f"[SWITCH_PROFILE] No roles found for user {user_id}")
            await callback.answer("У вас нет регистрированных профилей для переключения.", show_alert=True)
            return

        current_index = roles.index(user.role) if user.role in roles else 0
        new_role = roles[(current_index + 1) % len(roles)]
        log.info(f"[SWITCH_PROFILE] Switching role: {user.role} -> {new_role}")

        await create_or_update_user(session, user_id, {"role": new_role})
        updated_user = await get_user_by_telegram_id(session, user_id)

        if new_role == "employer":
            text = tr("role_switched_to_employer", lang)
        elif new_role == "seeker":
            text = tr("role_switched_to_seeker", lang)
        else:
            text = tr("role_switched_to_specialist", lang)

        log.info(f"[SWITCH_PROFILE] Message to user {user_id}: {text}")
        menu = universal_menu(updated_user, lang)

    await callback.message.edit_text(text, reply_markup=menu)
    await callback.answer()

