from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from datetime import datetime
from sqlalchemy import select

from database.models import User
from database.session import get_session, async_session
from services.user import get_user_by_telegram_id, create_or_update_user
from ui.buttons.menu import main_menu
from utils.translate_utils import get_lang, tr
from logger import log

router = Router()

@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    full_name = message.from_user.full_name or "Пользователь"
    user_lang = get_lang(message)
    lang = user_lang if user_lang in ["ru", "pt", "en"] else "en"

    log.info(f"[START] Получена команда /start от {user_id} ({full_name}), язык: {lang}")

    # ГАРАНТИЯ: ВСЕГДА создаём пользователя, если его нет в базе
    async with get_session() as session:
        existing_user = await session.execute(select(User).where(User.telegram_id == user_id))
        user_obj = existing_user.scalar()
        if not user_obj:
            log.info(f"[START] Новый пользователь: {user_id} ({full_name}) — добавляем в базу.")
            new_user = User(
                telegram_id=user_id,
                full_name=full_name,
                language=lang,
                last_login=datetime.utcnow()
            )
            session.add(new_user)
            await session.commit()
        else:
            # Даже если уже есть — обновляем ФИО и язык если вдруг изменились
            updated = False
            if user_obj.full_name != full_name:
                user_obj.full_name = full_name
                updated = True
            if user_obj.language != lang:
                user_obj.language = lang
                updated = True
            user_obj.last_login = datetime.utcnow()
            updated = True
            if updated:
                await session.commit()
            log.info(f"[START] Пользователь {user_id} уже существует в базе, данные обновлены.")

    # Дублирующая гарантия через сервисный слой (если он используется где-то ещё)
    async with async_session() as session:
        user = await get_user_by_telegram_id(session, user_id)
        if not user:
            log.warning(f"[START] Пользователь {user_id} не найден через get_user_by_telegram_id — создаём.")
            user_data = {
                "language": lang,
                "last_login": datetime.utcnow()
            }
            await create_or_update_user(session, user_id, user_data)
        else:
            log.info(f"[START] Обновляем дату последнего входа для {user_id}.")
            await create_or_update_user(session, user_id, {"last_login": datetime.utcnow()})

    await state.clear()
    log.info(f"[START] FSM очищен для {user_id}")

    reply_markup = main_menu(lang)
    log.info(f"[START] Отправка главного меню пользователю {user_id}")
    await message.answer(tr("choose_section", lang), reply_markup=reply_markup)

