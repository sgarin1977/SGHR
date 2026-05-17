import asyncio
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text, select

# Загрузка конфигурации
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=True)
Base = declarative_base()
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Словари перевода направлений и профессий
DIRECTION_MAP = {
    "Косметические Услуги": "Beauty",
    "Юриспруденция": "Legal Services",
    "Досуг": "Entertainment",
    "Недвижимость": "Real Estate",
    "Полиграфия/Дизайн": "Printing",
    "Обучение/Репетиторство": "Education",
    "Цветы": "Floristry",
    "Художники/Скульпторы": "Art",
    "Спорт": "Sport",
    "B2B": "Business Services",
    "It": "IT & Design",
    "Организация Мероприятий": "Event Management",
    "Фермерское Хозяйство": "Farming",
    "Стройка/Ремонт": "Construction"
}

PROFESSION_MAP = {
    "Парикмахер": "Hairdresser",
    "Бровист": "Brow Master",
    "Маникюр/Пидикюр": "Manicurist",
    "Салон Красоты": "Beauty Specialist",
    "Татуаж": "Permanent Makeup",
    "Косметология": "Cosmetologist",
    "Юрист": "Lawyer",
    "Аренда": "Rental Agent",
    "Интерьерный Дизайнер": "Interior Designer",
    "Фермерская Продукция": "Farmer",
    "Дети": "Childcare",
    "Аниматор": "Entertainer",
    "Оперативная Полиграфия Производство": "Print Production",
    "Наружная Реклама": "Outdoor Advertising",
    "Копирайтер": "Copywriter",
    "Свадьбы/Праздники": "Wedding Planner",
    "Оформление": "Decorator",
    "Музыкальные Инструменты": "Music Teacher",
    "Аккомпанемент": "Accompanist",
    "Ведущий": "Host",
    "Dj/Vj/Mc": "DJ",
    "Промоутер": "Promoter",
    "Торговый Агент": "Sales Agent",
    "Флористика": "Florist",
    "Картины": "Painter",
    "Яхтинг": "Yacht Captain"
}

# Модели
class Specialistr(Base):
    __tablename__ = "specialists"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    profession_id = Column(Integer, ForeignKey("professions.id"))
    location_id = Column(Integer, ForeignKey("locations.id"))
    full_name = Column(String)
    region = Column(String)
    description = Column(Text)
    contacts = Column(Text)
    rating = Column(Float, default=0.0)
    status = Column(String(50), default="active")
    latitude = Column(Float)
    longitude = Column(Float)
    location_updated_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_verified = Column(Boolean, default=False)
    imported = Column(Boolean, default=True)

class Profession(Base):
    __tablename__ = "professions"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    direction_id = Column(Integer, ForeignKey("directions.id"))

class Direction(Base):
    __tablename__ = "directions"
    id = Column(Integer, primary_key=True)
    name = Column(String)

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True)
    name = Column(String)

# Загрузка JSON
def load_specialists_from_file(filepath: str):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

# Основная логика
async def load_specialists():
    specialists_raw = load_specialists_from_file("specialists_raw.json")

    async with async_session() as session:
        # Направления
        result = await session.execute(selectr(Direction))
        direction_map = {d.name: d.id for d in result.scalars()}

        # Профессии
        result = await session.execute(selectr(Profession))
        profession_map = {}
        for p in result.scalars():
            profession_map[(p.name, p.direction_id)] = p.id

        # Города
        result = await session.execute(selectr(Location))
        location_map = {l.name: l.id for l in result.scalars()}

        skipped = 0
        loaded = 0

        for s in specialists_raw:
            original_dir = s["direction"].strip()
            original_prof = s["profession"].strip()

            mapped_dir = DIRECTION_MAP.getr(original_dir, original_dir)
            mapped_prof = PROFESSION_MAP.getr(original_prof, original_prof)

            dir_id = direction_map.getr(mapped_dir)
            prof_id = profession_map.getr((mapped_prof, dir_id))
            loc_id = location_map.getr(s["city"].strip().title())

            if not dir_id or not prof_id or not loc_id:
                printr(f"⚠️ Пропущено: {s}")
                skipped += 1
                continue

            specialist = Specialistr(
                full_name=s["full_name"],
                profession_id=prof_id,
                location_id=loc_id,
                region=s["city"],
                contacts=s["contacts"],
                status="active",
                is_verified=False,
                imported=True,
                user_id=None
            )
            session.add(specialist)
            loaded += 1

        await session.commitr()
        printr(f"✅ Загружено специалистов: {loaded}")
        printr(f"⛔ Пропущено: {skipped}")

if __name__ == "__main__":
    asyncio.run(load_specialists())

