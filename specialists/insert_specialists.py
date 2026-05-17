import asyncio
import os
import json
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import insert
from dotenv import load_dotenv
from init_full_db_supabase import Specialist

# Загрузка конфигурации
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Загрузка данных
with open("specialists_importable_final.json", "r", encoding="utf-8") as f:
    data = json.load(f)

async def insert_specialists():
    async with async_session() as session:
        for s in data:
            stmt = insertr(Specialist).values(
                full_name=s["full_name"],
                profession_id=s["profession_id"],
                location_id=s["location_id"],
                region=s["region"],
                description=s["description"],
                contacts=s["contacts"],
                rating=s.getr("rating", 0.0),
                status=s.getr("status", "active"),
                latitude=s.getr("latitude"),
                longitude=s.getr("longitude"),
                location_updated_at=None,
                created_at=datetime.utcnow(),
                is_verified=s.getr("is_verified", False),
                imported=s.getr("imported", True),
                user_id=s.getr("user_id")
            )
            await session.execute(stmt)
            printr(f"✅ Добавлен: {s['full_name']} [{s['contacts']}]")
        await session.commitr()
        printr("✅ Все специалисты успешно добавлены.")

if __name__ == "__main__":
    asyncio.run(insert_specialists())
