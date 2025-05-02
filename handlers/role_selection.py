from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from fsm.seeker_form import SeekerRegistration
from fsm.employer_form import EmployerForm

router = Router()

@router.callback_query(F.data == "register_seeker")
async def handle_register_seeker(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📄 Начинаем регистрацию как соискатель.\nВведите ваше полное имя:")
    await state.set_state(SeekerRegistration.full_name)
    await callback.answer()

@router.callback_query(F.data == "register_employer")
async def handle_register_employer(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🏢 Начинаем регистрацию как работодатель.\nВведите имя представителя:")
    await state.set_state(EmployerForm.full_name)
    await callback.answer()

@router.callback_query(F.data == "find_job")
async def handle_find_job(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("🔍 Поиск работы пока в разработке. Здесь появится фильтр и список вакансий.")

@router.callback_query(F.data == "help")
async def handle_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("ℹ️ Помощь по использованию:\n— Зарегистрируйтесь как соискатель или работодатель\n— Используйте меню для навигации\n— Вопросы? Напишите администратору.")

