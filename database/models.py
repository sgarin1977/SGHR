from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

from datetime import datetime

Base = declarative_base()
class Partner(Base):
    __tablename__ = "partners"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    direction_id = Column(Integer, ForeignKey("directions.id"), nullable=False)
    city = Column(String, nullable=False)

    user = relationship("User")
    direction = relationship("Direction")
    
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    full_name = Column(String)
    role = Column(String)  # employer/seeker
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, default=datetime.utcnow)
    rating = Column(Float, default=0.0)
    profile_complete = Column(Boolean, default=False)

    is_verified = Column(Boolean, default=False)
    is_blocked = Column(Boolean, default=False)
    reputation = Column(Integer, default=0)
    warnings = Column(Integer, default=0)

    language = Column(String, default='auto')
    country = Column(String, nullable=True)

class Vacancy(Base):
    __tablename__ = "vacancies"

    id = Column(Integer, primary_key=True)
    employer_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String)
    description = Column(Text)
    region = Column(String)
    company_type = Column(String)
    salary = Column(String)
    status = Column(String)
    contract_type = Column(String)
    required_experience = Column(String)
    required_skills = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime)
    published_at = Column(DateTime)

    is_approved = Column(Boolean, default=False)
    fraud_score = Column(Float, default=0.0)
    complaints_count = Column(Integer, default=0)

class Commentr(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    related_to = Column(String)
    related_id = Column(Integer)
    comment = Column(Text)
    rating = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

class Seeker(Base):
    __tablename__ = "seekers"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    full_name = Column(String)
    profession = Column(String)
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
    activity = Column(String)

class Specialist(Base):
    __tablename__ = "specialists"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    direction_id = Column(Integer, ForeignKey("directions.id"))
    profession_id = Column(Integer, ForeignKey("professions.id"))
    location_id = Column(Integer, ForeignKey("locations.id"))

    full_name = Column(String)
    country = Column(String)
    city = Column(String)
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

class Profession(Base):
    __tablename__ = "professions"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    name_ru = Column(String)
    direction_id = Column(Integer, ForeignKey("directions.id"))
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

class Direction(Base):
    __tablename__ = "directions"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    name_ru = Column(String)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

class ContactHistory(Base):
    __tablename__ = "contact_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))  # Кто написал
    specialist_id = Column(Integer, ForeignKey("specialists.id"))  # Кому
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

