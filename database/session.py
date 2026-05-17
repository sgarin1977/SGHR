from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from pathlib import Path
import os

# Загружаем переменные окружения ДО использования
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

# отладочный вывод (можно удалить позже)
print("✅ DATABASE_URL =", DATABASE_URL)

# создаём движок и сессию
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session

