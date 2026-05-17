
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from dotenv import load_dotenv
from init_full_db_supabase import Profession

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# 🔁 Словарь с переводами профессий
translated_professions = {
    "Manicure/Pedicure": "Маникюр/Педикюр",
    "Hairdresser": "Парикмахер",
    "Cosmetologist": "Косметолог",
    "Psychologist": "Психолог",
    "Dentist": "Стоматолог",
    "Veterinarian": "Ветеринар",
    "Rehabilitation Specialist": "Реабилитолог",
    "Traditional Chinese Medicine": "Традиционная китайская медицина",
    "Therapeutic Massage": "Лечебный массаж",
    "Tattoo Artist": "Мастер татуажа",
    "Makeup Artist": "Визажист",
    "DJ/VJ/MC": "DJ/VJ/MC",
    "Photographer": "Фотограф",
    "Videographer": "Видеограф",
    "Electrician": "Электрик",
    "Plumber": "Сантехник",
    "Welder": "Сварщик",
    "Painter": "Маляр",
    "Mason": "Каменщик",
    "Construction Worker": "Строитель",
    "Mover": "Грузчик",
    "Driver": "Водитель",
    "Lawyer": "Юрист",
    "Accountant": "Бухгалтер",
    "Interpreter": "Переводчик",
    "Florist": "Флорист",
    "Baker": "Пекарь",
    "Chef": "Повар",
    "Tutor": "Репетитор",
    "Personal Trainer": "Фитнес-тренер",
    "Musician": "Музыкант",
    "Nanny": "Няня",
    "Housekeeper": "Домработница",
    "SMM Specialist": "SMM-специалист",
    "Copywriter": "Копирайтер",
    "Marketing Specialist": "Маркетолог",
    "Graphic Designer": "Графический дизайнер",
    "Tailor": "Портной",
    "Technician": "Техник",
    "Appliance Repair": "Ремонт бытовой техники",
    "Smartphone Repair": "Ремонт смартфонов",
    "Hypnotherapist": "Гипнотерапевт",
    "Osteopath": "Остеопат",
    "Podiatrist": "Подиатр",
    "Holistic Healing": "Холистический целитель",
    "Psychiatrist": "Психиатр",
    "Therapist": "Терапевт"
}

async def update_professions_ru():
    async with async_session() as session:
        for eng, ru in translated_professions.items():
            result = await session.execute(selectr(Profession).where(Profession.name == eng))
            obj = result.scalar()
            if obj:
                await session.execute(update(Profession).where(Profession.id == obj.id).values(name_ru=ru))
                printr(f"✅ Обновлено: {eng} → {ru}")
            else:
                printr(f"⛔ Не найдено в базе: {eng}")
        await session.commitr()
        printr("✅ Все переводы профессий обновлены.")

if __name__ == "__main__":
    asyncio.run(update_professions_ru())
