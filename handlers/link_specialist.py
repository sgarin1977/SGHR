from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, and_

from database.models import Specialist
from database.session import get_session
from utils.lang_manager import tr
from ui.buttons.menu import specialists_menu
from ui.texts import translations
from logger import log

router = Router()

class LinkSpecialistForm(StatesGroup):
    direction = State()
    profession = State()
    choose_existing = State()

PAGE_SIZE = 5

def get_lang(event):
    code = (event.from_user.language_code or "ru").split("-")[0].split("_")[0]
    return code if code in ["ru", "pt", "en"] else "ru"

@router.message(lambda msg: msg.text == "🔗 Привязать профиль")
async def start_linking(message: types.Message, state: FSMContext):
    lang = get_lang(message)
    await state.set_state(LinkSpecialistForm.direction)
    await message.answer(translations["enter_direction"][lang])

@router.message(LinkSpecialistForm.direction)
async def enter_direction(message: types.Message, state: FSMContext):
    await state.update_data(direction_name=message.text)
    await state.set_state(LinkSpecialistForm.profession)
    lang = get_lang(message)
    await message.answer(translations["enter_profession"][lang])

@router.message(LinkSpecialistForm.profession)
async def enter_profession(message: types.Message, state: FSMContext):
    await state.update_data(profession_name=message.text)
    data = await state.get_data()
    lang = get_lang(message)

    async with get_session() as session:
        results = await session.execute(
            select(Specialist).where(
                and_(
                    Specialist.direction.has(name_ru=data["direction_name"]),
                    Specialist.profession.has(name_ru=data["profession_name"]),
                    Specialist.user_id == None
                )
            )
        )
        matches = results.scalars().all()

        if not matches:
            await message.answer(translations["no_matches_found"][lang])
            await state.clear()
            return

        buttons = [
            [InlineKeyboardButton(
                text=f"{s.full_name or 'Без имени'} ({s.contacts})",
                callback_data=f"link:{s.id}"
            )] for s in matches
        ]
        buttons.append([
            InlineKeyboardButton(text="◀️ " + translations["back"][lang], callback_data="go_back")
        ])

        await state.set_state(LinkSpecialistForm.choose_existing)
        await message.answer(
            translations["choose_yourself"][lang],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

@router.callback_query(lambda c: c.data == "go_back")
async def go_back(call: CallbackQuery, state: FSMContext):
    lang = get_lang(call)
    await state.set_state(LinkSpecialistForm.direction)
    await call.message.edit_text(translations["enter_direction"][lang])

@router.callback_query(lambda c: c.data.startswith("link:"))
async def confirm_link(call: CallbackQuery, state: FSMContext):
    try:
        spec_id = int(call.data.split(":")[1])
    except ValueError:
        await call.message.answer("ID специалиста некорректен.")
        return

    user_id = call.from_user.id
    lang = get_lang(call)

    async with get_session() as session:
        specialist = await session.get(Specialist, spec_id)
        if not specialist:
            await call.message.answer(translations["no_matches_found"][lang])
            return

        if specialist.user_id:
            await call.message.answer(translations["already_linked"][lang])
            return

        specialist.user_id = user_id
        await session.commit()

    await state.clear()
    await call.message.answer(translations["link_success"][lang], reply_markup=specialist_menu(lang))

