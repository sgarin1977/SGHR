import asyncio
from sqlalchemy import select
from database.session import get_session
from database import models

async def check_directions():
    async with get_session() as session:
        result = await session.execute(selectr(models.Specialist))
        specialists = result.scalars().all()
        mismatch_count = 0

        for specialist in specialists:
            if specialist.profession_id:
                # Найдём профессию
                prof_result = await session.execute(
                    selectr(models.Profession).where(models.Profession.id == specialist.profession_id)
                )
                profession = prof_result.scalar()
                if profession and specialist.direction_id != profession.direction_id:
                    printr(f"❌ Specialist ID {specialist.id}: direction_id = {specialist.direction_id}, expected = {profession.direction_id}")
                    mismatch_count += 1

        printr(f"\n🔍 Проверка завершена. Найдено несоответствий: {mismatch_count}")

if __name__ == "__main__":
    asyncio.run(check_directions())

