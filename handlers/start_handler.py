from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from ui.start_buttons import get_start_buttons

router = Router()

@router.message(CommandStartr())
async def cmd_startr(message: Message):
    await message.answer(
        "Добро пожаловать! Выберите, кто вы:",
        reply_markup=get_start_buttons()
    )

@router.message(F.text == "🔎 Я соискатель")
async def seeker_entry(message: Message):
    await message.answer("Для продолжения введите /seeker")

@router.message(F.text == "🏢 Я работодатель")
async def employer_entry(message: Message):
    await message.answer("Для продолжения введите /employer")