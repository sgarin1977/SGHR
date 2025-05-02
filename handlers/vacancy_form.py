# handlers/vacancy_form.py

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select
from database.session import async_session
from database.models import Vacancy, User
from datetime import datetime

router = Router()

class VacancyForm(StatesGroup):
    title = State()
    description = State()
    region = State()
    company_type = State()
    salary = State()
    contract_type = State()
    experience = State()
    skills = State()

@router.message(F.text.lower() == "разместить вакансию")
async def start_vacancy_creation(message: Message, state: FSMContext):
    await message.answer("📝 Введите название вакансии:")
    await state.set_state(VacancyForm.title)

@router.message(VacancyForm.title)
async def get_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("📄 Введите описание вакансии:")
    await state.set_state(VacancyForm.description)

@router.message(VacancyForm.description)
async def get_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("🌍 Укажите регион:")
    await state.set_state(VacancyForm.region)

@router.message(VacancyForm.region)
async def get_region(message: Message, state: FSMContext):
    await state.update_data(region=message.text)
    await message.answer("🏢 Тип компании (локальная/международная):")
    await state.set_state(VacancyForm.company_type)

@router.message(VacancyForm.company_type)
async def get_company_type(message: Message, state: FSMContext):
    await state.update_data(company_type=message.text)
    await message.answer("💰 Укажите уровень зарплаты:")
    await state.set_state(VacancyForm.salary)

@router.message(VacancyForm.salary)
async def get_salary(message: Message, state: FSMContext):
    await state.update_data(salary=message.text)
    await message.answer("📃 Тип контракта (по резюме/контракт/устный):")
    await state.set_state(VacancyForm.contract_type)

@router.message(VacancyForm.contract_type)
async def get_contract_type(message: Message, state: FSMContext):
    await state.update_data(contract_type=message.text)
    await message.answer("📈 Требуемый опыт:")
    await state.set_state(VacancyForm.experience)

@router.message(VacancyForm.experience)
async def get_experience(message: Message, state: FSMContext):
    await state.update_data(experience=message.text)
    await message.answer("🧠 Укажите ключевые навыки через запятую:")
    await state.set_state(VacancyForm.skills)

@router.message(VacancyForm.skills)
async def get_skills(message: Message, state: FSMContext):
    await state.update_data(skills=message.text)
    data = await state.get_data()
    await state.clear()

    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Ошибка: пользователь не найден.")
            return

        vacancy = Vacancy(
            employer_id=user.id,
            title=data["title"],
            description=data["description"],
            region=data["region"],
            company_type=data["company_type"],
            salary=data["salary"],
            contract_type=data["contract_type"],
            required_experience=data["experience"],
            required_skills=data["skills"],
            status="active",
            created_at=datetime.utcnow()
        )
        session.add(vacancy)
        await session.commit()

    await message.answer("✅ Вакансия успешно добавлена!")

