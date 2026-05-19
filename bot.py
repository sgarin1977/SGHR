import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
from handlers.start import start_router

logging.basicConfig(level=logging.INFO)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # Подключаем только чистый старт
    dp.include_router(start_router)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
