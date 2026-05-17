from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database.session import async_session
from database.models import User
from utils.lang_manager import tr
from logger import log

router = Router()

class SpecialistReplyFSM(StatesGroup):
    waiting_for_reply = State()
    waiting_for_decline_reason = State()

@router.callback_query(F.data.startswith("accept_order:"))
async def accept_order(callback: CallbackQuery):
    log.info(f"[REPLY] Специалист {callback.from_user.id} принял заказ")
    lang = callback.from_user.language_code or "ru"
    await callback.message.edit_reply_markup()
    await callback.message.answer(tr("order_accepted", lang))

    parts = callback.data.split(":")
    if len(parts) < 2:
        return
    user_id = int(parts[1])
    try:
        await callback.bot.send_message(user_id, tr("specialist_accepted", lang))
    except Exception:
        pass

@router.callback_query(F.data.startswith("reply_user:"))
async def ask_reply_text(callback: CallbackQuery, state: FSMContext):
    log.info(f"[REPLY] Специалист {callback.from_user.id} начал писать ответ")
    lang = callback.from_user.language_code or "ru"
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.message.answer(tr("error", lang))
        return

    user_id = int(parts[1])
    await state.set_state(SpecialistReplyFSM.waiting_for_reply)
    await state.update_data(user_id=user_id)
    await callback.message.answer(tr("write_reply", lang))
    await callback.answer()

@router.message(SpecialistReplyFSM.waiting_for_reply)
async def send_reply_to_user(message: Message, state: FSMContext):
    log.info(f"[REPLY] Ответ от специалиста {message.from_user.id} отправляется")
    lang = message.from_user.language_code or "ru"
    data = await state.get_data()
    user_id = data.get("user_id")

    if not user_id:
        await message.answer(tr("error", lang))
        await state.clear()
        return

    try:
        await message.bot.send_message(user_id, message.text)
        await message.answer(tr("reply_sent", lang))
    except Exception:
        await message.answer(tr("error_cannot_send", lang))

    await state.clear()

@router.callback_query(F.data.startswith("decline_order:"))
async def ask_decline_reason(callback: CallbackQuery, state: FSMContext):
    log.info(f"[REPLY] Специалист {callback.from_user.id} отказался от заказа, просим причину")
    lang = callback.from_user.language_code or "ru"
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.message.answer(tr("error", lang))
        return

    user_id = int(parts[1])
    await state.set_state(SpecialistReplyFSM.waiting_for_decline_reason)
    await state.update_data(user_id=user_id)
    await callback.message.answer(tr("ask_decline_reason", lang))

@router.message(SpecialistReplyFSM.waiting_for_decline_reason)
async def send_decline_reason(message: Message, state: FSMContext):
    log.info(f"[REPLY] Специалист {message.from_user.id} отправляет причину отказа")
    lang = message.from_user.language_code or "ru"
    data = await state.get_data()
    user_id = data.get("user_id")

    if not user_id:
        await message.answer(tr("error_no_specialist", lang))
        await state.clear()
        return

    try:
        await message.bot.send_message(user_id, tr("specialist_declined", lang) + f"\n\n❗ {message.text}")
        await message.answer(tr("decline_sent", lang))
    except Exception:
        await message.answer(tr("error_cannot_send", lang))

    await state.clear()
