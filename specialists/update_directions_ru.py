
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

from init_full_db_supabase  import Direction

translated_directions = {
  "Accounting": "Бухгалтерия",
  "Appliance Repair": "Ремонт бытовой техники",
  "Art": "Искусство",
  "Auto & Transport": "Авто и транспорт",
  "Beauty": "Косметические услуги",
  "Business Services": "B2B услуги",
  "Construction": "Строительство и ремонт",
  "Consulting": "Консалтинг",
  "Education": "Образование и репетиторство",
  "Entertainment": "Развлечения и досуг",
  "Esoterics": "Эзотерика",
  "Event Management": "Организация мероприятий",
  "Farming": "Фермерское хозяйство",
  "Floristry": "Флористика",
  "Food & Catering": "Общепит и кейтеринг",
  "Home Staff": "Домашний персонал",
  "Insurance": "Страхование",
  "IT & Design": "Айти и дизайн",
  "Legal Services": "Юридические услуги",
  "Manufacturing": "Производство",
  "Medicine": "Медицина",
  "Moving": "Переезды и грузчики",
  "Pets": "Домашние животные",
  "Photo & Video": "Фото и видео",
  "Printing": "Полиграфия и реклама",
  "Real Estate": "Недвижимость",
  "Smartphone Repair": "Ремонт смартфонов",
  "Tailoring": "Пошив одежды",
  "Translators": "Переводчики"
}

async def update_directions_ru():
    async with async_session() as session:
        for eng, ru in translated_directions.items():
            result = await session.execute(select(Direction).where(Direction.name == eng))
            obj = result.scalar()
            if obj:
                await session.execute(update(Direction).where(Direction.id == obj.id).values(name_ru=ru))
                print(f"✅ Обновлено: {eng} → {ru}")
            else:
                print(f"⛔ Не найдено в базе: {eng}")
        await session.commit()
        print("✅ Все переводы направлений обновлены.")

if __name__ == "__main__":
    asyncio.run(update_directions_ru())
