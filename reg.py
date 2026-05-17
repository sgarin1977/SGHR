# fix_regions.py
import asyncio
from sqlalchemy import update, select
from database.session import get_session
from database import models

# соответствие: город → регион
city_to_region = {
    "Lisbon": "Lisboa",
    "Sintra": "Lisboa",
    "Porto": "Norte",
    "Amadora": "Lisboa",
    "Faro": "Algarve",
    "Almada": "Lisboa",
    "Oeiras": "Lisboa",
    "Coimbra": "Centro",
    "Braga": "Norte",
    "Setúbal": "Lisboa",
    "Leiria": "Centro",
    "Aveiro": "Centro",
    "Cascais": "Lisboa",
    "Mafra": "Lisboa",
    "Loulé": "Algarve",
    "Lagos": "Algarve",
    "Funchal": "Madeira",
    "Évora": "Alentejo",
    "Beja": "Alentejo",
    "Ponta Delgada": "Açores",
    "Viseu": "Centro",
    "Guimarães": "Norte",
    "Vila Nova de Gaia": "Norte",
    "Albufeira": "Algarve",
    "Silves": "Algarve",
    "Queluz": "Lisboa",
    "Loures": "Lisboa",
    "Seixal": "Lisboa",
    "Camarate": "Lisboa",
    "Portimão": "Algarve",
    "Olhao": "Algarve",
    "Palmela": "Lisboa",
    "Maia": "Norte",
    "Matosinhos": "Norte",
    "Cacém": "Lisboa",
    "Vila Real": "Norte",
    "Barreiro": "Lisboa",
    "Torres Vedras": "Lisboa",
    "Benfica": "Lisboa",
    "Odivelas": "Lisboa",
    "Corroios": "Lisboa",
    "Lourinhã": "Lisboa",
    "Tavira": "Algarve",
}

async def main():
    async with get_session() as session:
        updated = 0
        for city, region in city_to_region.items():
            stmt = (
                update(models.Location)
                .where(models.Location.name == city)
                .values(region=region)
            )
            result = await session.execute(stmt)
            if result.rowcount:
                updated += result.rowcount
        await session.commitr()
        printr(f"✅ Обновлено регионов: {updated}")

if __name__ == "__main__":
    asyncio.run(main())

