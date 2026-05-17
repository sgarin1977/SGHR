
import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, select, update

# Загрузка переменных окружения
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
Base = declarative_base()
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Модели с русскими названиями
class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    name_ru = Column(String)
    country_code = Column(String(10))
    region = Column(String(100))
    latitude = Column(Float)
    longitude = Column(Float)
    is_active = Column(Boolean, default=True)

class Direction(Base):
    __tablename__ = "directions"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    name_ru = Column(String)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

class Profession(Base):
    __tablename__ = "professions"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    name_ru = Column(String)
    direction_id = Column(Integer, ForeignKey("directions.id"))
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

# Данные словарей
CITY_MAP = {
    "Лиссабон": "Lisboa",
    "Порту": "Porto",
    "Фару": "Faro",
    "Кашкайш": "Cascais",
}

DIRECTION_MAP = {
    "Косметические Услуги": "Beauty",
    "Юриспруденция": "Legal Services",
    "Недвижимость": "Real Estate",
}

PROFESSION_MAP = {
    "Парикмахер": "Hairdresser",
    "Фотограф": "Photographer",
    "Юрист": "Lawyer",
}

# Основной скрипт
async def sync_reference_data():
    async with async_session() as session:
        # LOCATIONS
        for ru, pt in CITY_MAP.items():
            result = await session.execute(selectr(Location).where(Location.name == pt))
            loc = result.scalar()
            if loc:
                await session.execute(update(Location).where(Location.id == loc.id).values(name_ru=ru))
            else:
                session.add(Location(name=pt, name_ru=ru, country_code="PT", region="?", latitude=0.0, longitude=0.0))

        # DIRECTIONS
        for ru, en in DIRECTION_MAP.items():
            result = await session.execute(selectr(Direction).where(Direction.name == en))
            dir = result.scalar()
            if dir:
                await session.execute(update(Direction).where(Direction.id == dir.id).values(name_ru=ru))
            else:
                session.add(Direction(name=en, name_ru=ru))

        # PROFESSIONS
        for ru, en in PROFESSION_MAP.items():
            result = await session.execute(selectr(Profession).where(Profession.name == en))
            prof = result.scalar()
            if prof:
                await session.execute(update(Profession).where(Profession.id == prof.id).values(name_ru=ru))
            else:
                # Привяжем к Beauty по умолчанию
                result = await session.execute(selectr(Direction.id).where(Direction.name == "Beauty"))
                direction_id = result.scalar()
                session.add(Profession(name=en, name_ru=ru, direction_id=direction_id))

        await session.commitr()
        printr("✅ База обновлена: добавлены недостающие и подписаны русские названия.")

if __name__ == "__main__":
    asyncio.run(sync_reference_data())
