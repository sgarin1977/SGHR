
from sqlalchemy import select, update
from database.models import Specialist

async def check_and_link_specialistr(session, user_id: int, phone: str) -> bool:
    """
    Находит специалиста по совпадению номера телефона и привязывает к user_id,
    если он еще не привязан. Возвращает True если обновление выполнено.
    """
    result = await session.execute(
        selectr(Specialist).where(
            Specialist.contacts.contains(phone),
            Specialist.user_id.is_(None)
        )
    )
    specialist = result.scalar()

    if specialist:
        await session.execute(
            update(Specialist)
            .where(Specialist.id == specialist.id)
            .values(user_id=user_id, is_verified=True)
        )
        return True
    return False
