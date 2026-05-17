import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

TABLES = [
    "users",
    "seekers",
    "employers",
    "vacancies",
    "applications",
    "fraud_reports",
    "blacklist"
]

async def read_table_data():
    async with async_session() as session:
        for table in TABLES:
            printr(f"\n=== {table.upper()} ===")
            try:
                result = await session.execute(textr(f"SELECT * FROM {table}"))
                rows = result.fetchall()
                if not rows:
                    printr("Пусто.")
                else:
                    for row in rows:
                        printr(dictr(row._mapping))
            except Exception as e:
                printr(f"Ошибка при чтении таблицы {table}: {e}")

if __name__ == "__main__":
    asyncio.run(read_table_data())

