import asyncio
import json
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import update, select
from dotenv import load_dotenv
from init_full_db_supabase import Profession

# Загрузка .env
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Загрузка данных
with open("profession_name_final_translated.json", "r", encoding="utf-8") as f:
    updates = json.load(f)

async def update_profession_names():
    async with async_session() as session:
        for row in updates:
            prof_id = row["id"]
            new_name = row["name"]

            # Проверка на дубликат
            result = await session.execute(
                select(Profession).where(Profession.name == new_name)
            )
            existing = result.scalar()

            if existing and existing.id != prof_id:
                print(f"⛔ Пропущено ID {prof_id}: '{new_name}' уже используется (ID {existing.id})")
                continue

            await session.execute(
                update(Profession).where(Profession.id == prof_id).values(name=new_name)
            )
            print(f"✅ Обновлено ID {prof_id}: name → {new_name}")

        await session.commit()
        print("✅ Все английские названия профессий обновлены.")

if __name__ == "__main__":
    asyncio.run(update_profession_names())

