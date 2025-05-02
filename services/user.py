import logging
from sqlalchemy import select
from database.models import User
from datetime import datetime

logger = logging.getLogger("services.user")


async def get_user_by_telegram_id(session, telegram_id: int):
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar()


async def create_or_update_user(session, telegram_id: int, data: dict):
    user = await get_user_by_telegram_id(session, telegram_id)

    if user:
        logger.info(f"[USER] {telegram_id} найден. Обновляем.")
        for key, value in data.items():
            if hasattr(user, key):
                setattr(user, key, value)
        user.last_login = datetime.utcnow()
    else:
        logger.info(f"[USER] {telegram_id} не найден. Создаём нового.")
        user = User(
            telegram_id=telegram_id,
            full_name=data.get("full_name"),
            role=data.get("role"),
            created_at=datetime.utcnow(),
            last_login=datetime.utcnow(),
            rating=float(data.get("rating", 0.0)),
            profile_complete=True if data.get("profile_complete") in [True, "true", "True", 1, "1"] else False,
            is_verified=True if data.get("is_verified") in [True, "true", "True", 1, "1"] else False,
            is_blocked=True if data.get("is_blocked") in [True, "true", "True", 1, "1"] else False,
            reputation=int(data.get("reputation", 0)),
            warnings=int(data.get("warnings", 0)),
            language=data.get("language"),
            country=data.get("country")
        )
        session.add(user)

    await session.commit()


