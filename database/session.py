from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from pathlib import Path
import os

# Загружаем переменные окружения ДО использования
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

# отладочный вывод (можно удалить позже)
print("✅ DATABASE_URL =", DATABASE_URL)

# создаём движок с отключенным кэшем стейтментов для PgBouncer
engine = create_async_engine(
    DATABASE_URL, 
    echo=False,
    connect_args={"statement_cache_size": 0}
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session
