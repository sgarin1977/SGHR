import logging
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

logger = logging.getLogger(__name__)
start_router = Router()

@start_router.message(CommandStart())
async def cmd_start(message: Message):
    user_name = message.from_user.first_name
    await message.answer(f"Привет, {user_name}! 👋\n\n✅ Beta-архитектура подключена. Никаких старых таблиц!")
