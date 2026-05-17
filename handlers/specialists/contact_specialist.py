from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from utils.lang_manager import tr
from database.session import async_session
from database.models import Specialist, User, ContactHistory
from services.translator import translate as auto_translate

from aiogram import Bot
from logger import log

router = Router()

class ContactFSM(StatesGroup):
    waiting_for_message = State()

@router.callback_query(F.data.startswith("contact_specialist:"))
async def start_contact_specialist(callback: CallbackQuery, state: FSMContext):
    log.info(f"[CONTACT] Пользователь {callback.from_user.id} нажал 'Связаться'")
    lang = callback.from_user.language_code or "ru"
    parts = callback.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await callback.message.answer(tr("invalid_specialist_id", lang))
        return

    specialist_id = int(parts[1])
    await state.set_state(ContactFSM.waiting_for_message)
    await state.update_data(specialist_id=specialist_id)

    await callback.message.answer(
        tr("ask_contact_message", lang),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=tr("cancel", lang), callback_data="cancel")]]
        )
    )

@router.message(ContactFSM.waiting_for_message)
async def receive_contact_message(message: Message, state: FSMContext, bot: Bot):
    log.info(f"[CONTACT] Получено сообщение от пользователя {message.from_user.id}")
    lang = message.from_user.language_code or "ru"
    data = await state.get_data()
    specialist_id = data.get("specialist_id")

    if not specialist_id:
        log.warning("[CONTACT] Не передан specialist_id")
        await message.answer(tr("error_no_specialist", lang))
        return

    user_name = message.from_user.full_name or "Пользователь"
    user_id = message.from_user.id
    original_text = message.text or "(пустое сообщение)"

    async with async_session() as session:
        specialist = await session.get(Specialist, specialist_id)
        if not specialist:
            await message.answer(tr("specialist_not_found", lang))
            return

        specialist_user = await session.get(User, specialist.user_id)
        if not specialist_user:
            await message.answer(tr("specialist_not_found", lang))
            return

        translated_text = await auto_translate(original_text, specialist_user.language or "ru")

        text_to_send = (
            f"\U0001F4E9 <b>{user_name}</b> хочет связаться с вами:\n"
            f'\"{translated_text}\"'
        )

        try:
            await bot.send_message(
                specialist_user.telegram_id,
                text_to_send,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text=tr("accept_order", lang), callback_data="accept_order"),
                        InlineKeyboardButton(text=tr("reply", lang), callback_data=f"reply_user:{user_id}"),
                        InlineKeyboardButton(text=tr("decline_order", lang), callback_data=f"decline_order:{user_id}")
                    ]]
                )
            )
        except Exception as e:
            log.error(f"[CONTACT] Ошибка при отправке специалисту: {e}")
            await message.answer(tr("error_cannot_send", lang))
            return

        contact = ContactHistory(user_id=user_id, specialist_id=specialist_id, message=original_text)
        session.add(contact)
        await session.commit()

    await message.answer(tr("message_sent_to_specialist", lang))
    await state.clear()

