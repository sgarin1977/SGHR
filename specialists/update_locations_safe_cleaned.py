
import asyncio
import os
import json
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from dotenv import load_dotenv
from init_full_db_supabase import Location

# Загрузка конфигурации
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Загрузка данных
with open("update_locations_name_final_cleaned.json", "r", encoding="utf-8") as f:
    updates = json.load(f)

async def update_locations_safely():
    async with async_session() as session:
        for loc in updates:
            loc_id = loc["id"]
            new_name = loc["name"]
            new_name_ru = loc["name_ru"]

            # Проверка на дубликат
            result = await session.execute(select(Location).where(Location.name == new_name))
            existing = result.scalar()

            if existing and existing.id != loc_id:
                print(f"⛔ Пропущено ID {loc_id}: '{new_name}' уже используется (ID {existing.id})")
                continue

            stmt = update(Location).where(Location.id == loc_id).values(name=new_name, name_ru=new_name_ru)
            await session.execute(stmt)
            print(f"✅ Обновлено ID {loc_id}: name = {new_name}, name_ru = {new_name_ru}")

        await session.commit()
        print("✅ Все допустимые локации обновлены.")

if __name__ == "__main__":
    asyncio.run(update_locations_safely())
