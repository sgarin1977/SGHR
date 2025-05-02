from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command
from datetime import datetime
from sqlalchemy import select
from database.models import Employer
from database.session import async_session
from services.user import create_or_update_user

router = Router()

class EmployerForm(StatesGroup):
    full_name = State()
    company_name = State()
    company_type = State()
    region = State()
    activity = State()  # ← добавлено
    email = State()
    phone = State()

@router.message(Command("employer"))
async def start_employer_registration(message: Message, state: FSMContext):
    await message.answer("👤 Введите имя представителя:")
    await state.set_state(EmployerForm.full_name)

@router.message(EmployerForm.full_name)
async def process_full_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("🏢 Введите название компании:")
    await state.set_state(EmployerForm.company_name)

@router.message(EmployerForm.company_name)
async def process_company_name(message: Message, state: FSMContext):
    await state.update_data(company_name=message.text)
    await message.answer("🏢 Укажите тип компании (например, локальная, международная):")
    await state.set_state(EmployerForm.company_type)

@router.message(EmployerForm.company_type)
async def process_company_type(message: Message, state: FSMContext):
    await state.update_data(company_type=message.text)
    await message.answer("🌍 Укажите регион вашей деятельности:")
    await state.set_state(EmployerForm.region)

@router.message(EmployerForm.region)
async def process_region(message: Message, state: FSMContext):
    await state.update_data(region=message.text)
    await message.answer("📌 Чем занимается компания?")
    await state.set_state(EmployerForm.activity)

@router.message(EmployerForm.activity)
async def process_activity(message: Message, state: FSMContext):
    await state.update_data(activity=message.text)
    await message.answer("📧 Укажите email:")
    await state.set_state(EmployerForm.email)

@router.message(EmployerForm.email)
async def process_email(message: Message, state: FSMContext):
    await state.update_data(email=message.text)
    await message.answer("📞 Укажите номер телефона:")
    await state.set_state(EmployerForm.phone)

@router.message(EmployerForm.phone)
async def process_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text)
    data = await state.get_data()
    await state.clear()

    async with async_session() as session:
        # Сохраняем/обновляем пользователя
        user_data = {
            "full_name": data["full_name"],
            "role": "employer",
            "language": message.from_user.language_code or "auto",
            "profile_complete": True,
            "last_login": datetime.utcnow()
        }
        user = await create_or_update_user(session, message.from_user.id, user_data)

        # Добавляем работодателя
        existing = await session.execute(select(Employer).where(Employer.user_id == user.id))
        if not existing.scalar_one_or_none():
            employer = Employer(
                user_id=user.id,
                representative_name=data["full_name"],
                company_name=data["company_name"],
                company_type=data["company_type"],
                region=data["region"],
                activity=data["activity"],
                email=data["email"],
                phone=data["phone"],
            )
            session.add(employer)
            await session.commit()

    await message.answer("✅ Профиль работодателя успешно сохранён!")
0
