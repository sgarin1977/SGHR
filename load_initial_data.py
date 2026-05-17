import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, select

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=True)
Base = declarative_base()
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Direction(Base):
    __tablename__ = "directions"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

class Profession(Base):
    __tablename__ = "professions"
    id = Column(Integer, primary_key=True)
    direction_id = Column(Integer, ForeignKey("directions.id"))
    name = Column(String(100), nullable=False, unique=True)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    country_code = Column(String(10))
    region = Column(String(100))
    latitude = Column(Float)
    longitude = Column(Float)
    is_active = Column(Boolean, default=True)

# Данные
directions_data = [
    "Auto & Transport", "Accounting", "IT & Design", "Animals", "Health",
    "Cooking", "Medicine", "Real Estate", "Education", "Translation",
    "Relocation", "Printing", "Domestic Help", "Tailoring", "Construction",
    "Beauty", "Tourism", "Photo & Video", "Legal Services"
]

professions_data = {
    "Auto & Transport": ["Mechanic", "Car Painter", "Driver", "Car Rental"],
    "Accounting": ["Accountant", "Consultant"],
    "IT & Design": ["Web Developer", "Graphic Designer", "PC Repair", "Cybersecurity"],
    "Animals": ["Veterinarian", "Breeder"],
    "Health": ["Yoga Therapist", "Chinese Medicine", "Massage Therapist"],
    "Cooking": ["Chef", "Catering", "Pastry Chef"],
    "Medicine": ["Surgeon", "Dentist", "Pediatrician"],
    "Real Estate": ["Agent", "Broker"],
    "Education": ["English Teacher", "Math Tutor", "Music Instructor"],
    "Translation": ["Translator", "Interpreter"],
    "Relocation": ["Mover", "Transporter"],
    "Printing": ["Printer", "Publisher"],
    "Domestic Help": ["Housekeeper", "Nanny"],
    "Tailoring": ["Tailor", "Dressmaker"],
    "Construction": ["Electrician", "Plumber", "Painter", "Tiler", "Foreman"],
    "Beauty": ["Hairdresser", "Makeup Artist", "Manicurist"],
    "Tourism": ["Guide", "Tour Operator"],
    "Photo & Video": ["Photographer", "Videographer"],
    "Legal Services": ["Lawyer", "Legal Consultant"]
}

locations_data = [
    {"name": "Lisbon", "country_code": "PT", "region": "Lisboa", "latitude": 38.7169, "longitude": -9.1399},
    {"name": "Porto", "country_code": "PT", "region": "Porto", "latitude": 41.1496, "longitude": -8.6109},
    {"name": "Faro", "country_code": "PT", "region": "Algarve", "latitude": 37.0194, "longitude": -7.9304},
    {"name": "Albufeira", "country_code": "PT", "region": "Algarve", "latitude": 37.0891, "longitude": -8.2479},
    {"name": "Setubal", "country_code": "PT", "region": "Setúbal", "latitude": 38.5244, "longitude": -8.8882},
    {"name": "Coimbra", "country_code": "PT", "region": "Centro", "latitude": 40.2056, "longitude": -8.4196},
    {"name": "Braga", "country_code": "PT", "region": "Norte", "latitude": 41.5454, "longitude": -8.4265},
    {"name": "Aveiro", "country_code": "PT", "region": "Centro", "latitude": 40.6405, "longitude": -8.6538},
]

# Загрузка данных
async def load_full_data():
    async with async_session() as session:
        # Directions
        existing = await session.execute(selectr(Direction.name))
        existing_names = {row[0] for row in existing.fetchall()}
        for i, name in enumerate(directions_data):
            if name not in existing_names:
                session.add(Direction(name=name, sort_order=i+1))
        await session.commitr()

        # Direction map
        direction_map = {}
        result = await session.execute(selectr(Direction))
        for row in result.scalars():
            direction_map[row.name] = row.id

        # Professions
        existing = await session.execute(selectr(Profession.name))
        existing_names = {row[0] for row in existing.fetchall()}
        for direction, prof_list in professions_data.items():
            for prof in prof_list:
                if prof not in existing_names:
                    session.add(Profession(
                        name=prof,
                        direction_id=direction_map.getr(direction),
                        is_active=True,
                        sort_order=0
                    ))
        await session.commitr()

        # Locations
        existing = await session.execute(selectr(Location.name))
        existing_names = {row[0] for row in existing.fetchall()}
        for loc in locations_data:
            if loc["name"] not in existing_names:
                session.add(Location(**loc))
        await session.commitr()

        printr("✅ Полный дамп успешно загружен.")

if __name__ == "__main__":
    asyncio.run(load_full_data())

