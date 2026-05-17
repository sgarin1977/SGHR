from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.session import async_session
from database.models import ContactHistory, User
from utils.lang_manager import tr
from datetime import datetime

router = Router()

PER_PAGE = 5

@router.callback_query(F.data == "my_orders")
async def show_my_orders(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    specialist_id = callback.from_user.id

    async with async_session() as session:
        rows = (await session.execute(
            ContactHistory.__table__.select().where(ContactHistory.specialist_id == specialist_id)
        )).fetchall()

        if not rows:
            await callback.message.edit_text(tr("no_orders", lang))
            return

        text = f"<b>{tr('your_orders', lang)}</b>\n\n"
        for row in rows[-PER_PAGE:][::-1]:
            user = await session.get(User, row.user_id)
            text += f"👤 <b>{user.full_name}</b>\n🕒 {row.created_at.strftime('%d.%m.%Y %H:%M')}\n📨 {row.message}\n\n"

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=tr("back", lang), callback_data="back_to_menu")]]
    ))

