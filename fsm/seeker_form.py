from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command

from database.models import User, Seeker
from database.session import async_session
from services.user import get_user_by_telegram_id, create_or_update_user
from logger import log

router = Router()

class SeekerRegistration(StatesGroup):
    full_name = State()
    profession = State()
    region = State()
    language = State()
    status = State()

@router.message(Command("seeker"))
async def start_registration(message: Message, state: FSMContext):
    await message.answer("👤 Введите ваше полное имя:")
    await state.set_state(SeekerRegistration.full_name)

@router.message(SeekerRegistration.full_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("💼 Введите вашу профессию:")
    await state.set_state(SeekerRegistration.profession)

@router.message(SeekerRegistration.profession)
async def process_profession(message: Message, state: FSMContext):
    await state.update_data(profession=message.text)
    await message.answer("🌍 Введите ваш регион:")
    await state.set_state(SeekerRegistration.region)

@router.message(SeekerRegistration.region)
async def process_region(message: Message, state: FSMContext):
    await state.update_data(region=message.text)
    await message.answer("🌐 Введите ваш язык:")
    await state.set_state(SeekerRegistration.language)

@router.message(SeekerRegistration.language)
async def process_language(message: Message, state: FSMContext):
    await state.update_data(language=message.text)
    await message.answer("🧭 Введите ваш текущий статус (работаю/в поиске и т.д.):")
    await state.set_state(SeekerRegistration.status)

@router.message(SeekerRegistration.status)
async def process_status(message: Message, state: FSMContext):
    await state.update_data(status=message.text)
    data = await state.get_data()
    await state.clear()

    async with async_session() as session:
        user_data = {
            "full_name": data["full_name"],
            "country": data["region"],
            "language": data["language"],
            "profile_complete": True
        }
        user = await create_or_update_user(session, message.from_user.id, user_data)

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
        await session.commit()

        # Только после успешной регистрации — проставляем роль
        await create_or_update_user(session, message.from_user.id, {"role": "seeker"})

    await message.answer(
        f"✅ Спасибо! Вы зарегистрированы:\n\n"
        f"Имя: {data.get('full_name')}\n"
        f"Профессия: {data.get('profession')}\n"
        f"Регион: {data.get('region')}\n"
        f"Язык: {data.get('language')}\n"
        f"Статус: {data.get('status')}"
    )

