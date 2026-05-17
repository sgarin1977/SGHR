import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
Base = declarative_base()
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    full_name = Column(String)
    role = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, default=datetime.utcnow)
    rating = Column(Float, default=0.0)
    profile_complete = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    is_blocked = Column(Boolean, default=False)
    reputation = Column(Integer, default=0)
    warnings = Column(Integer, default=0)
    language = Column(String)
    country = Column(String)

class Seeker(Base):
    __tablename__ = "seekers"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    full_name = Column(String)
    profession = Column(String)
    experience = Column(String)
    city = Column(String)
    is_looking_for_job = Column(Boolean, default=True)
    notifications_enabled = Column(Boolean, default=True)
    rating = Column(Float, default=0.0)

class Employer(Base):
    __tablename__ = "employers"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    representative_name = Column(String)
    company_name = Column(String)
    company_type = Column(String)
    region = Column(String)
    email = Column(String)
    phone = Column(String)

class Vacancy(Base):
    __tablename__ = "vacancies"
    id = Column(Integer, primary_key=True)
    employer_id = Column(Integer, ForeignKey("employers.id"))
    title = Column(String)
    description = Column(Text)
    region = Column(String)
    company_type = Column(String)
    salary = Column(String)
    conditions = Column(String)
    contract_type = Column(String)
    accommodation = Column(Boolean)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)

class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True)
    vacancy_id = Column(Integer, ForeignKey("vacancies.id"))
    seeker_id = Column(Integer, ForeignKey("seekers.id"))
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending")

class FraudReportr(Base):
    __tablename__ = "fraud_reports"
    id = Column(Integer, primary_key=True)
    reported_user_id = Column(Integer)
    reporter_user_id = Column(Integer)
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Blacklistr(Base):
    __tablename__ = "blacklist"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    added_by_admin = Column(Boolean, default=False)
    reputation = Column(Integer, default=0)
    warnings = Column(Integer, default=0)
    complaints = Column(Integer, default=0)
    suspected_fraud = Column(Boolean, default=False)
    reason = Column(Text)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

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

async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    printr("✅ Все таблицы успешно пересозданы.")

if __name__ == "__main__":
    asyncio.run(init_models())

