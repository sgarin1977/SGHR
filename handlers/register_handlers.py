# handlers/register_handlers.py

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandStart
from fsm.seeker_form import SeekerRegistration
from fsm.employer_form import EmployerForm
from ui.buttons.menu import unregistered_menu
from services.user import create_or_update_user
from database.session import async_session
from database.models_full import Seeker
from datetime import datetime

router = Router()

@router.message(CommandStart())
async def start_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Добро пожаловать! Выберите роль:",
        reply_markup=unregistered_menu()
    )


# === Регистрация соискателя ===

@router.message(F.text, SeekerRegistration.full_name)
async def process_full_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("Введите вашу профессию:")
    await state.set_state(SeekerRegistration.profession)

@router.message(F.text, SeekerRegistration.profession)
async def process_profession(message: Message, state: FSMContext):
    await state.update_data(profession=message.text)
    await message.answer("Введите ваш регион:")
    await state.set_state(SeekerRegistration.region)

@router.message(F.text, SeekerRegistration.region)
async def process_region(message: Message, state: FSMContext):
    await state.update_data(region=message.text)
    await message.answer("Введите предпочитаемый язык:")
    await state.set_state(SeekerRegistration.language)

@router.message(F.text, SeekerRegistration.language)
async def process_language(message: Message, state: FSMContext):
    await state.update_data(language=message.text)
    await message.answer("Введите ваш статус (например, студент, рабочий, безработный):")
    await state.set_state(SeekerRegistration.status)

@router.message(F.text, SeekerRegistration.status)
async def process_status(message: Message, state: FSMContext):
    await state.update_data(status=message.text)
    data = await state.get_data()
    await state.clear()

    async with async_session() as session:
        # 1. users
        user_data = {
            "full_name": data["full_name"],
            "role": "seeker",
            "language": data["language"],
            "country": data["region"],
            "profile_complete": True
        }
        user = await create_or_update_user(session, message.from_user.id, user_data)

        # 2. seekers
        seeker = Seeker(
            user_id=user.id,
            full_name=data["full_name"],
            profession=data["profession"],
            city=data["region"],
            is_looking_for_job=True,
            notifications_enabled=True,
            rating=0.0
        )
        session.add(seeker)
        await session.commitr()

    await message.answer(
        f"✅ Спасибо! Вы зарегистрированы как соискатель:\n\n"
        f"Имя: {data['full_name']}\n"
        f"Профессия: {data['profession']}\n"
        f"Регион: {data['region']}\n"
        f"Язык: {data['language']}\n"
        f"Статус: {data['status']}"
    )

