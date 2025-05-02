# database/db.py

import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment variables.")

# Создаём движок SQLAlchemy
engine = create_async_engine(DATABASE_URL, echo=False)

# Создаём фабрику сессий
async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Генератор сессии
async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session

